"""Standalone desktop entry; also supports --headless for diagnostics."""
import argparse
import asyncio
import json
import logging
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

from paths import DATA_HOME, RESOURCE_DIR


class Tee:
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]
    def write(self, value):
        for stream in self.streams:
            try: stream.write(value)
            except Exception: pass
    def flush(self):
        for stream in self.streams:
            try: stream.flush()
            except Exception: pass
    def isatty(self):
        return False


def find_browser():
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


def main():
    parser = argparse.ArgumentParser(description="WatchRec Open Source")
    parser.add_argument("--headless", action="store_true", help="只启动本地服务，不打开窗口")
    parser.add_argument("--check", action="store_true", help="检查打包依赖，不下载模型")
    parser.add_argument("--transcribe-file", help="转写指定测试音频，在其旁边保存结果")
    args = parser.parse_args()
    os.environ["PATH"] = str(RESOURCE_DIR / "bin") + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("MODELSCOPE_CACHE", str(DATA_HOME / "models"))
    os.environ.setdefault("HF_HOME", str(DATA_HOME / "hf-cache"))
    from runtime_state import LOG
    logfile = open(DATA_HOME / "watchrec.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.stdout, logfile, LOG)
    sys.stderr = Tee(sys.stderr, logfile, LOG)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.check:
        import torch, torchaudio, funasr, soundfile
        import importlib.metadata
        print(json.dumps({"torch": torch.__version__, "torchaudio": torchaudio.__version__,
            "funasr": importlib.metadata.version("funasr"), "cuda": torch.cuda.is_available(),
            "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
            "data_dir": str(DATA_HOME)}, ensure_ascii=False))
        return
    if args.transcribe_file:
        from transcriber import transcribe_files, write_sidecar
        path = str(Path(args.transcribe_file).resolve())
        result = transcribe_files([path])[0]
        write_sidecar(path, result)
        print(json.dumps(result, ensure_ascii=False))
        return
    from config import HOST, PORT
    import uvicorn
    from server import app
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        listener.bind((HOST, PORT))
    except OSError as e:
        listener.close()
        raise RuntimeError(f"端口 {PORT} 已被占用，未连接或修改已运行的服务。请关闭重复的开源版，或修改 {DATA_HOME / 'connection.json'} 中的 local_port 后重试。") from e
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, proxy_headers=False, access_log=False))
    if args.headless:
        server.run(sockets=[listener])
        return
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        if not thread.is_alive(): raise RuntimeError("服务启动失败，请查看开源版数据目录里的 watchrec.log。")
        time.sleep(0.1)
    if not server.started: raise RuntimeError("服务启动超时，请查看 watchrec.log。")
    url = f"http://127.0.0.1:{PORT}"
    browser = find_browser()
    try:
        if browser:
            proc = subprocess.Popen([browser, f"--app={url}", "--window-size=1180,820",
                f"--user-data-dir={DATA_HOME / 'browser-profile'}", "--no-first-run", "--no-default-browser-check"])
            started = time.monotonic()
            proc.wait()
            if time.monotonic() - started < 3:
                print("浏览器窗口由已有进程接管；请在任务管理器中结束开源版进程以停止服务。")
                thread.join()
        else:
            import webbrowser
            webbrowser.open(url)
            print("未找到 Chrome/Edge；请使用当前浏览器。结束服务可关闭此进程。")
            thread.join()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if os.name == "nt" and "--headless" not in sys.argv and "--check" not in sys.argv and "--transcribe-file" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "WatchRec 开源版启动失败", 0x10)
        sys.exit(1)
