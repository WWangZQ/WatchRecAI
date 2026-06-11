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
import sys
import threading
import time
from contextlib import asynccontextmanager

# Windows 中文控制台默认 GBK，emoji 日志会 UnicodeEncodeError；尽力切到 UTF-8。
# 桌面模式下 stdout 已被 desktop.py 换成无 reconfigure 的 Tee，hasattr 守卫即可跳过。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from config import (
    APP_TOKEN, IP_REPORT_INTERVAL_SEC, LAN_IP_OVERRIDE,
    LOCAL_DATA_DIR, POLL_INTERVAL_SEC, TIMEZONE,
)
from worker import TranscribeWorker
from vps_client import VPSClient
from runtime_state import get_logs, get_state, set_state
from settings import get_llm, save_llm

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

import ipaddress

# 需要排除的虚拟网卡名称关键词（不区分大小写）
_VIRTUAL_KEYWORDS = [
    "meta", "clash", "tailscale", "vmware", "virtualbox",
    "hyper-v", "vethernet", "wsl", "loopback", "npcap",
]
# 优先匹配的物理网卡关键词
_PHYSICAL_KEYWORDS = ["wi-fi", "wifi", "wlan", "以太网", "ethernet", "eth"]


def _is_private_rfc1918(ip_str: str) -> bool:
    """判断是否是 RFC1918 私有地址，同时排除 Clash TUN (198.18/15)、链路本地 (169.254/16)。"""
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return False
    return addr in ipaddress.IPv4Network("10.0.0.0/8") \
        or addr in ipaddress.IPv4Network("172.16.0.0/12") \
        or addr in ipaddress.IPv4Network("192.168.0.0/16")


def _is_virtual_interface(name: str) -> bool:
    lower = name.lower()
    return any(kw in lower for kw in _VIRTUAL_KEYWORDS)


def _interface_priority(name: str) -> int:
    """物理网卡优先（返回 0），虚拟/未知排后（返回 1）。"""
    lower = name.lower()
    return 0 if any(kw in lower for kw in _PHYSICAL_KEYWORDS) else 1


def _detect_via_psutil() -> str | None:
    """用 psutil 枚举网卡，挑出真实局域网 IP。"""
    try:
        import psutil
    except ImportError:
        return None

    candidates: list[tuple[int, str]] = []  # (priority, ip)

    for iface_name, addrs in psutil.net_if_addrs().items():
        if _is_virtual_interface(iface_name):
            continue
        priority = _interface_priority(iface_name)
        for addr in addrs:
            if addr.family.name != "AF_INET":
                continue
            ip_str = addr.address
            if ip_str.startswith("127."):
                continue
            if _is_private_rfc1918(ip_str):
                candidates.append((priority, ip_str))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    return chosen


