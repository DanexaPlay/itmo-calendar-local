@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. Read the error above.
  pause
  exit /b 1
)
echo.
echo Next step: FIRST_RUN.cmd
pause
