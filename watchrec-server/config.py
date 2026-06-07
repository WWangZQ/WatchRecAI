"""WatchRec 电脑端配置 — VPS 拉取 + 本地转写"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── VPS 连接 ─────────────────────────────────────────────

VPS_BASE_URL = "https://202.189.23.245:27312"
APP_TOKEN = os.environ.get("APP_TOKEN", "")
CA_CERT = os.environ.get("CA_CERT", str(Path(__file__).parent / "server.crt"))

# ── 轮询 ─────────────────────────────────────────────────

POLL_INTERVAL_SEC = 30

# ── 本地存储 ─────────────────────────────────────────────

LOCAL_DATA_DIR = str(Path(__file__).parent / "downloads")
