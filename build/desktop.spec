# Build with the dedicated environment; never collect the app's runtime data.
from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
app = root / "watchrec-server"
datas = [(str(app / "app"), "app")]
hiddenimports = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                 "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "tiktoken_ext.openai_public"]
for package in ("funasr", "modelscope", "librosa", "hydra", "omegaconf"):
    datas += collect_data_files(package, include_py_files=True)
    hiddenimports += collect_submodules(package)
for package in ("funasr", "modelscope", "torch", "torchaudio", "transformers", "numpy", "soundfile", "requests", "tqdm", "omegaconf", "hydra-core"):
    datas += copy_metadata(package)
for command in ("ffmpeg", "ffprobe"):
    path = shutil.which(command)
    if not path:
        raise RuntimeError(f"{command} must be on PATH before building")
    datas.append((path, "bin"))

a = Analysis([str(app / "desktop.py")], pathex=[str(app)], binaries=[], datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["playwright", "pytest", "IPython", "jupyter", "notebook", "tensorflow"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WatchRec", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="WatchRec")
