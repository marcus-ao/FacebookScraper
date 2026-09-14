@echo off
REM No arguments: run, show log, and pause. Scheduled calls must pass arguments.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

if not exist "state" mkdir "state"

call "%~dp0run_python.bat" -m routes.delta %* >> "state\delta.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\delta.log"

if "%~1"=="" (
  powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 40 'state\delta.log'"
  echo.
  echo Full log: state\delta.log
  pause
)
exit /b %RC%
