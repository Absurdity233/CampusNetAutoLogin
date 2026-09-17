@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Creating Python environment...
if exist ".venv\Scripts\python.exe" (
  echo Using existing environment.
) else (
  python -m venv .venv 2>nul
  if errorlevel 1 py -3 -m venv .venv
  if errorlevel 1 goto :error
)

echo [2/4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install --upgrade --force-reinstall -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -c "import greenlet; import playwright.sync_api"
if errorlevel 1 goto :error

echo [3/4] Configuring campus network account...
".venv\Scripts\python.exe" campusnet.py setup
if errorlevel 1 goto :error

echo [4/4] Enabling Windows startup...
".venv\Scripts\python.exe" campusnet.py install-startup
if errorlevel 1 goto :error

echo.
echo Installation finished. Run test-login.bat while connected to campus Wi-Fi.
pause
exit /b 0

:error
echo.
echo Installation failed. See the error above.
pause
exit /b 1
