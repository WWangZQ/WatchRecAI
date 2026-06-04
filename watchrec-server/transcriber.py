"""
FunASR SenseVoice-Small 转写模块。

首次加载模型约需 1-2 分钟（含下载），后续调用复用已加载模型。
"""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import TIMEZONE

logger = logging.getLogger("transcriber")

# 全局单例，延迟初始化
_model = None
_tz = ZoneInfo(TIMEZONE)


def _get_model():
    """延迟加载 SenseVoice-Small 模型（仅首次调用时加载）。"""
    global _model
    if _model is not None:
        return _model

    logger.info("正在加载 SenseVoice-Small 模型（首次运行会从 ModelScope 下载）...")
    print("  ⏳ 正在加载 SenseVoice-Small 模型（首次运行会自动下载）...")

    from funasr import AutoModel

    _model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device="cuda:0",
    )

    logger.info("模型加载完成")
    print("  ✓ SenseVoice-Small 模型已加载 (GPU)")
    return _model


def transcribe(audio_path: str) -> dict:
    """
    转写音频文件。

    Args:
        audio_path: .m4a 文件路径

    Returns:
        {
            "transcript": "清洗后的纯文本",
            "raw": "带情感/事件标记的原始文本",
            "language": "识别到的语种代码",
            "duration_sec": 音频时长（秒）
        }
    """
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    model = _get_model()
    path = Path(audio_path)

    if not path.exists():
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    logger.info(f"开始转写: {path.name}")
    print(f"  🎙️  转写中: {path.name} ...")

    res = model.generate(
        input=str(path),
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=60,
        merge_vad=True,
        merge_length_s=15,
    )

    raw_text = res[0]["text"]
    clean_text = rich_transcription_postprocess(raw_text)
    language = res[0].get("language", "unknown")

    result = {
        "transcript": clean_text,
        "raw": raw_text,
        "language": language,
        "duration_sec": _get_duration(path),
    }

    preview = clean_text[:40] + ("..." if len(clean_text) > 40 else "")
    logger.info(f"转写完成: {path.name} → {preview}")
    print(f"  ✓ 转写完成: {path.name} → \"{preview}\"")

    return result


def write_sidecar(audio_path: str, result: dict) -> Path:
    """
    将转写结果写入音频同目录下的 .json 边车文件。

    Args:
        audio_path: 原始音频文件路径
        result: transcribe() 返回的字典

    Returns:
        .json 文件路径
    """
    import json

    audio = Path(audio_path)
    json_path = audio.with_suffix(".json")

    # 从文件名解析录制时间
    recorded_at = _parse_recorded_at(audio.name)

    data = {
        "audio_file": audio.name,
        "recorded_at": recorded_at,
        "duration_sec": result.get("duration_sec"),
        "language": result.get("language", ""),
        "transcript": result.get("transcript", ""),
        "raw": result.get("raw", ""),
        "transcribed_at": datetime.now(_tz).strftime("%Y-%m-%d %H:%M:%S"),
    }

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def has_sidecar(audio_path: str) -> bool:
    """检查音频是否已有对应的 .json 边车文件。"""
    return Path(audio_path).with_suffix(".json").exists()


def transcribe_and_save(audio_path: str) -> dict | None:
    """
    转写音频并保存 .json 边车文件。
    如果已有边车文件则跳过。

    Returns:
        转写结果字典，已存在则返回 None
    """
    if has_sidecar(audio_path):
        logger.info(f"跳过（已有转写）: {Path(audio_path).name}")
        return None

    result = transcribe(audio_path)
    write_sidecar(audio_path, result)
    return result


# ── 内部辅助 ─────────────────────────────────────────────────

import re

_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")


def _parse_recorded_at(filename: str) -> str:
    """从可读文件名解析录制时间，如 2026-06-04_14-30-30_486997.m4a → 2026-06-04 14:30:30。"""
    m = _FILENAME_RE.search(filename)
    if m:
        return m.group(1).replace("_", " ").replace("-", "-", 2)
    return "unknown"


def _get_duration(audio_path: Path) -> float:
    """获取音频时长（秒）。"""
    try:
        import soundfile as sf
        info = sf.info(str(audio_path))
        return round(info.duration, 2)
    except Exception:
        return 0.0
