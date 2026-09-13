@echo off
REM ---------------------------------------------------------------------
REM Start the dedicated publishing Chrome profile on its own CDP port.
REM This profile must never be shared with the scraping account.
REM
REM Pure ASCII on purpose. All user-facing non-ASCII output lives in
REM tools\start_chrome_publish.py.
REM ---------------------------------------------------------------------
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."


call "%~dp0run_python.bat" "tools\start_chrome_publish.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
