@echo off
REM No arguments previews offline. --run opts into live monitoring.
REM --run --process also opts into model processing under existing budgets.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup.bat first.
  exit /b 1
)
".venv\Scripts\python.exe" -m pipeline.scheduler %*
exit /b %ERRORLEVEL%
