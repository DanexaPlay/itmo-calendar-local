@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\first-run.ps1"
if errorlevel 1 echo First-run wizard stopped. Check the error above.
pause
