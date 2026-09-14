@echo off
REM Default: offline preview. --run accesses social accounts; --process permits budgeted processing.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
call "%~dp0run_python.bat" -m pipeline.scheduler %*
exit /b %ERRORLEVEL%
