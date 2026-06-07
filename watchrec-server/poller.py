"""
WatchRec 电脑端 — VPS 拉取 + 本地转写 + 回报结果。

启动即开始轮询 VPS /pending，Ctrl+C 干净退出。

去重策略（以 VPS 为准 + 幂等回报）：
- /pending 返回的每个 id 都要处理；
- 本地已有 JSON → 不重新下载/转写，直接重新 POST /result（自愈回报失败）；
- 本地没有 → 下载 → 转写 → 存 JSON → POST /result。
- 只要 VPS 还认为某条未转写，电脑就持续尝试回报，直到 VPS 收到。
"""

import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import LOCAL_DATA_DIR, POLL_INTERVAL_SEC, VPS_BASE_URL

_tz = ZoneInfo("Asia/Shanghai")
_running = True


def _handle_signal(sig, frame):
    global _running
    print("\n  ⏹ 正在退出...")
    _running = False


def find_local_json(data_dir: str, file_id: str) -> Path | None:
    """查找本地已有的转写 JSON 边车文件。"""
    json_path = Path(data_dir) / (Path(file_id).stem + ".json")
    # 也检查同目录下的 .json（与音频同名去 .m4a 加 .json）
    audio_path = Path(data_dir) / file_id
    sibling_json = audio_path.with_suffix(".json")
    if sibling_json.exists():
        return sibling_json
    if json_path.exists():
        return json_path
    return None


def report_from_local_json(client, file_id: str, json_path: Path) -> bool:
    """从本地 JSON 提取结果回报给 VPS，成功返回 True。"""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        client.post_result(
            file_id=file_id,
            transcript=data.get("transcript", ""),
            raw=data.get("raw", ""),
            language=data.get("language", ""),
        )
        return True
    except Exception as e:
        print(f"  ✗ 回报失败: {file_id} — {e}")
        return False


def poll_once(client, data_dir: str) -> tuple[int, int, int]:
    """
    执行一次轮询。

    Returns:
        (downloaded, transcribed, reported) 计数
    """
    from transcriber import ensure_model_loaded, transcribe_files, write_sidecar

    # 1. 拉取待转写列表
    pending = client.get_pending()
    if not pending:
        return 0, 0, 0

    print(f"  📋 待处理: {len(pending)} 条")
    downloaded = 0
    transcribed = 0
    reported = 0

    # 2. 逐条处理（以 /pending 为准）
    to_transcode: list[str] = []  # 需要转写的本地路径
    id_map: dict[str, str] = {}   # local_path → file_id

    for item in pending:
        file_id = item["id"]

        # 检查本地是否已有转写结果
        local_json = find_local_json(data_dir, file_id)

        if local_json:
            # 已有本地 JSON → 不重新下载/转写，直接重新回报（幂等自愈）
            print(f"  🔄 幂等回报: {file_id}")
            if report_from_local_json(client, file_id, local_json):
                reported += 1
            continue

        # 本地没有 → 下载
        try:
            local_path = client.download(file_id, data_dir)
            print(f"  ⬇ 已下载: {file_id} → {local_path}")
            downloaded += 1
            to_transcode.append(local_path)
            id_map[local_path] = file_id
        except Exception as e:
            print(f"  ✗ 下载失败: {file_id} — {e}")

    # 3. 批量转写
    if to_transcode:
        print(f"  🎙️  转写 {len(to_transcode)} 个文件...")
        t0 = time.monotonic()
        try:
            results = transcribe_files(to_transcode)
        except Exception as e:
            print(f"  ✗ 转写失败: {e}")
            results = [None] * len(to_transcode)

        elapsed = time.monotonic() - t0
        print(f"  ✓ 转写完成: {len([r for r in results if r])} 个, {elapsed:.1f}s")

        # 4. 存本地 JSON + 回报 VPS
        for local_path, result in zip(to_transcode, results):
            file_id = id_map[local_path]
            if result is None:
                continue

            # 写本地边车 JSON
            write_sidecar(local_path, result)
            transcribed += 1

            # 回报 VPS
            if report_from_local_json(client, file_id, Path(local_path).with_suffix(".json")):
                reported += 1
                preview = result["transcript"][:40]
                suffix = "..." if len(result["transcript"]) > 40 else ""
                print(f"    ✓ {Path(file_id).name} → \"{preview}{suffix}\"")

    return downloaded, transcribed, reported


def main():
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 延迟导入（避免未安装依赖时 config 阶段就崩）
    from config import APP_TOKEN, CA_CERT
    from vps_client import VPSClient

    if not APP_TOKEN:
        print("ERROR: APP_TOKEN not set. Check .env or environment variable.")
        sys.exit(1)

    data_dir = LOCAL_DATA_DIR
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    client = VPSClient()

    print()
    print(f"  VPS:      {VPS_BASE_URL}")
    print(f"  CA cert:  {CA_CERT}")
    print(f"  数据目录: {data_dir}")
    print(f"  轮询间隔: {POLL_INTERVAL_SEC}s")
    print()

    # 预加载模型（避免第一批音频卡在下载/初始化）
    from transcriber import ensure_model_loaded
    print("  ⏳ 预加载 SenseVoice-Small 模型...")
    ensure_model_loaded()
    print()

    print("  ▶ 开始轮询 (Ctrl+C 退出)")
    total_downloaded = 0
    total_transcribed = 0
    total_reported = 0

    while _running:
        try:
            d, t, r = poll_once(client, data_dir)
            total_downloaded += d
            total_transcribed += t
            total_reported += r
        except Exception as e:
            print(f"  ✗ 轮询异常: {e}")

        # 等待下一轮（可被信号中断）
        for _ in range(POLL_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)

    print(f"\n  统计: 下载 {total_downloaded}, 转写 {total_transcribed}, 回报 {total_reported}")
    print("  已退出。")


if __name__ == "__main__":
    main()
