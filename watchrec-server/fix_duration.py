#!/usr/bin/env python
"""
修复已有 JSON 的 duration_sec。
扫描 uploads/ 下所有 .json，从对应音频文件名解析时长并更新。

用法：
    python fix_duration.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import UPLOAD_DIR

upload_dir = Path(__file__).parent / UPLOAD_DIR


def parse_duration_ms(filename: str) -> float | None:
    """从文件名末尾解析毫秒数，返回秒数。"""
    stem = Path(filename).stem
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        try:
            ms = int(parts[1])
            if ms > 0:
                return round(ms / 1000.0, 2)
        except ValueError:
            pass
    return None


def main():
    json_files = sorted(upload_dir.rglob("*.json"))
    if not json_files:
        print("  没有找到 .json 文件。")
        return

    updated = 0
    skipped = 0

    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        audio_name = data.get("audio_file", "")
        old_dur = data.get("duration_sec")

        new_dur = parse_duration_ms(audio_name)
        if new_dur is None:
            print(f"  ⏭ {jf.relative_to(upload_dir)} — 无法从文件名解析时长: {audio_name}")
            skipped += 1
            continue

        if old_dur and old_dur > 0 and abs(old_dur - new_dur) < 0.1:
            print(f"  ⏭ {jf.relative_to(upload_dir)} — duration_sec 已正确: {new_dur}s")
            skipped += 1
            continue

        data["duration_sec"] = new_dur
        jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ {jf.relative_to(upload_dir)} — {old_dur} → {new_dur}s")
        updated += 1

    print(f"\n  完成：更新 {updated}，跳过 {skipped}，共 {len(json_files)} 个文件。")


if __name__ == "__main__":
    main()
