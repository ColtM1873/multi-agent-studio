@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo =================================================
echo   Multi-Agent Studio 安装版本选择
echo =================================================
echo.
echo   [1] 普通版（推荐，功能完整）
echo       约需 9 GB 存储空间：包含本地 embedding 模型与运行库，
echo       支持长期记忆、记忆读写、记忆吸附与记忆库管理。
echo.
echo   [2] Lite 轻量版
echo       占用更少：无需下载 embedding 模型，运行库也更小。
echo       不含长期记忆、记忆读写、记忆吸附与记忆库管理；
echo       其余功能与普通版一致。
echo.
choice /C 12 /N /M "请选择安装版本 [1=普通版, 2=Lite]: "
if errorlevel 255 exit /b 1
if errorlevel 2 (
    set "MODE=Lite"
) else (
    set "MODE=Full"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Mode %MODE%
pause
