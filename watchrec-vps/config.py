"""VPS configuration: environment overrides this directory's .env file."""
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
if not re.fullmatch(r"[A-Za-z0-9_.~-]{16,}", APP_TOKEN) or APP_TOKEN == "CHANGE_ME":
    raise RuntimeError("请在 .env 设置 APP_TOKEN：至少 16 位的随机英文字母/数字密钥。可运行 python setup.py 生成。")
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Shanghai")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "3"))
LAN_TTL_SECONDS = int(os.environ.get("LAN_TTL_SECONDS", "300"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(Path(__file__).parent / "data" / "uploads"))).expanduser().resolve()
if not 1 <= PORT <= 65535 or RETENTION_DAYS < 0 or LAN_TTL_SECONDS < 1:
    raise RuntimeError("请核对 PORT (1–65535)、RETENTION_DAYS (>=0)、LAN_TTL_SECONDS (>0)。")
