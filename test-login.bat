@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" campusnet.py run --show-browser
echo.
if errorlevel 1 (
  echo Login failed. Check the log or edit advanced selectors.
) else (
  echo Login succeeded, or the network was already online.
)
pause
