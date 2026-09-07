"""Writable data is separate from the executable and other editions."""
import os
import sys
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent
if os.environ.get("WATCHREC_HOME"):
    DATA_HOME = Path(os.environ["WATCHREC_HOME"]).expanduser().resolve()
elif getattr(sys, "frozen", False):
    DATA_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WatchRecOpenSource"
else:
    DATA_HOME = RESOURCE_DIR / "data"
DATA_HOME.mkdir(parents=True, exist_ok=True)
