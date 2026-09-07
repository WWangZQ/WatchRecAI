@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first to install the independent Python environment.
    pause
    exit /b 1
)
.venv\Scripts\python.exe desktop.py
if errorlevel 1 pause
