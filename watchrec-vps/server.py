"""
WatchRec VPS 接收服务

手表上传音频到此服务，电脑主动来拉取转写并回报结果。
不做转写，只做中转缓冲。

启动：APP_TOKEN=xxx python server.py（或通过 systemd / start.sh）
单 worker 约束：uvicorn 只跑 1 个 worker，LAN 缓存在内存中，多 worker 会不共享。
"""

import asyncio
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from config import APP_TOKEN, LAN_TTL_SECONDS, PORT, RETENTION_DAYS, TIMEZONE

tz = ZoneInfo(TIMEZONE)
upload_dir = Path(__file__).parent / "uploads"
upload_dir.mkdir(exist_ok=True)

FILENAME_RE = re.compile(r"^recording_(\d+)_(.+)\.m4a$")

# ── LAN 缓存（内存，重启丢失，可接受）──────────────────────

_lan_info: dict = {}
_lan_lock = asyncio.Lock()


# ── 清理 ─────────────────────────────────────────────────────

def _cleanup_expired():
    """删除 status==transcribed 且 uploaded_at > RETENTION_DAYS 天前的文件和 meta。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    deleted = 0

    for meta_path in upload_dir.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if meta.get("status") != "transcribed":
            continue

        uploaded_at_str = meta.get("uploaded_at")
        if not uploaded_at_str:
            continue

        try:
            uploaded_at = datetime.fromisoformat(uploaded_at_str)
        except ValueError:
            continue

        if uploaded_at > cutoff:
            continue

        audio_path = meta_path.with_suffix("")  # strip .json → .m4a
        if audio_path.exists():
            audio_path.unlink()
        meta_path.unlink()
        deleted += 1

    if deleted:
        print(f"  🗑  Cleanup: deleted {deleted} expired transcribed file(s)")


async def _cleanup_loop():
    """每小时跑一次清理。"""
    while True:
        await asyncio.sleep(3600)
        _cleanup_expired()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("  ⏳ Starting cleanup scan...")
    _cleanup_expired()
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


# ── 鉴权 ─────────────────────────────────────────────────────

async def verify_token(request: Request):
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {APP_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── 文件名解析 & 归档（复用现有逻辑）────────────────────────

def parse_and_rename(raw_name: str) -> tuple[str, str, float]:
    """返回 (新文件名, 日期子目录, duration_sec)。"""
    m = FILENAME_RE.match(raw_name)
    if m:
        ts_ms = int(m.group(1))
        dur_ms_str = m.group(2)
        dur_sec = round(int(dur_ms_str) / 1000.0, 2) if dur_ms_str.isdigit() else 0.0
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    else:
        dt = datetime.now(tz=tz)
        dur_ms_str = "unknown"
        dur_sec = 0.0

    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    new_name = f"{time_str}_{dur_ms_str}.m4a"
    return new_name, date_str, dur_sec


def write_meta(audio_path: Path, rel_path: str, duration_sec: float, size_bytes: int):
    """创建 .meta.json 边车文件。"""
    meta = {
        "status": "uploaded",
        "rel_path": rel_path,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": duration_sec,
        "size_bytes": size_bytes,
        "transcribed_at": None,
        "transcript": None,
        "raw": None,
        "language": None,
    }
    meta_path = Path(str(audio_path) + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def read_meta(rel_path: str) -> dict:
    """读取指定音频的 meta.json。"""
    audio_path = upload_dir / rel_path
    meta_path = Path(str(audio_path) + ".meta.json")
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Meta not found: {rel_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def update_meta(rel_path: str, updates: dict):
    """更新 meta.json 的指定字段。"""
    audio_path = upload_dir / rel_path
    meta_path = Path(str(audio_path) + ".meta.json")
    meta = read_meta(rel_path)
    meta.update(updates)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ── App ──────────────────────────────────────────────────────

app = FastAPI(title="WatchRec VPS", lifespan=lifespan)


# ── 手表接口 ─────────────────────────────────────────────────

@app.get("/health")
async def health(_=Depends(verify_token)):
    return {"status": "alive"}


@app.post("/upload")
async def upload(file: UploadFile = File(...), _=Depends(verify_token)):
    """
    接收音频文件，流式写盘（不读进内存），归档+建 meta。
    """
    raw_name = file.filename or "unnamed.m4a"
    new_name, date_dir, duration_sec = parse_and_rename(raw_name)

    dest_dir = upload_dir / date_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / new_name
    rel_path = f"{date_dir}/{new_name}"

    # 流式写盘：从 UploadFile 的临时文件分块拷到目标，内存占用恒定
    with open(dest, "wb") as out_f:
        shutil.copyfileobj(file.file, out_f, length=16384)

    size_bytes = dest.stat().st_size
    write_meta(dest, rel_path, duration_sec, size_bytes)

    print(f"  ✓ 已保存: {rel_path}  ({size_bytes / 1024:.1f} KB)")
    return JSONResponse({"status": "ok", "id": rel_path})


@app.get("/lan-info")
async def get_lan_info(_=Depends(verify_token)):
    """获取电脑上报的局域网信息，超过 TTL 则视为过期。"""
    async with _lan_lock:
        if not _lan_info:
            return {"lan_ip": None}
        if time.time() - _lan_info.get("updated_at", 0) > LAN_TTL_SECONDS:
            return {"lan_ip": None}
        return {"lan_ip": _lan_info["lan_ip"], "port": _lan_info["port"]}


# ── 电脑接口 ─────────────────────────────────────────────────

@app.get("/pending")
async def list_pending(_=Depends(verify_token)):
    """列出所有 status=uploaded 的音频。"""
    results = []
    for meta_path in upload_dir.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("status") != "uploaded":
            continue
        results.append({
            "id": meta["rel_path"],
            "date": meta["rel_path"].split("/")[0] if "/" in meta["rel_path"] else "",
            "duration_sec": meta.get("duration_sec"),
            "uploaded_at": meta.get("uploaded_at"),
        })
    return results


@app.get("/download")
async def download(id: str = Query(...), _=Depends(verify_token)):
    """流式返回音频文件。id 为 URL 编码的相对路径。"""
    rel_path = unquote(id)
    audio_path = upload_dir / rel_path
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")
    return FileResponse(str(audio_path), media_type="audio/mp4", filename=audio_path.name)


@app.post("/result")
async def submit_result(
    id: str = Query(...),
    request: Request = ...,
    _=Depends(verify_token),
):
    """电脑提交转写结果，更新 meta 为 transcribed。"""
    rel_path = unquote(id)
    body = await request.json()

    meta = update_meta(rel_path, {
        "status": "transcribed",
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
        "transcript": body.get("transcript", ""),
        "raw": body.get("raw", ""),
        "language": body.get("language", ""),
    })

    preview = (meta.get("transcript") or "")[:40]
    print(f"  ✓ 转写回报: {rel_path} → \"{preview}\"")
    return {"status": "ok"}


@app.post("/lan-info")
async def set_lan_info(request: Request, _=Depends(verify_token)):
    """电脑上报局域网信息。"""
    body = await request.json()
    async with _lan_lock:
        _lan_info.update({
            "lan_ip": body.get("lan_ip"),
            "port": body.get("port"),
            "updated_at": time.time(),
        })
    print(f"  ✓ LAN info updated: {_lan_info['lan_ip']}:{_lan_info['port']}")
    return {"status": "ok"}


@app.delete("/lan-info")
async def clear_lan_info(_=Depends(verify_token)):
    """电脑关闭时清除局域网信息。"""
    async with _lan_lock:
        _lan_info.clear()
    print("  ✓ LAN info cleared")
    return {"status": "ok"}


# ── 启动 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # 单 worker：LAN 缓存在内存中，多 worker 会不共享
    print()
    print(f"  服务已启动：http://0.0.0.0:{PORT}")
    print(f"  上传目录：  {upload_dir.resolve()}")
    print(f"  时区：      {TIMEZONE}")
    print(f"  保留天数：  {RETENTION_DAYS}")
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT)
