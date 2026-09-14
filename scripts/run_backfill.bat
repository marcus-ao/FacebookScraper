@echo off
REM Requires the scraping account to be logged in via start_chrome.bat.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

call "%~dp0run_python.bat" -m routes.backfill %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
