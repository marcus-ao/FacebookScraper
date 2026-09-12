@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup.bat first.
  exit /b 1
)
".venv\Scripts\python.exe" -m tools.start_chrome_detect %*
exit /b %ERRORLEVEL%
