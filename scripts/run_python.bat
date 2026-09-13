@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
REM Bootstrap only reads local interpreter binding; the project .venv runs the command.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\runtime.py exec %*
) else (
  py -3 tools\runtime.py exec %*
)
exit /b %ERRORLEVEL%
