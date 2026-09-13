@echo off
setlocal
call "%~dp0run_python.bat" -m uvicorn web.api.app:app --host 127.0.0.1 --port 8000 %*
exit /b %ERRORLEVEL%
