@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

call "%~dp0run_python.bat" "tools\start_chrome.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
