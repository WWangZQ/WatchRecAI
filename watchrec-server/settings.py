"""
运行时设置（可在前端页面修改并持久化）。

优先级：settings.json（UI 保存）> .env / 环境变量（config.py 的默认值）。
存 watchrec-server/settings.json —— 含 API Key，已 gitignore。
llm.py 在每次调用时读取这里，所以页面保存后立即生效，无需重启。
"""

import json
import threading
from pathlib import Path

from config import LLM_API_KEY as _ENV_KEY
from config import LLM_BASE_URL as _ENV_BASE
from config import LLM_MODEL as _ENV_MODEL

_FILE = Path(__file__).parent / "settings.json"
_lock = threading.Lock()

_settings = {
    "llm_base_url": _ENV_BASE,
    "llm_api_key": _ENV_KEY,
    "llm_model": _ENV_MODEL or "gpt-4o-mini",
}


def _load():
    if _FILE.exists():
        try:
            saved = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        for k in _settings:
            if k in saved and saved[k] is not None:
                _settings[k] = saved[k]


_load()


def get_llm() -> dict:
    with _lock:
        return dict(_settings)


def save_llm(base_url: str, api_key: str | None, model: str) -> dict:
    """保存 LLM 设置。api_key 为空表示「不修改」（保留已存的），避免页面不回显时被清空。"""
    with _lock:
        _settings["llm_base_url"] = (base_url or "").strip()
        _settings["llm_model"] = (model or "gpt-4o-mini").strip()
        if api_key:  # 非空才覆盖
            _settings["llm_api_key"] = api_key.strip()
        _FILE.write_text(json.dumps(_settings, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(_settings)