def _detect_via_udp() -> str:
    """兜底：UDP connect 方法（Clash TUN 下可能返回 198.18.x.x）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def detect_lan_ip() -> str:
    """
    检测本机局域网 IP。
    优先级：LAN_IP_OVERRIDE → psutil 枚举（排除 TUN/虚拟网卡）→ UDP connect 兜底。
    """
    if LAN_IP_OVERRIDE:
        return LAN_IP_OVERRIDE
    return _detect_via_psutil() or _detect_via_udp()


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
    set_state(last_poll_at=time.time(), pending=len(pending))
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

        # 本地已有音频但还没结果：在队列里就等；否则（上次失败/中断）重新入队
        local_audio = Path(data_dir) / file_id
        if local_audio.exists():
            if _worker.is_queued(str(local_audio)):
                print(f"  ⏳ 等待转写: {file_id}")
            else:
                print(f"  ↻ 重新排队转写: {file_id}")
                _worker.submit(str(local_audio))
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
        set_state(lan_ip=ip)
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


# ── 查看界面路由（浏览器用，无鉴权）──────────────────────

@app.get("/")
async def viewer_index():
    html_path = Path(__file__).parent / "app" / "viewer.html"
    if not html_path.exists():
        raise HTTPException(404, "viewer.html not found")
    return FileResponse(str(html_path), media_type="text/html")


@app.get("/api/status")
async def api_status():
    """服务运行状态（供桌面窗口状态栏展示）。"""
    return get_state()


@app.get("/api/logs")
async def api_logs(since: int = Query(0)):
    """增量拉取服务日志（供桌面窗口日志面板展示）。"""
    return get_logs(since)


@app.get("/api/recordings")
async def api_recordings():
    """列出所有录音，按录制时间倒序。"""
    data_dir = Path(LOCAL_DATA_DIR)
    results = []

    for json_path in sorted(data_dir.rglob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # 用边车里记的真实音频文件名（支持 .m4a 以外的手动上传格式）
        parent = json_path.parent.relative_to(data_dir).as_posix()
        audio_file = data.get("audio_file") or (json_path.stem + ".m4a")
        audio_id = audio_file if parent in ("", ".") else f"{parent}/{audio_file}"

        # 侧栏副标题：优先用 AI 短标题（像 Claude chat 的对话名）；没有再回退内容预览
        headline = (data.get("headline") or "").strip()
        if headline:
            snippet = headline
        else:
            text = (data.get("summary") or data.get("full_text") or data.get("transcript") or "").strip().replace("\n", " ")
            snippet = (text[:80] + "...") if len(text) > 80 else text

        results.append({
            "id": audio_id,
            "title": data.get("title"),
            "date": data.get("recorded_at", "")[:10],
            "time": data.get("recorded_at", "")[11:19] if len(data.get("recorded_at", "")) > 11 else "",
            "duration_sec": data.get("duration_sec"),
            "language": data.get("language"),
            "snippet": snippet if snippet else "（无转写）",
            "has_summary": bool(data.get("summary")),
        })

    # 按 recorded_at 倒序
    results.sort(key=lambda x: x.get("date", "") + x.get("time", ""), reverse=True)
    return results


def _safe_resolve(data_dir: Path, id: str, suffix: str) -> Path:
    """
    从 id 安全拼出文件路径，校验仍在 data_dir 内（防目录穿越）。
    id 格式：2026-06-08/2026-06-08_15-18-27_14898.m4a
    suffix：".json"（替换扩展名，音频格式无关）
    """
    # 统一用正斜杠，再替换扩展名
    base_id = id.replace("\\", "/").rsplit(".", 1)[0]
    target = (data_dir / (base_id + suffix)).resolve()
    if not str(target).startswith(str(data_dir.resolve())):
        raise HTTPException(403, "Path traversal")
    return target


def _safe_path(data_dir: Path, id: str) -> Path:
    """按 id 原样（保留真实扩展名）解析路径，防目录穿越。"""
    target = (data_dir / id.replace("\\", "/")).resolve()
    if not str(target).startswith(str(data_dir.resolve())):
        raise HTTPException(403, "Path traversal")
    return target


_AUDIO_MIME = {
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm",
}
_ALLOWED_AUDIO = set(_AUDIO_MIME)


def _audio_mime(path: Path) -> str:
    return _AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")


@app.post("/api/upload")
async def api_manual_upload(file: UploadFile = File(...)):
    """本地查看页手动上传音频：存盘 + 进转写/AI 队列（无需 token）。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_AUDIO:
        raise HTTPException(400, f"不支持的音频格式：{ext or '无扩展名'}")

    now = datetime.now(tz)
    date_dir = now.strftime("%Y-%m-%d")
    stem = now.strftime("%Y-%m-%d_%H-%M-%S") + "_manual"
    dest_dir = Path(LOCAL_DATA_DIR) / date_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (stem + ext)
    n = 1
    while dest.exists():
        dest = dest_dir / f"{stem}-{n}{ext}"
        n += 1

    with open(dest, "wb") as out_f:
        shutil.copyfileobj(file.file, out_f, length=16384)

    rel_path = f"{date_dir}/{dest.name}"
    print(f"  ⬆ 手动上传: {rel_path}  ({dest.stat().st_size / 1024:.1f} KB)")
    _worker.submit(str(dest))
    return JSONResponse({"status": "ok", "id": rel_path})


@app.get("/api/recording")
async def api_recording_detail(id: str = Query(...)):
    """读取单条录音的完整 JSON。"""
    data_dir = Path(LOCAL_DATA_DIR)
    json_path = _safe_resolve(data_dir, id, ".json")
    if not json_path.exists():
        raise HTTPException(404, f"Recording not found: {id}")
    return json.loads(json_path.read_text(encoding="utf-8"))


# ── AI 设置（前端可填写并持久化）────────────────────────

def _settings_public(c: dict) -> dict:
    """对外不回传 api_key 明文，只给是否已设置。"""
    return {
        "llm_base_url": c.get("llm_base_url", ""),
        "llm_model": c.get("llm_model", ""),
        "api_key_set": bool(c.get("llm_api_key")),
    }


@app.get("/api/settings")
async def api_get_settings():
    return _settings_public(get_llm())


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    c = save_llm(
        body.get("llm_base_url", ""),
        body.get("llm_api_key", ""),   # 空 = 保留已存的
        body.get("llm_model", ""),
    )
    return _settings_public(c)


