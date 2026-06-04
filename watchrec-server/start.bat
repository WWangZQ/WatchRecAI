@echo off
REM WatchRec 音频接收服务 — Windows 启动脚本
REM 使用 conda ics 环境
cd /d "%~dp0"
echo === WatchRec Server ===

call conda activate ics
if errorlevel 1 (
    echo [ERROR] 无法激活 conda ics 环境
    pause
    exit /b 1
)

echo   环境: ics
python -c "import torch; print('  GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '不可用')"

python server.py
pause
