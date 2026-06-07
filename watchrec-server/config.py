"""WatchRec 电脑端配置 — 统一服务（LAN 接收 + VPS 轮询 + 转写）"""

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
IP_REPORT_INTERVAL_SEC = 120  # 2 分钟

# ── 本地存储 ─────────────────────────────────────────────

LOCAL_DATA_DIR = str(Path(__file__).parent / "downloads")

# ── 转写参数 ─────────────────────────────────────────────

BATCH_SIZE_S = 300
MAX_BATCH_FILES = 16
TIMEZONE = "Asia/Shanghai"

# ── LAN IP（留空 = 自动探测，Clash TUN 下可能返回假 IP，手表回退走 VPS，无害）──
LAN_IP_OVERRIDE = os.environ.get("LAN_IP_OVERRIDE", "")
