@echo off
REM Bootstrap reads the local interpreter binding before executing.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\runtime.py exec %*
) else (
  py -3 tools\runtime.py exec %*
)
exit /b %ERRORLEVEL%
