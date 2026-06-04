"""
WatchRec 音频接收服务

接收手表端通过 HTTP POST 上传的 .m4a 录音文件，保存到本地 uploads/ 目录。

启动方式：  python server.py   或   ./start.sh
默认端口：  8765（在 config.py 中修改）
"""

import socket
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from config import PORT, UPLOAD_DIR

app = FastAPI(title="WatchRec Server")

# 确保上传目录存在
upload_dir = Path(__file__).parent / UPLOAD_DIR
upload_dir.mkdir(exist_ok=True)


@app.get("/health")
def health():
    """手表端用此接口检测服务是否在线。"""
    return {"status": "alive"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    接收音频文件，保存到 uploads/ 目录。
    文件名保留手表端原始文件名。
    """
    dest = upload_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    print(f"  ✓ 已保存: {dest.name}  ({len(content) / 1024:.1f} KB)")
    # TODO: 上传完成后调用 transcribe(filepath) 进行转写
    return JSONResponse({"status": "ok", "filename": file.filename})


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
    print(f"  上传地址：http://{ip}:{PORT}/upload")
    print(f"  文件保存：{upload_dir.resolve()}")
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT)
