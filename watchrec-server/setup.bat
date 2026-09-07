@echo off
cd /d "%~dp0"
echo Requires Python 3.10 or 3.11 on PATH. Creates a local .venv only.
python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,11), 'Use Python 3.10 or 3.11'"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Done. Install ffmpeg if needed, then double-click start.bat.
pause
exit /b 0
:fail
echo Setup failed. Read the message above. Your other Python environments were not changed.
pause
exit /b 1
