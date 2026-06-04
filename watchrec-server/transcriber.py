"""
FunASR SenseVoice-Small 转写模块。

架构：单模型实例 + 单 worker 线程 + 队列驱动动态批处理。
- 模型全局唯一，用 threading.Lock + 双重检查保证只加载一次。
- worker 从 queue.Queue 取任务，攒批后调 model.generate(input=[...]) 并行推理。
"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue
from zoneinfo import ZoneInfo

from config import BATCH_SIZE_S, MAX_BATCH_FILES, TIMEZONE

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


# ── 单文件转写（内部用）─────────────────────────────────────

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
        "duration_sec": _get_duration(path),
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
            "duration_sec": _get_duration(Path(audio_paths[i])),
        })
    return output


# ── 边车文件写入 ───────────────────────────────────────────

def write_sidecar(audio_path: str, result: dict) -> Path:
    """将转写结果写入 .json 边车文件。"""
    audio = Path(audio_path)
    json_path = audio.with_suffix(".json")
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
    """检查音频是否已有 .json 边车文件。"""
    return Path(audio_path).with_suffix(".json").exists()


# ── TranscribeWorker：单线程 + 队列 + 动态批处理 ──────────

class TranscribeWorker:
    """
    常驻转写 worker。
    - /upload 把文件路径 put 进队列后立刻返回。
    - worker 阻塞等第一个任务，到达后非阻塞 drain 队列凑批，
      一次性调 model.generate(input=batch) 批量推理。
    - 每个文件各自写 .json 边车文件。
    """

    def __init__(self):
        self._queue: Queue[str] = Queue()
        self._thread: threading.Thread | None = None

    def start(self):
        """启动 worker 线程（服务启动时调一次）。"""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="transcribe-worker")
        self._thread.start()
        print("  ✓ 转写 worker 已启动")

    def submit(self, audio_path: str):
        """提交一个转写任务（非阻塞）。"""
        self._queue.put(audio_path)

    def submit_batch(self, paths: list[str]):
        """提交一批转写任务。"""
        for p in paths:
            self._queue.put(p)

    def wait_idle(self, timeout: float | None = None):
        """等待队列清空（用于 transcribe_all.py）。"""
        self._queue.join()

    def pending_count(self) -> int:
        return self._queue.qsize()

    # ── worker 主循环 ──────────────────────────────────────

    def _run(self):
        """常驻循环：阻塞等任务 → 凑批 → 批量推理 → 写结果 → 继续等。"""
        # 首次循环前加载模型（一次性）
        _get_model()

        while True:
            # 1. 阻塞等第一个任务
            first = self._queue.get()
            batch = [first]

            # 2. 非阻塞 drain：把队列里现有的任务一次性取空（上限 MAX_BATCH_FILES）
            while len(batch) < MAX_BATCH_FILES:
                try:
                    batch.append(self._queue.get_nowait())
                except Exception:
                    break

            # 3. 过滤已有边车的文件
            todo = [p for p in batch if not has_sidecar(p)]
            skip_count = len(batch) - len(todo)

            if not todo:
                for _ in batch:
                    self._queue.task_done()
                continue

            if skip_count:
                print(f"  ⏭ 跳过 {skip_count} 个已有转写的文件")

            # 4. 批量转写
            names = [Path(p).name for p in todo]
            n = len(todo)
            print(f"  🎙️  转写批次: {n} 个文件 — {', '.join(names[:3])}{'...' if n > 3 else ''}")
            logger.info(f"Batch transcribe: {n} files")

            t0 = time.monotonic()
            try:
                if n == 1:
                    results = [_transcribe_single(_model, todo[0])]
                else:
                    results = _transcribe_batch(_model, todo)

                elapsed = time.monotonic() - t0

                # 5. 逐文件写边车 + 打印
                for i, (path, result) in enumerate(zip(todo, results)):
                    write_sidecar(path, result)
                    preview = result["transcript"][:50]
                    suffix = "..." if len(result["transcript"]) > 50 else ""
                    print(f"    ✓ [{i+1}/{n}] {Path(path).name} → \"{preview}{suffix}\"")

                total_dur = sum(r.get("duration_sec", 0) for r in results)
                speed = total_dur / elapsed if elapsed > 0 else 0
                print(f"  ✓ 批次完成: {n} 个文件, {elapsed:.1f}s, "
                      f"音频 {total_dur:.0f}s, RTF {speed:.1f}x")

            except Exception as e:
                elapsed = time.monotonic() - t0
                print(f"  ✗ 批次转写失败 ({elapsed:.1f}s): {e}")
                logger.error(f"Batch transcribe failed: {e}", exc_info=True)

            finally:
                # 6. 标记所有任务完成
                for _ in batch:
                    self._queue.task_done()


# ── 模块级单例 ─────────────────────────────────────────────

_worker: TranscribeWorker | None = None


def get_worker() -> TranscribeWorker:
    """获取全局 worker 单例。"""
    global _worker
    if _worker is None:
        _worker = TranscribeWorker()
    return _worker


def init_worker():
    """启动 worker（FastAPI lifespan 调用）。"""
    get_worker().start()


def submit(audio_path: str):
    """提交单个文件到转写队列。"""
    get_worker().submit(audio_path)


def submit_batch(paths: list[str]):
    """提交一批文件到转写队列。"""
    get_worker().submit_batch(paths)


def wait_idle(timeout: float | None = None):
    """等待队列清空。"""
    get_worker().wait_idle(timeout)


# ── 辅助函数 ──────────────────────────────────────────────

def _parse_recorded_at(filename: str) -> str:
    """从文件名解析录制时间。"""
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
