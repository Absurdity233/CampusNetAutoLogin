@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" campusnet.py uninstall-startup
  ".venv\Scripts\python.exe" campusnet.py clear-credentials
) else (
  echo Python environment not found; remove CampusNetAutoLogin from HKCU Run manually.
)
echo Uninstalled startup entry and saved credentials. Project files were kept.
pause