# 注意：同步 def → FastAPI 在线程池跑，LLM 网络阻塞不卡事件循环
@app.post("/api/enrich")
def api_enrich(id: str = Query(...)):
    """对单条录音重新生成「全文」（AI 去噪）和「AI 总结」，回写边车。"""
    from llm import is_configured, enrich

    if not is_configured():
        raise HTTPException(400, "LLM 未配置：请在 .env 填 LLM_BASE_URL / LLM_API_KEY")

    data_dir = Path(LOCAL_DATA_DIR)
    json_path = _safe_resolve(data_dir, id, ".json")
    if not json_path.exists():
        raise HTTPException(404, f"Recording not found: {id}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    transcript = data.get("transcript") or ""
    if not transcript.strip():
        raise HTTPException(400, "该录音没有原文，无法生成")

    try:
        full, summary, head = enrich(transcript)
    except Exception as e:
        raise HTTPException(502, f"AI 调用失败：{e}")

    data["full_text"] = full
    data["summary"] = summary
    data["headline"] = head
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "full_text": full, "summary": summary, "headline": head}


@app.post("/api/summarize")
def api_summarize(id: str = Query(...)):
    """重新生成「AI 总结」和「短标题」（基于现有全文，没有全文则用原文），回写边车。"""
    from llm import headline, is_configured, summarize

    if not is_configured():
        raise HTTPException(400, "LLM 未配置：请在 .env 填 LLM_BASE_URL / LLM_API_KEY")

    data_dir = Path(LOCAL_DATA_DIR)
    json_path = _safe_resolve(data_dir, id, ".json")
    if not json_path.exists():
        raise HTTPException(404, f"Recording not found: {id}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    text = (data.get("full_text") or data.get("transcript") or "").strip()
    if not text:
        raise HTTPException(400, "没有可总结的文本")

    try:
        summary = summarize(text)
        head = headline(summary or text)
    except Exception as e:
        raise HTTPException(502, f"AI 调用失败：{e}")

    data["summary"] = summary
    data["headline"] = head
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "summary": summary, "headline": head}


@app.post("/api/transcript")
async def api_update_transcript(id: str = Query(...), request: Request = ...):
    """保存人工修改后的原文（修正敏感词/错字），回写边车。全文/总结不动，由用户自行重新生成。"""
    data_dir = Path(LOCAL_DATA_DIR)
    json_path = _safe_resolve(data_dir, id, ".json")
    if not json_path.exists():
        raise HTTPException(404, f"Recording not found: {id}")

    body = await request.json()
    transcript = body.get("transcript")
    if not isinstance(transcript, str):
        raise HTTPException(400, "transcript 字段缺失")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["transcript"] = transcript.strip()
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok"}


@app.post("/api/rename")
async def api_rename(id: str = Query(...), request: Request = ...):
    """给录音设置/清除自定义标题（写入边车 title 字段）。"""
    data_dir = Path(LOCAL_DATA_DIR)
    json_path = _safe_resolve(data_dir, id, ".json")
    if not json_path.exists():
        raise HTTPException(404, f"Recording not found: {id}")

    body = await request.json()
    title = (body.get("title") or "").strip()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["title"] = title or None
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "title": data["title"]}


@app.post("/api/delete")
def api_delete(id: str = Query(...)):
    """删除一条录音的音频与边车 JSON。"""
    data_dir = Path(LOCAL_DATA_DIR)
    audio_path = _safe_path(data_dir, id)
    json_path = _safe_resolve(data_dir, id, ".json")

    removed = []
    for p in (audio_path, json_path):
        if p.exists():
            p.unlink()
            removed.append(p.name)
    if not removed:
        raise HTTPException(404, f"Recording not found: {id}")

    print(f"  🗑 已删除: {id}")
    return {"status": "ok", "removed": removed}


@app.get("/api/audio")
async def api_audio(id: str = Query(...), request: Request = ...):
    """流式返回音频文件，支持 HTTP Range（206 Partial Content）。"""
    data_dir = Path(LOCAL_DATA_DIR)
    audio_path = _safe_path(data_dir, id)
    if not audio_path.exists():
        raise HTTPException(404, f"Audio not found: {id}")

    mime = _audio_mime(audio_path)
    file_size = audio_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        start, end = _parse_range(range_header, file_size)
        if start >= file_size:
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{file_size}"
            })

        content_length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
            "Accept-Ranges": "bytes",
            "Content-Type": mime,
        }

        def file_chunker():
            with open(audio_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(file_chunker(), status_code=206, headers=headers)
    else:
        return FileResponse(
            str(audio_path),
            media_type=mime,
            headers={"Accept-Ranges": "bytes"},
        )


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    """
    解析 Range 头，支持完整和开放式区间。
    "bytes=1000-2000" → (1000, 2000)
    "bytes=1000-"     → (1000, file_size-1)
    """
    range_val = range_header.replace("bytes=", "").strip()
    parts = range_val.split("-")
    start = int(parts[0]) if parts[0] else 0
    end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
    return start, min(end, file_size - 1)


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    if not APP_TOKEN:
        print("ERROR: APP_TOKEN not set. Check .env or environment variable.")
        exit(1)

    # 单 worker：LAN 缓存在内存中
    uvicorn.run(app, host="0.0.0.0", port=8765)
