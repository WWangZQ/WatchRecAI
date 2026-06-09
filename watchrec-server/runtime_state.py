"""
运行时状态 + 日志环形缓冲。

桌面模式(desktop.py)下，stdout/stderr 被重定向到 LOG，前端日志面板通过
/api/logs 拉取；服务状态(轮询/转写/模型)写入 STATE，前端通过 /api/status 展示。
独立模式(python server.py)下这里仍可用，只是日志同时还打到控制台。

无重依赖，可被 server.py / worker.py 安全 import。
"""

import collections
import threading
import time

_lock = threading.Lock()
_lines: "collections.deque[tuple[int, str]]" = collections.deque(maxlen=1000)
_counter = 0


class _LogSink:
    """file-like：把写入按行存进环形缓冲，每行一个递增 id。"""

    def write(self, s: str):
        global _counter
        if not s:
            return
        with _lock:
            for part in s.splitlines():
                if not part.strip():
                    continue
                _counter += 1
                _lines.append((_counter, part))

    def flush(self):
        pass


LOG = _LogSink()


def get_logs(since: int = 0, limit: int = 500) -> dict:
    """返回 id > since 的日志行（最多 limit 条）及最新 id。"""
    with _lock:
        items = [(i, t) for (i, t) in _lines if i > since]
    items = items[-limit:]
    last = items[-1][0] if items else since
    return {"last": last, "lines": [t for (_, t) in items]}


# ── 服务状态 ─────────────────────────────────────────────

STATE: dict = {
    "started_at": time.time(),
    "lan_ip": None,
    "last_poll_at": None,
    "pending": 0,
    "model_loaded": False,
    "transcribing": None,
}


def set_state(**kw):
    STATE.update(kw)


def get_state() -> dict:
    s = dict(STATE)
    s["uptime_sec"] = int(time.time() - s["started_at"])
    return s
