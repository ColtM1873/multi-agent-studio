@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到 venv，请先完成安装步骤：
    echo   1. python -m venv venv
    echo   2. venv\Scripts\activate
    echo   3. pip install -r requirements.txt
    echo 安装完成后再双击本脚本。
    pause
    exit /b 1
)

"venv\Scripts\python.exe" build_exe.py
pause
