@echo off
REM No arguments previews offline. --run opts into live monitoring.
REM --run --process also opts into model processing under existing budgets.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
call "%~dp0run_python.bat" -m pipeline.scheduler %*
exit /b %ERRORLEVEL%
