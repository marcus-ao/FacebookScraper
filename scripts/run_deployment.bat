@echo off
setlocal
call "%~dp0run_python.bat" -m deployment %*
exit /b %errorlevel%
