"""
FunASR SenseVoice-Small 转写模块。

单模型实例（threading.Lock + 双重检查保证只加载一次），供 worker.py 的
TranscribeWorker 线程调用。对外暴露：
  - ensure_model_loaded() —— 启动时预加载
  - transcribe_files(paths) —— 批量转写
  - write_sidecar(path, result) —— 写 .json 边车文件
"""

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import BATCH_SIZE_S, TIMEZONE

logger = logging.getLogger("transcriber")

_tz = ZoneInfo(TIMEZONE)
_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")

# ── 模型（线程安全，双重检查锁）─────────────────────────────

_model = None
_model_lock = threading.Lock()


def _get_model():
    """加载 SenseVoice-Small，全局唯一，首次调用时下载+初始化。"""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model

        print("  ⏳ 正在加载 SenseVoice-Small 模型（首次运行会从 ModelScope 下载）...")
        logger.info("Loading SenseVoice-Small ...")

        from funasr import AutoModel

        model = AutoModel(
            model="iic/SenseVoiceSmall",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device="cuda:0",
        )

        _model = model
        print("  ✓ SenseVoice-Small 模型已加载 (GPU)")
        logger.info("Model loaded")
        return _model


# ── 转写 ───────────────────────────────────────────────────

def _transcribe_single(model, audio_path: str) -> dict:
    """转写单个文件，返回结果字典。"""
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    path = Path(audio_path)
    res = model.generate(
        input=str(path),
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=BATCH_SIZE_S,
        merge_vad=True,
        merge_length_s=15,
    )

    raw_text = res[0]["text"]
    clean_text = rich_transcription_postprocess(raw_text)
    language = res[0].get("language", "unknown")

    return {
        "transcript": clean_text,
        "raw": raw_text,
        "language": language,
        "duration_sec": _parse_duration_from_filename(path),
    }


def _transcribe_batch(model, audio_paths: list[str]) -> list[dict]:
    """
    批量转写：一次 model.generate 调用处理多个文件。
    FunASR 内部会按 batch_size_s 拆分并行。
    """
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    results_raw = model.generate(
        input=audio_paths,
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=BATCH_SIZE_S,
        merge_vad=True,
        merge_length_s=15,
    )

    output = []
    for i, res in enumerate(results_raw):
        raw_text = res["text"]
        clean_text = rich_transcription_postprocess(raw_text)
        language = res.get("language", "unknown")
        output.append({
            "transcript": clean_text,
            "raw": raw_text,
            "language": language,
            "duration_sec": _parse_duration_from_filename(Path(audio_paths[i])),
        })
    return output


def ensure_model_loaded():
    """预加载模型（启动时调用，避免第一批音频卡在模型下载）。"""
    _get_model()


def transcribe_files(audio_paths: list[str]) -> list[dict]:
    """
    批量转写，返回与 audio_paths 等长的结果列表。
    """
    model = _get_model()
    n = len(audio_paths)
    if n == 0:
        return []
    if n == 1:
        return [_transcribe_single(model, audio_paths[0])]
    return _transcribe_batch(model, audio_paths)


# ── 边车文件写入 ───────────────────────────────────────────

def write_sidecar(audio_path: str, result: dict) -> Path:
    """将转写结果写入 .json 边车文件。"""
    audio = Path(audio_path)
    json_path = audio.with_suffix(".json")
    recorded_at = _parse_recorded_at(audio.name)

    data = {
        "audio_file": audio.name,
        "title": None,                                # 自定义标题（重命名用）
        "recorded_at": recorded_at,
        "duration_sec": result.get("duration_sec"),
        "language": result.get("language", ""),
        "transcript": result.get("transcript", ""),  # 原文：去标记逐字稿
        "raw": result.get("raw", ""),                 # 原始标记文本
        "full_text": result.get("full_text"),         # 全文：AI 去噪（待生成）
        "summary": result.get("summary"),             # AI 总结（待生成）
        "transcribed_at": datetime.now(_tz).strftime("%Y-%m-%d %H:%M:%S"),
    }

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def update_sidecar(audio_path: str, fields: dict) -> None:
    """把若干字段合并进已有的 .json 边车（如 AI 生成的 full_text / summary）。"""
    json_path = Path(audio_path).with_suffix(".json")
    if not json_path.exists():
        return
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data.update(fields)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 辅助函数 ──────────────────────────────────────────────

def _parse_recorded_at(filename: str) -> str:
    """从文件名解析录制时间。"""
    m = _FILENAME_RE.search(filename)
    if m:
        return m.group(1).replace("_", " ").replace("-", "-", 2)
    return "unknown"


def _parse_duration_from_filename(audio_path: Path) -> float | None:
    """
    从文件名末尾解析时长毫秒数。
    文件名格式: YYYY-MM-DD_HH-MM-SS_<duration_ms>.m4a
    返回秒数，解析失败返回 None。
    """
    name = audio_path.stem  # 去掉 .m4a
    parts = name.rsplit("_", 1)
    if len(parts) == 2:
        try:
            ms = int(parts[1])
            if ms > 0:
                return round(ms / 1000.0, 2)
        except ValueError:
            pass
    return None
