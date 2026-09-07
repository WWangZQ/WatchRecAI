"""Collect installed distribution license/notice files for the desktop bundle."""
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
dest = root / "dist" / "WatchRec" / "third-party-licenses"
dest.mkdir(parents=True, exist_ok=True)
versions = []
for package in importlib.metadata.distributions():
    name = package.metadata.get("Name", "unknown")
    versions.append(f"{name}=={package.version}")
    for f in package.files or []:
        if (".dist-info/" in str(f) or ".egg-info/" in str(f)) and any(s in f.name.lower() for s in ("license", "copying", "notice", "authors")):
            source = Path(package.locate_file(f))
            if source.is_file():
                target = dest / name / str(f).split("/", 1)[-1]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
(dest / "build-environment.txt").write_text("\n".join(sorted(versions)) + "\n", encoding="utf-8")
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.exists():
    python_license = Path(sys.base_prefix) / "LICENSE"
if python_license.is_file(): shutil.copyfile(python_license, dest / "Python-LICENSE.txt")
ffmpeg = Path(shutil.which("ffmpeg"))
ffmpeg_root = ffmpeg.parent.parent
if (ffmpeg_root / "LICENSE").is_file():
    shutil.copyfile(ffmpeg_root / "LICENSE", dest / "FFmpeg-LICENSE.txt")
if (ffmpeg_root / "README.txt").is_file():
    shutil.copyfile(ffmpeg_root / "README.txt", dest / "FFmpeg-README.txt")
(dest / "ffmpeg-version.txt").write_text(subprocess.check_output([str(ffmpeg), "-version"], text=True, errors="replace"), encoding="utf-8")
