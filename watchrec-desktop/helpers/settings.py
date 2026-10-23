"""Read the integration app's settings only, without touching the old app."""
import json
import os
from pathlib import Path


def get_llm():
    data = json.loads(Path(os.environ["WATCHREC_SETTINGS"]).read_text(encoding="utf-8-sig"))
    return {
        "ai_enabled": bool(data.get("ai_enabled", False)),
        "llm_base_url": data.get("llm_base_url", ""),
        "llm_api_key": data.get("llm_api_key", ""),
        "llm_model": data.get("llm_model", ""),
    }

