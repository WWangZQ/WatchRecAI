#!/usr/bin/env python
"""
批量补转：扫描 uploads/ 下所有 .m4a，对没有对应 .json 的文件逐个转写。

用法：
    python transcribe_all.py
"""

import sys
from pathlib import Path

# 确保从项目目录导入 config
sys.path.insert(0, str(Path(__file__).parent))

from config import UPLOAD_DIR
from transcriber import transcribe_and_save

upload_dir = Path(__file__).parent / UPLOAD_DIR


def find_pending() -> list[Path]:
    """找出所有没有 .json 边车文件的 .m4a 文件。"""
    pending = []
    for m4a in sorted(upload_dir.rglob("*.m4a")):
        json_file = m4a.with_suffix(".json")
        if not json_file.exists():
            pending.append(m4a)
    return pending


def main():
    pending = find_pending()
    if not pending:
        print("  ✓ 所有音频均已转写，无需补转。")
        return

    print(f"  找到 {len(pending)} 个待转写文件：")
    for f in pending:
        print(f"    - {f.relative_to(upload_dir)}")
    print()

    success = 0
    failed = 0
    for i, m4a in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] {m4a.relative_to(upload_dir)}")
        try:
            result = transcribe_and_save(str(m4a))
            if result:
                success += 1
        except Exception as e:
            print(f"  ✗ 转写失败: {e}")
            failed += 1

    print()
    print(f"  完成：成功 {success}，失败 {failed}，共 {len(pending)} 个文件。")


if __name__ == "__main__":
    main()
