"""Create clean distributable archives from explicit source folders."""
from pathlib import Path
import hashlib
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION = "1.1.0-preview"
DIST.mkdir(exist_ok=True)
excluded_parts = {".git", ".gradle", "__pycache__", ".venv", "data", "downloads", "uploads", ".pytest_cache"}
excluded_names = {"local.properties", "settings.json", ".env", "server.crt"}


def sources():
    for folder in ("watchrec-server", "watchrec-vps", "watchrec-watch", "docs", "build", "tests"):
        for p in (ROOT / folder).rglob("*"):
            if not p.is_file(): continue
            rel = p.relative_to(ROOT)
            if any(part in excluded_parts for part in rel.parts): continue
            if rel.parts[0] == "watchrec-watch" and "build" in rel.parts: continue
            if p.name in excluded_names or p.suffix.lower() in {".log", ".pyc", ".jks", ".keystore", ".m4a", ".wav", ".mp3", ".mp4", ".pem", ".key", ".crt"}: continue
            if p.name.startswith(".env") and p.name != ".env.example": continue
            yield p
    for name in ("README.md", ".gitignore", ".gitattributes", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
        yield ROOT / name


def archive(path, files, base):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(files):
            z.write(p, p.relative_to(base).as_posix())


def main():
    files = list(sources())
    archive(DIST / f"WatchRec-Source-{VERSION}.zip", files, ROOT)
    archive(DIST / f"WatchRec-VPS-{VERSION}.zip", [p for p in files if p.is_relative_to(ROOT / "watchrec-vps") or p.is_relative_to(ROOT / "docs") or p.name in {"README.md", "THIRD_PARTY_NOTICES.md"}], ROOT)
    apk = ROOT / "watchrec-watch/app/build/outputs/apk/debug/app-debug.apk"
    if not apk.is_file(): raise SystemExit("Build the watch APK first")
    shutil.copyfile(apk, DIST / f"WatchRec-Watch-{VERSION}.apk")
    desktop = DIST / "WatchRec"
    if not (desktop / "WatchRec.exe").is_file(): raise SystemExit("Build Windows desktop first")
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, desktop / name)
    shutil.copytree(ROOT / "docs", desktop / "docs", dirs_exist_ok=True)
    (desktop / "watchrec-vps").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "watchrec-vps/README.md", desktop / "watchrec-vps/README.md")
    archive(DIST / f"WatchRec-Windows-{VERSION}.zip", [p for p in desktop.rglob("*") if p.is_file()], DIST)
    outputs = sorted([p for p in DIST.iterdir() if p.suffix in {".zip", ".apk"}])
    lines = []
    for p in outputs:
        digest = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""): digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {p.name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(f"{p.name}: {p.stat().st_size / 1024 / 1024:.1f} MiB" for p in outputs))


if __name__ == "__main__":
    main()
