@echo off
setlocal
cd /d "%~dp0"

rem Self-heal: some downloaders / cloud-drive clients / sandbox extractors mark the
rem folder as Windows "Low" integrity, which is inherited by every file. Executables
rem then run at Low integrity and cannot write to %TEMP% / %LOCALAPPDATA%, so the
rem onefile launcher fails with "Could not create temporary directory!". If the Low
rem label is present, reset the whole tree to Medium before starting.
icacls . 2>nul | findstr /i /c:"Low Mandatory Level" >nul
if errorlevel 1 goto launch

echo Applying integrity fix ^(Low -^> Medium^)...
icacls . /setintegritylevel ^(OI^)^(CI^)M /T /C >nul 2>&1

:launch
start "" "%~dp0MultiAgentStudio.exe"
endlocal
