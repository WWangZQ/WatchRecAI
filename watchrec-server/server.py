"""
WatchRec 音频接收服务

接收手表端上传的 .m4a 录音，按日期归档，后台批量转写。

启动方式：  python server.py   或   ./start.sh / start.bat
默认端口：  8765（在 config.py 中修改）
"""

import re
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from config import PORT, UPLOAD_DIR, TIMEZONE

tz = ZoneInfo(TIMEZONE)
upload_dir = Path(__file__).parent / UPLOAD_DIR

FILENAME_RE = re.compile(r"^recording_(\d+)_(.+)\.m4a$")


# ── lifespan：服务启动时初始化转写 worker ─────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    upload_dir.mkdir(exist_ok=True)
    from transcriber import init_worker
    init_worker()
    yield
    # 关闭时无特殊清理（worker 是 daemon 线程，随进程退出）


app = FastAPI(title="WatchRec Server", lifespan=lifespan)


# ── 文件名解析 ─────────────────────────────────────────────

def parse_and_rename(raw_name: str) -> tuple[str, str]:
    """解析手表端文件名，返回 (新文件名, 日期子目录)。"""
    m = FILENAME_RE.match(raw_name)
    if m:
        ts_ms = int(m.group(1))
        suffix = m.group(2)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    else:
        dt = datetime.now(tz=tz)
        suffix = "unknown"

    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{time_str}_{suffix}.m4a", date_str


# ── 路由 ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "alive"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    接收音频文件 → 保存到按日期归档的目录 → 提交转写队列 → 立刻返回 200。
    """
    raw_name = file.filename or "unnamed.m4a"
    new_name, date_dir = parse_and_rename(raw_name)

    dest_dir = upload_dir / date_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / new_name
    content = await file.read()
    dest.write_bytes(content)

    rel_path = f"{date_dir}/{new_name}"
    print(f"  ✓ 已保存: {rel_path}  ({len(content) / 1024:.1f} KB)")

    # 提交到转写队列（非阻塞）
    from transcriber import submit
    submit(str(dest))

    return JSONResponse({"status": "ok", "filename": new_name, "path": rel_path})


# ── 启动 ───────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    import uvicorn

    ip = get_local_ip()
    print()
    print(f"  服务已启动：http://{ip}:{PORT}")
    print(f"  上传地址：  http://{ip}:{PORT}/upload")
    print(f"  文件保存：  {upload_dir.resolve()}")
    print(f"  时区：      {TIMEZONE}")
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT)
