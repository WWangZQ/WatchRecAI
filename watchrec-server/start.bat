@echo off
cd /d "%~dp0"
echo === WatchRec Server ===

call "%USERPROFILE%\miniconda3\Scripts\activate.bat" ics 2>nul
if errorlevel 1 (
    call "D:\ProgramData\miniconda3\Scripts\activate.bat" ics 2>nul
)
if errorlevel 1 (
    echo [ERROR] Cannot activate conda ics env
    pause
    exit /b 1
)

echo Env: ics
python -c "import torch; print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

python server.py
pause
