@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
  echo [!] Missing .venv. Run scripts\setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" localize_images.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
