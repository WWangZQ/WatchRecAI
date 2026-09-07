"""Connection changes are validated on save and applied on the next launch."""
import ipaddress
import json
import os
import re
import threading
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv
from paths import DATA_HOME, RESOURCE_DIR

load_dotenv(DATA_HOME / ".env")
load_dotenv(RESOURCE_DIR / ".env")
FILE = DATA_HOME / "connection.json"
_lock = threading.Lock()


def normalize_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        u = urlsplit(value)
        port = u.port
        if (u.scheme not in {"http", "https"} or not u.hostname or u.username is not None
                or u.password is not None or u.query or u.fragment or "?" in value or "#" in value
                or re.search(r"[\s\\]", value) or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
    except ValueError:
        raise ValueError("服务器地址须以 http:// 或 https:// 开头，可含端口和路径，不能含账号、空格、查询参数或 #。") from None
    return urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), "", ""))


def validate_token(value: str) -> str:
    value = str(value or "").strip()
    if value and (len(value) < 16 or not re.fullmatch(r"[A-Za-z0-9_.~-]+", value)):
        raise ValueError("连接密钥至少 16 位，请使用英文字母、数字或 _ . ~ -。")
    return value


def validate(result: dict) -> dict:
    result = dict(result)
    result["vps_base_url"] = normalize_url(result["vps_base_url"])
    result["app_token"] = validate_token(result["app_token"])
    try:
        port = int(result["local_port"])
        if not 1024 <= port <= 65535:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("电脑端口须为 1024–65535 的整数。") from None
    result["local_port"] = port
    if not isinstance(result["lan_enabled"], bool):
        raise ValueError("局域网直传选项必须为布尔值。")
    ip = str(result["lan_ip_override"] or "").strip()
    if ip:
        try:
            addr = ipaddress.IPv4Address(ip)
            if not any(addr in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
                raise ValueError()
        except ValueError:
            raise ValueError("请填写电脑的局域网 IPv4 地址，或留空自动探测。") from None
    result["lan_ip_override"] = ip
    if result["asr_device"] not in {"auto", "cpu", "cuda:0"}:
        raise ValueError("转写设备须为 auto、cpu 或 cuda:0。")
    return result


def read_connection() -> dict:
    result = {
        "vps_base_url": os.environ.get("VPS_BASE_URL", ""),
        "app_token": os.environ.get("APP_TOKEN", ""),
        "local_port": int(os.environ.get("PORT", "18765")),
        "lan_enabled": os.environ.get("LAN_ENABLED", "false").lower() == "true",
        "lan_ip_override": os.environ.get("LAN_IP_OVERRIDE", ""),
        "asr_device": os.environ.get("ASR_DEVICE", "auto"),
    }
    if FILE.exists():
        saved = json.loads(FILE.read_text(encoding="utf-8"))
        result.update({k: saved[k] for k in result if k in saved})
    return validate(result)


def public_connection(c: dict) -> dict:
    return {**{k: v for k, v in c.items() if k != "app_token"}, "token_set": bool(c["app_token"])}


def save_connection(body: dict) -> dict:
    with _lock:
        result = read_connection()
        for k in result:
            if k in body and k != "app_token":
                result[k] = body[k]
        if body.get("clear_token"):
            result["app_token"] = ""
        elif body.get("app_token"):
            result["app_token"] = body["app_token"]
        result = validate(result)
        if (result["vps_base_url"] or result["lan_enabled"]) and not result["app_token"]:
            raise ValueError("连接服务器或启用局域网直传前，请填写连接密钥。")
        temp = FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, FILE)
        return result
