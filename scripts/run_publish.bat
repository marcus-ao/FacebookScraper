@echo off
REM Offline content preview; no browser actions.
setlocal
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0.."

call "%~dp0run_python.bat" "tools\compose_publish.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
