"""
统一转写 worker：单队列 + 单线程 + 批处理。

LAN 上传和 VPS 下载的音频走同一条路，同一个模型实例，零并发风险。
模型在 worker 线程内首次使用时加载（不阻塞 uvicorn 启动）。
"""

import threading
from pathlib import Path
from queue import Queue

from transcriber import transcribe_files, write_sidecar


class TranscribeWorker:
    def __init__(self):
        self._queue: Queue = Queue()
        self._thread: threading.Thread | None = None
        self._stop_flag = False

    def start(self):
        if self._thread is not None:
            return
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="transcribe-worker")
        self._thread.start()

    def submit(self, audio_path: str):
        self._queue.put(audio_path)

    def wait_idle(self):
        self._queue.join()

    def stop(self):
        self._stop_flag = True
        # put sentinel to wake the blocking get
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        """常驻循环：阻塞等任务 → 凑批 → 批量转写 → 写 JSON。"""
        # 模型在 worker 线程内加载，不阻塞 uvicorn 启动
        from transcriber import ensure_model_loaded
        ensure_model_loaded()

        while not self._stop_flag:
            # 1. 阻塞等第一个任务
            try:
                first = self._queue.get(timeout=1)
            except Exception:
                continue
            if first is None:  # stop sentinel
                break

            batch = [first]

            # 2. 非阻塞 drain 凑批
            from config import MAX_BATCH_FILES
            while len(batch) < MAX_BATCH_FILES:
                try:
                    item = self._queue.get_nowait()
                    if item is None:
                        break
                    batch.append(item)
                except Exception:
                    break

            # 3. 过滤已有边车的文件
            todo = [p for p in batch if not Path(p).with_suffix(".json").exists()]
            skip_count = len(batch) - len(todo)

            if skip_count:
                print(f"  ⏭ worker: 跳过 {skip_count} 个已有转写的文件")

            if not todo:
                for _ in batch:
                    self._queue.task_done()
                continue

            # 4. 批量转写
            names = [Path(p).name for p in todo]
            n = len(todo)
            print(f"  🎙️  转写批次: {n} 个文件 — {', '.join(names[:3])}{'...' if n > 3 else ''}")

            import time
            t0 = time.monotonic()
            try:
                results = transcribe_files(todo)
                elapsed = time.monotonic() - t0

                for i, (path, result) in enumerate(zip(todo, results)):
                    if result:
                        write_sidecar(path, result)
                        preview = result["transcript"][:50]
                        suffix = "..." if len(result["transcript"]) > 50 else ""
                        print(f"    ✓ [{i+1}/{n}] {Path(path).name} → \"{preview}{suffix}\"")

                total_dur = sum(r.get("duration_sec", 0) for r in results if r)
                speed = total_dur / elapsed if elapsed > 0 else 0
                print(f"  ✓ 批次完成: {n} 个文件, {elapsed:.1f}s, "
                      f"音频 {total_dur:.0f}s, RTF {speed:.1f}x")
            except Exception as e:
                print(f"  ✗ 批次转写失败: {e}")

            finally:
                for _ in batch:
                    self._queue.task_done()
