"""
统一转写 worker：单队列 + 单线程 + 批处理。

LAN 上传和 VPS 下载的音频走同一条路，同一个模型实例，零并发风险。
模型在 worker 线程内首次使用时加载（不阻塞 uvicorn 启动）。

容错：一个损坏文件不拖垮整批 —— 批量失败时退化为逐个转写，无法解码的
文件写「失败边车」并跳过，避免 poller 反复重试同一坏文件。
submit() 去重，防止 poller 每轮重复入队。
"""

import threading
import time
from pathlib import Path
from queue import Queue

from transcriber import transcribe_files, write_failed_sidecar, write_sidecar
from runtime_state import set_state


class TranscribeWorker:
    def __init__(self):
        self._queue: Queue = Queue()
        self._queued: set[str] = set()        # 已入队/在途的路径，去重
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_flag = False

    def start(self):
        if self._thread is not None:
            return
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="transcribe-worker")
        self._thread.start()

    def submit(self, audio_path: str):
        with self._lock:
            if audio_path in self._queued:
                return
            self._queued.add(audio_path)
        self._queue.put(audio_path)

    def is_queued(self, audio_path: str) -> bool:
        with self._lock:
            return audio_path in self._queued

    def _done(self, audio_path: str):
        with self._lock:
            self._queued.discard(audio_path)

    def wait_idle(self):
        self._queue.join()

    def stop(self):
        self._stop_flag = True
        self._queue.put(None)  # 唤醒阻塞的 get
        if self._thread:
            self._thread.join(timeout=5)

    # ── 主循环 ──────────────────────────────────────────────

    def _run(self):
        # 模型在 worker 线程内加载，不阻塞 uvicorn 启动
        from transcriber import ensure_model_loaded
        ensure_model_loaded()
        set_state(model_loaded=True)

        while not self._stop_flag:
            try:
                first = self._queue.get(timeout=1)
            except Exception:
                continue
            if first is None:  # stop sentinel
                break

            batch = [first]
            from config import MAX_BATCH_FILES
            while len(batch) < MAX_BATCH_FILES:
                try:
                    item = self._queue.get_nowait()
                    if item is None:
                        break
                    batch.append(item)
                except Exception:
                    break

            todo = [p for p in batch if not Path(p).with_suffix(".json").exists()]
            skip = len(batch) - len(todo)
            if skip:
                print(f"  ⏭ worker: 跳过 {skip} 个已有转写的文件")

            try:
                if todo:
                    self._process(todo)
            finally:
                for p in batch:
                    self._done(p)
                    self._queue.task_done()

    def _process(self, todo: list[str]):
        names = [Path(p).name for p in todo]
        n = len(todo)
        print(f"  🎙️  转写批次: {n} 个文件 — {', '.join(names[:3])}{'...' if n > 3 else ''}")
        set_state(transcribing=(names[0] if n == 1 else f"{n} 个文件"))
        t0 = time.monotonic()
        try:
            pairs = self._transcribe_resilient(todo)
            elapsed = time.monotonic() - t0

            ok = []
            for i, (path, result) in enumerate(pairs):
                if result:
                    write_sidecar(path, result)
                    preview = result["transcript"][:50]
                    suffix = "..." if len(result["transcript"]) > 50 else ""
                    print(f"    ✓ [{i+1}/{n}] {Path(path).name} → \"{preview}{suffix}\"")
                    ok.append((path, result))

            total_dur = sum(r.get("duration_sec", 0) for _, r in ok)
            speed = total_dur / elapsed if elapsed > 0 else 0
            print(f"  ✓ 批次完成: {len(ok)}/{n} 成功, {elapsed:.1f}s, "
                  f"音频 {total_dur:.0f}s, RTF {speed:.1f}x")

            # AI 去噪 + 总结（可选；未配置 LLM 则跳过）
            if ok:
                self._enrich([p for p, _ in ok], [r for _, r in ok])
        finally:
            set_state(transcribing=None)

    def _transcribe_resilient(self, todo: list[str]) -> list:
        """先整批；失败则逐个转写，无法解码的文件写失败边车并跳过。返回 [(path, result|None)]."""
        try:
            results = transcribe_files(todo)
            return list(zip(todo, results))
        except Exception as e:
            print(f"  ⚠ 批量转写失败，改为逐个: {str(e).splitlines()[0][:100]}")

        pairs = []
        for p in todo:
            try:
                pairs.append((p, transcribe_files([p])[0]))
            except Exception as e:
                msg = str(e).splitlines()[0][:120]
                print(f"  ✗ 跳过无法转写的文件: {Path(p).name} — {msg}")
                write_failed_sidecar(p, str(e))
                pairs.append((p, None))
        return pairs

    def _enrich(self, paths: list[str], results: list):
        """转写完成后，逐个调 LLM：原文 → 全文(去噪) → 总结，写回边车。"""
        try:
            from llm import is_configured, enrich
        except Exception as e:
            print(f"  ✗ AI 模块加载失败: {e}")
            return
        if not is_configured():
            return

        from transcriber import update_sidecar
        for path, result in zip(paths, results):
            if not result or not result.get("transcript"):
                continue
            name = Path(path).name
            set_state(transcribing=f"AI 整理 {name}")
            try:
                full, summary, head = enrich(result["transcript"])
                update_sidecar(path, {"full_text": full, "summary": summary, "headline": head})
                print(f"    ✎ AI 整理完成: {name}" + (f" — 「{head}」" if head else ""))
            except Exception as e:
                print(f"    ✗ AI 整理失败: {name} — {e}")
