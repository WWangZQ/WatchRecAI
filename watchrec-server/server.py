"""
WatchRec 音频接收服务

接收手表端通过 HTTP POST 上传的 .m4a 录音文件。
- 按录制日期归档到 uploads/YYYY-MM-DD/ 子目录
- 文件名重命名为可读格式：YYYY-MM-DD_HH-MM-SS_<原随机数>.m4a

启动方式：  python server.py   或   ./start.sh
默认端口：  8765（在 config.py 中修改）
"""

import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from config import PORT, UPLOAD_DIR, TIMEZONE

app = FastAPI(title="WatchRec Server")

upload_dir = Path(__file__).parent / UPLOAD_DIR
upload_dir.mkdir(exist_ok=True)

tz = ZoneInfo(TIMEZONE)

# 匹配手表端文件名：recording_<timestamp>_<suffix>.m4a
FILENAME_RE = re.compile(r"^recording_(\d+)_(.+)\.m4a$")


def parse_and_rename(raw_name: str) -> tuple[str, str]:
    """
    解析原始文件名，返回 (新文件名, 日期子目录名)。

    格式示例：
        recording_1780570430011_486997.m4a
        → timestamp = 1780570430011 ms
        → 本地时间 2026-06-04 14:30:30
        → 新文件名 2026-06-04_14-30-30_486997.m4a
        → 子目录   2026-06-04

    解析失败时用当前服务器时间兜底。
    """
    m = FILENAME_RE.match(raw_name)
    if m:
        ts_ms = int(m.group(1))
        suffix = m.group(2)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    else:
        # 文件名格式不符预期，用当前时间兜底
        dt = datetime.now(tz=tz)
        suffix = "unknown"

    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%Y-%m-%d_%H-%M-%S")
    new_name = f"{time_str}_{suffix}.m4a"
    return new_name, date_str


@app.get("/health")
def health():
    """手表端用此接口检测服务是否在线。"""
    return {"status": "alive"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    接收音频文件，按录制日期归档保存。
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

    # TODO: 上传完成后调用 transcribe(filepath) 进行转写
    return JSONResponse({"status": "ok", "filename": new_name, "path": rel_path})


def get_local_ip() -> str:
    """获取本机局域网 IP。"""
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
