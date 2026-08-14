@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---------- 检测管理员权限 ----------
net session >nul 2>&1
if %errorlevel% neq 0 goto :need_admin
goto :admin_ok

:need_admin
echo.
echo ================================================
echo  [需要管理员权限]
echo  本安装需要启用 Windows「长路径支持」，
echo  否则安装 torch 时会报「路径过长」错误。
echo  即将弹出「用户账户控制」窗口，请点击「是」。
echo ================================================
echo.
pause
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:admin_ok
echo.
echo ================================================
echo  Multi-Agent Studio 一键安装
echo ================================================
echo.

rem ---------- 1. 检查 Python ----------
python --version >nul 2>&1
if %errorlevel% neq 0 goto :no_python
echo [1/5] 检测到 Python：
python --version

rem ---------- 2. 启用长路径支持（硬性前置） ----------
echo [2/5] 启用 Windows 长路径支持...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
if %errorlevel% neq 0 goto :longpath_fail

reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled | findstr /i "0x1" >nul 2>&1
if %errorlevel% neq 0 goto :longpath_fail
echo       长路径支持已启用。

rem ---------- 3. 创建虚拟环境 ----------
echo [3/5] 创建虚拟环境 venv...
if not exist venv python -m venv venv
if %errorlevel% neq 0 goto :venv_fail

rem ---------- 4. 安装依赖 ----------
echo [4/5] 安装依赖（清华源，首次约需几分钟）...
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 goto :pip_fail

rem ---------- 5. 打包 exe ----------
echo [5/5] 打包 MultiAgentStudio.exe（首次会自动安装打包工具）...
venv\Scripts\python.exe build_exe.py
if %errorlevel% neq 0 goto :build_fail

echo.
echo ================================================
echo  安装完成！已生成 MultiAgentStudio.exe。
echo  现在双击它即可启动。
echo ================================================
echo.
pause
exit /b 0

:no_python
echo.
echo [错误] 未检测到 Python！
echo 请先到 https://www.python.org/downloads/ 安装 Python 3.13，
echo 安装时务必勾选 "Add python.exe to PATH"，然后重新运行本脚本。
echo.
pause
exit /b 1

:longpath_fail
echo.
echo [错误] 无法启用 Windows 长路径支持（注册表写入失败）。
echo 请确认已以管理员身份运行本脚本后重试。
echo.
pause
exit /b 1

:venv_fail
echo.
echo [错误] 创建虚拟环境失败，请截图上方报错信息反馈。
echo.
pause
exit /b 1

:pip_fail
echo.
echo [错误] 依赖安装失败，请截图上方完整报错信息反馈。
echo.
pause
exit /b 1

:build_fail
echo.
echo [错误] 打包 exe 失败，请截图上方完整报错信息反馈。
echo.
pause
exit /b 1
