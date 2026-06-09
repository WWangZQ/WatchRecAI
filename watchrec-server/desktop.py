"""
WatchRec 桌面入口 —— 双击即用。

一个进程里：
  1. 后台线程跑 uvicorn(server:app)，监听 0.0.0.0:8765（局域网接收 + VPS 轮询 + 转写）
  2. 用 Microsoft Edge 的「应用模式」(--app) 打开一个无地址栏、无标签的独立窗口显示 UI

为什么用 Edge 应用模式而不是 pywebview：本机 pythonnet 无法创建 .NET 运行时
(coreclr/netfx 均失败)，pywebview 的 Windows 后端起不来；Edge(WebView2 同源)
Win11 必带，--app 模式给出同样的「原生独立窗口」体验，且零 Python GUI 依赖。

stdout/stderr 重定向到日志缓冲(runtime_state.LOG) + 文件，前端日志面板可见，
无需弹出黑色命令行窗口。关闭窗口即退出整个服务。

由 WatchRec.vbs 用 pythonw 静默启动。
"""

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
os.chdir(HERE)

PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"

# ── 1. 接管 stdout/stderr：写到日志缓冲 + 文件，不依赖控制台 ──
import runtime_state


class _Tee:
    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass


_log_file = open(HERE / "watchrec.log", "a", encoding="utf-8", buffering=1)
# pythonw 下 sys.__stdout__ 为 None，Tee 会自动跳过
sys.stdout = _Tee(sys.__stdout__, _log_file, runtime_state.LOG)
sys.stderr = _Tee(sys.__stderr__, _log_file, runtime_state.LOG)

import logging

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)


# ── 2. 后台线程：uvicorn ───────────────────────────────────

def _port_alive(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _run_server():
    import uvicorn
    from server import app

    config = uvicorn.Config(
        app, host="0.0.0.0", port=PORT,
        log_config=None,      # 复用 root logging → 我们的 Tee
        access_log=False,     # 不记录每个请求（前端会高频轮询，避免刷屏）
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # 非主线程不能装信号处理器
    asyncio.run(server.serve())


def _ensure_server():
    if _port_alive(PORT):
        print(f"  ℹ 端口 {PORT} 已被占用，复用已运行的服务")
        return
    threading.Thread(target=_run_server, daemon=True, name="uvicorn").start()
    print("  ⏳ 正在启动服务...")
    for _ in range(240):  # 最多等 120s（首次加载模型较慢）
        if _port_alive(PORT):
            print("  ✓ 服务已就绪")
            return
        time.sleep(0.5)
    print("  ⚠ 服务启动超时，窗口仍会打开")


# ── 3. 窗口：Chromium 应用模式（无地址栏独立窗口）─────────
#
# 优先 Chrome：本机实测 Chrome --app 能稳定留住独立窗口，关闭窗口时
# 启动进程随之结束（proc.wait 解除阻塞）。Edge 在本机被后台单例抢占，
# --app 窗口留不住，故仅作次选；都没有则回退默认浏览器。

def _find_browser() -> tuple[str | None, str | None]:
    candidates = [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Chrome"),
        (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "Chrome"),
        (str(Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"), "Chrome"),
        (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "Edge"),
        (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Edge"),
    ]
    for p, name in candidates:
        if p and Path(p).exists():
            return p, name
    return None, None


def _open_window() -> subprocess.Popen | None:
    """用 Chromium --app 打开独立窗口；找不到则用默认浏览器兜底（返回 None）。"""
    browser, name = _find_browser()
    if not browser:
        print("  ⚠ 未找到 Chrome/Edge，改用默认浏览器打开")
        import webbrowser
        webbrowser.open(URL)
        return None

    # 专用 user-data-dir：起一个独立、可被本进程托管的窗口
    profile = Path(os.environ.get("LOCALAPPDATA", HERE)) / "WatchRec" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--app={URL}",
        "--window-size=1180,820",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print(f"  ✓ 打开应用窗口 ({name} app 模式)")
    return subprocess.Popen(args)


def main():
    _ensure_server()
    proc = _open_window()
    if proc is None:
        # 兜底浏览器模式：无法感知窗口关闭，服务转常驻
        print("  服务常驻中（浏览器模式）。结束请从任务管理器结束本进程。")
        threading.Event().wait()
    else:
        t0 = time.monotonic()
        proc.wait()  # 正常情况下阻塞至应用窗口关闭
        if time.monotonic() - t0 < 3:
            # 浏览器把窗口交给了已有实例后立即退出 → 无法靠它判断关闭，转常驻
            print("  浏览器进程已分离，服务转入常驻（关闭窗口不会停止服务）")
            threading.Event().wait()
        else:
            print("  ⏹ 窗口已关闭，退出")
    os._exit(0)


if __name__ == "__main__":
    main()
