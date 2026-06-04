#!/usr/bin/env python
"""
批量补转：扫描 uploads/ 下所有 .m4a，对没有对应 .json 的文件批量转写。
复用与 server.py 相同的 worker（单模型 + 批量推理）。

用法：
    python transcribe_all.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import UPLOAD_DIR

upload_dir = Path(__file__).parent / UPLOAD_DIR


def find_pending() -> list[str]:
    """找出所有没有 .json 边车文件的 .m4a 文件路径。"""
    pending = []
    for m4a in sorted(upload_dir.rglob("*.m4a")):
        if not m4a.with_suffix(".json").exists():
            pending.append(str(m4a))
    return pending


def main():
    pending = find_pending()
    if not pending:
        print("  ✓ 所有音频均已转写，无需补转。")
        return

    print(f"  找到 {len(pending)} 个待转写文件：")
    for p in pending:
        print(f"    - {Path(p).relative_to(upload_dir)}")
    print()

    # 初始化 worker 并提交全部文件
    from transcriber import get_worker
    worker = get_worker()
    worker.start()
    worker.submit_batch(pending)

    # 等待队列清空
    print("  等待转写完成...")
    worker.wait_idle()
    print("  ✓ 全部转写完成。")


if __name__ == "__main__":
    main()
