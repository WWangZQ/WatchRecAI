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
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 减少长音频转写时的显存碎片（必须在 torch 初始化 CUDA 上下文之前设置）。
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from config import (
    BATCH_SIZE_S,
    CHUNK_WINDOW_SEC,
    LONG_AUDIO_THRESHOLD_SEC,
    TIMEZONE,
)

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

# 单批显存随 batch_size_s 增长；长音频或显存碎片化时一档可能 OOM，逐级缩小重试。
_BATCH_FALLBACKS = [bs for bs in (BATCH_SIZE_S, 120, 60, 30) if bs <= BATCH_SIZE_S] or [BATCH_SIZE_S]


def _empty_cuda_cache():
    """把 PyTorch 缓存的显存块还给驱动，缓解跨文件累积/碎片。无 CUDA 时静默跳过。"""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _is_oom(e: Exception) -> bool:
    return "out of memory" in str(e).lower()


def _ffmpeg_chunk(src: Path, start: float, length: float, dst: Path) -> None:
    """用 ffmpeg 截取 [start, start+length) 段，转成 16kHz 单声道 wav 喂给模型。"""
    exe = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [exe, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", str(src), "-ar", "16000", "-ac", "1", str(dst)],
        check=True, capture_output=True,
    )


def _transcribe_long(model, path: Path, total_sec: float) -> dict:
    """超长音频：切成 CHUNK_WINDOW_SEC 一片逐片转写再拼接，峰值显存与总时长无关。"""
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    n_chunks = int((total_sec + CHUNK_WINDOW_SEC - 1) // CHUNK_WINDOW_SEC)
    print(f"  ⏳ 长音频 {total_sec/60:.0f} 分钟，切成 {n_chunks} 片（每片 {CHUNK_WINDOW_SEC//60} 分钟）逐片转写")

    raws: list[str] = []
    language = "unknown"
    tmpdir = Path(tempfile.mkdtemp(prefix="wrec_chunk_"))
    try:
        start = 0.0
        idx = 0
        while start < total_sec - 0.05:
            length = min(float(CHUNK_WINDOW_SEC), total_sec - start)
            chunk = tmpdir / f"c{idx}.wav"
            _ffmpeg_chunk(path, start, length, chunk)
            _empty_cuda_cache()
            res = model.generate(
                input=str(chunk), cache={}, language="auto", use_itn=True,
                batch_size_s=BATCH_SIZE_S, merge_vad=True, merge_length_s=15,
            )
            _empty_cuda_cache()
            raws.append(res[0]["text"])
            lang = res[0].get("language")
            if lang:
                language = lang
            try:
                chunk.unlink()
            except OSError:
                pass
            idx += 1
            start += length
            print(f"    … 切片 {idx}/{n_chunks} 完成（至 {int(min(start, total_sec))}/{int(total_sec)}s）")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    raw_text = "".join(raws)
    clean_text = rich_transcription_postprocess(raw_text)
    return {
        "transcript": clean_text,
        "raw": raw_text,
        "language": language,
        "duration_sec": total_sec,
    }


def _transcribe_single(model, audio_path: str) -> dict:
    """转写单个文件，返回结果字典。OOM 时清显存并逐级缩小 batch 重试。"""
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    path = Path(audio_path)

    # 超长音频改走切片转写：单次 generate 的峰值显存（VAD/特征提取对全文件的
    # 大张量 + 调用内累积）随总时长增长，缩 batch_size_s 救不了，必须切短输入。
    total = audio_duration(path) or 0
    if total > LONG_AUDIO_THRESHOLD_SEC and shutil.which("ffmpeg"):
        return _transcribe_long(model, path, total)

    _empty_cuda_cache()  # 开工前先腾出最大可用显存

    res = None
    for i, bs in enumerate(_BATCH_FALLBACKS):
        try:
            res = model.generate(
                input=str(path),
                cache={},
                language="auto",
                use_itn=True,
                batch_size_s=bs,
                merge_vad=True,
                merge_length_s=15,
            )
            break
        except RuntimeError as e:
            if not _is_oom(e) or i == len(_BATCH_FALLBACKS) - 1:
                _empty_cuda_cache()
                raise
            nxt = _BATCH_FALLBACKS[i + 1]
            print(f"  ⚠ 显存不足(batch_size_s={bs})，清缓存后改用 {nxt}s 重试…")
            logger.warning("CUDA OOM at batch_size_s=%s, retry with %s", bs, nxt)
            _empty_cuda_cache()

    raw_text = res[0]["text"]
    clean_text = rich_transcription_postprocess(raw_text)
    language = res[0].get("language", "unknown")
    _empty_cuda_cache()  # 收尾释放，给下一条留干净显存

    return {
        "transcript": clean_text,
        "raw": raw_text,
        "language": language,
        "duration_sec": audio_duration(path),
    }


def _transcribe_batch(model, audio_paths: list[str]) -> list[dict]:
    """
    批量转写：一次 model.generate 调用处理多个文件。
    FunASR 内部会按 batch_size_s 拆分并行。
    """
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    _empty_cuda_cache()
    results_raw = model.generate(
        input=audio_paths,
        cache={},
        language="auto",
        use_itn=True,
        batch_size_s=BATCH_SIZE_S,
        merge_vad=True,
        merge_length_s=15,
    )
    _empty_cuda_cache()

    output = []
    for i, res in enumerate(results_raw):
        raw_text = res["text"]
        clean_text = rich_transcription_postprocess(raw_text)
        language = res.get("language", "unknown")
        output.append({
            "transcript": clean_text,
            "raw": raw_text,
            "language": language,
            "duration_sec": audio_duration(audio_paths[i]),
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
        "headline": result.get("headline"),           # 短标题（待生成）
        "transcribed_at": datetime.now(_tz).strftime("%Y-%m-%d %H:%M:%S"),
    }

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def write_failed_sidecar(audio_path: str, error: str) -> Path:
    """音频无法转写（损坏/格式错误）时写一个失败边车，避免 poller 死循环重试。"""
    audio = Path(audio_path)
    json_path = audio.with_suffix(".json")
    first = (error or "").splitlines()[0][:200] if error else "转写失败"
    data = {
        "audio_file": audio.name,
        "title": None,
        "recorded_at": _parse_recorded_at(audio.name),
        "duration_sec": audio_duration(audio),
        "language": "",
        "transcript": "",
        "raw": "",
        "full_text": None,
        "summary": None,
        "headline": None,
        "error": first,
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


def _probe_duration(audio_path) -> float | None:
    """用 ffprobe 读音频真实时长（秒），失败返回 None。"""
    import shutil
    import subprocess

    exe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(audio_path)],
            capture_output=True, text=True, timeout=60,
        )
        v = out.stdout.strip()
        return round(float(v), 2) if v else None
    except Exception:
        return None


def audio_duration(audio_path) -> float | None:
    """优先从文件名解析时长（手表录音），否则用 ffprobe 探测（手动上传等）。"""
    d = _parse_duration_from_filename(Path(audio_path))
    return d if d else _probe_duration(audio_path)
