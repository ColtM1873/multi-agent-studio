@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Install first:
    echo   1. python -m venv venv
    echo   2. venv\Scripts\activate
    echo   3. pip install -r requirements.txt
    echo Re-run this script after installation.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" build_exe.py
pause
