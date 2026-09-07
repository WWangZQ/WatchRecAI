"""Defaults for a fresh installation; connection changes apply on restart."""
import os
from paths import DATA_HOME
from connection_settings import read_connection

CONNECTION = read_connection()
VPS_BASE_URL = CONNECTION["vps_base_url"]
APP_TOKEN = CONNECTION["app_token"]
PORT = CONNECTION["local_port"]
LAN_ENABLED = CONNECTION["lan_enabled"]
HOST = "0.0.0.0" if LAN_ENABLED else "127.0.0.1"
LAN_IP_OVERRIDE = CONNECTION["lan_ip_override"]
ASR_DEVICE = CONNECTION["asr_device"]
POLL_INTERVAL_SEC = 30
IP_REPORT_INTERVAL_SEC = 120
LOCAL_DATA_DIR = str(DATA_HOME / "downloads")
BATCH_SIZE_S = 300
MAX_BATCH_FILES = 16
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Shanghai")
LONG_AUDIO_THRESHOLD_SEC = 1800
CHUNK_WINDOW_SEC = 1200
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
