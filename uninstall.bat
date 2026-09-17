@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" campusnet.py uninstall-startup
  ".venv\Scripts\python.exe" campusnet.py clear-credentials
) else (
  echo Python environment not found; delete the CampusNetAutoLogin scheduled task manually.
)
echo Removed the scheduled task and saved credentials. Project files were kept.
pause
