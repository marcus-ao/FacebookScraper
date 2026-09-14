@echo off
REM --check reads evidence; --emit writes the verified signal registry.
setlocal
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0.."

call "%~dp0run_python.bat" "tools\_scaffolding\probe_signals.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
