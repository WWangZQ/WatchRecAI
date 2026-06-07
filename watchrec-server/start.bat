@echo off
cd /d "%~dp0"
echo === WatchRec PC ===

call "%USERPROFILE%\miniconda3\Scripts\activate.bat" ics 2>nul
if errorlevel 1 (
    call "D:\ProgramData\miniconda3\Scripts\activate.bat" ics 2>nul
)
if errorlevel 1 (
    echo [ERROR] Cannot activate conda ics env
    pause
    exit /b 1
)

if not exist .env (
    echo [ERROR] .env not found. Copy .env.example and fill in APP_TOKEN.
    pause
    exit /b 1
)

python server.py
pause
