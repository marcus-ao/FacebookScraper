@echo off
setlocal
call "%~dp0run_python.bat" -m tools.update_service_address %*
set "UPDATE_RESULT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %UPDATE_RESULT%
