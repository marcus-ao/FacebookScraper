@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
call "%~dp0run_python.bat" -m tools.start_chrome_detect %*
exit /b %ERRORLEVEL%
