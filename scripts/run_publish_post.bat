@echo off
REM Prepares a browser draft; --submit permits one verified submission.
setlocal
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0.."

call "%~dp0run_python.bat" "tools\publish_post.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
