@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
call "%~dp0run_python.bat" -m localize.text %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
