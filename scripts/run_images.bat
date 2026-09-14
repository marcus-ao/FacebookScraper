@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
call "%~dp0run_python.bat" -m localize.images %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
