@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Keep this file ASCII-only. cmd.exe misparses UTF-8 batch files with many
rem non-ASCII lines (its parser drifts and executes fragments of commands),
rem which made setup.bat flash and exit. All Chinese UI lives in setup.ps1
rem (UTF-8 with BOM), which PowerShell reads correctly.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
