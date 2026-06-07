"""
WatchRec 电脑端统一服务

一个 uvicorn 进程，三件事并发：
  1. 局域网接收口（FastAPI 路由，0.0.0.0:8765，事件循环）
  2. VPS 轮询（后台线程，每 30s）
  3. LAN IP 上报（后台线程，每 2min）
  4. 统一转写 worker（后台线程，单队列+批处理）

模型在 worker 线程内首次使用时加载，不阻塞 /health 响应。
poller / IP 上报用独立线程（不是 asyncio task），阻塞式 HTTP 不影响事件循环。
单 worker 约束：LAN 缓存在内存中，uvicorn 不要加 --workers。
"""

import json
import shutil
import socket
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from config import (
    APP_TOKEN, IP_REPORT_INTERVAL_SEC, LAN_IP_OVERRIDE,
    LOCAL_DATA_DIR, POLL_INTERVAL_SEC, TIMEZONE,
)
from worker import TranscribeWorker
from vps_client import VPSClient

tz = ZoneInfo(TIMEZONE)

# 全局共享
_worker = TranscribeWorker()
_vps = VPSClient()
_poller_stop = threading.Event()
_ip_stop = threading.Event()

import re
_FILENAME_RE = re.compile(r"^recording_(\d+)_(.+)\.m4a$")


# ── 文件名解析 & 归档（和 VPS 同规则）────────────────────

def parse_and_rename(raw_name: str) -> tuple[str, str]:
    """返回 (新文件名, 日期子目录)。"""
    m = _FILENAME_RE.match(raw_name)
    if m:
        ts_ms = int(m.group(1))
        dur_ms_str = m.group(2)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    else:
        dt = datetime.now(tz=tz)
        dur_ms_str = "unknown"
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{time_str}_{dur_ms_str}.m4a", date_str


def find_local_json(data_dir: str, file_id: str) -> Path | None:
    """查找本地已有的转写 JSON（与音频同名，.m4a → .json）。"""
    audio_path = Path(data_dir) / file_id
    json_path = audio_path.with_suffix(".json")
    return json_path if json_path.exists() else None


# ── LAN IP 探测 ────────────────────────────────────────────

def detect_lan_ip() -> str:
    """取默认出口 IP。Clash TUN 下可能返回 198.18.x.x（假 IP），手表回退走 VPS，无害。"""
    if LAN_IP_OVERRIDE:
        return LAN_IP_OVERRIDE
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── 后台线程：VPS 轮询 ────────────────────────────────────

def _poller_loop():
    """每 POLL_INTERVAL_SEC 秒轮询一次 VPS /pending。"""
    data_dir = LOCAL_DATA_DIR
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    while not _poller_stop.is_set():
        try:
            _poll_once(data_dir)
        except Exception as e:
            print(f"  ✗ poller: {e}")
        _poller_stop.wait(POLL_INTERVAL_SEC)


def _poll_once(data_dir: str):
    pending = _vps.get_pending()
    if not pending:
        return

    print(f"  📋 VPS 待处理: {len(pending)} 条")
    to_download: list[str] = []

    for item in pending:
        file_id = item["id"]

        # 本地已有转写 JSON → 幂等回报（自愈之前失败的回报）
        local_json = find_local_json(data_dir, file_id)
        if local_json:
            try:
                data = json.loads(local_json.read_text(encoding="utf-8"))
                _vps.post_result(file_id, data.get("transcript", ""), data.get("raw", ""), data.get("language", ""))
                print(f"  🔄 已回报: {file_id}")
            except Exception as e:
                print(f"  ✗ 回报失败: {file_id} — {e}")
            continue

        # 本地已有音频（在队列里等转写或正在转写）→ 跳过
        local_audio = Path(data_dir) / file_id
        if local_audio.exists():
            print(f"  ⏳ 等待转写: {file_id}")
            continue

        # 都没有 → 需要下载
        to_download.append(file_id)

    # 下载 + 提交转写
    for file_id in to_download:
        try:
            local_path = _vps.download(file_id, data_dir)
            print(f"  ⬇ 已下载: {file_id}")
            _worker.submit(local_path)
        except Exception as e:
            print(f"  ✗ 下载失败: {file_id} — {e}")


# ── 后台线程：LAN IP 上报 ─────────────────────────────────

def _ip_reporter_loop():
    """每 IP_REPORT_INTERVAL_SEC 秒上报一次局域网 IP 给 VPS。"""
    while not _ip_stop.is_set():
        try:
            ip = detect_lan_ip()
            _vps.report_lan_info(ip, 8765)
        except Exception as e:
            print(f"  ✗ IP 上报失败: {e}")
        _ip_stop.wait(IP_REPORT_INTERVAL_SEC)


# ── FastAPI 应用 ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(LOCAL_DATA_DIR).mkdir(parents=True, exist_ok=True)

    # 启动后台线程（不阻塞事件循环）
    _worker.start()
    threading.Thread(target=_poller_loop, daemon=True, name="poller").start()
    threading.Thread(target=_ip_reporter_loop, daemon=True, name="ip-reporter").start()

    # 立即上报一次 IP
    try:
        ip = detect_lan_ip()
        _vps.report_lan_info(ip, 8765)
        print(f"  ✓ LAN IP 已上报: {ip}:8765")
    except Exception as e:
        print(f"  ⚠ 首次 IP 上报失败: {e}")

    print()
    print(f"  LAN 接收口: http://0.0.0.0:8765")
    print(f"  VPS 轮询:   每 {POLL_INTERVAL_SEC}s")
    print(f"  数据目录:   {LOCAL_DATA_DIR}")
    print()

    yield

    # 关闭：通知 VPS 下线 → 停线程
    print("  ⏹ 正在关闭...")
    _poller_stop.set()
    _ip_stop.set()
    _vps.clear_lan_info()
    _worker.stop()


app = FastAPI(title="WatchRec PC", lifespan=lifespan)


# ── 鉴权 ──────────────────────────────────────────────────

async def verify_token(request: Request):
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {APP_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── 局域网路由（手表用）──────────────────────────────────

@app.get("/health")
async def health(_=Depends(verify_token)):
    return {"status": "alive"}


@app.post("/upload")
async def upload(file: UploadFile = File(...), _=Depends(verify_token)):
    """局域网直传：存盘 + 提交转写队列（不回报 VPS）。"""
    raw_name = file.filename or "unnamed.m4a"
    new_name, date_dir = parse_and_rename(raw_name)

    dest_dir = Path(LOCAL_DATA_DIR) / date_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / new_name
    rel_path = f"{date_dir}/{new_name}"

    # 流式写盘
    with open(dest, "wb") as out_f:
        shutil.copyfileobj(file.file, out_f, length=16384)

    print(f"  ✓ LAN 已保存: {rel_path}  ({dest.stat().st_size / 1024:.1f} KB)")

    # 提交统一转写队列（转写完只存本地 JSON，不回报 VPS）
    _worker.submit(str(dest))

    return JSONResponse({"status": "ok", "id": rel_path})


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    if not APP_TOKEN:
        print("ERROR: APP_TOKEN not set. Check .env or environment variable.")
        exit(1)

    # 单 worker：LAN 缓存在内存中
    uvicorn.run(app, host="0.0.0.0", port=8765)
