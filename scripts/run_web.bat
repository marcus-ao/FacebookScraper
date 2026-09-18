@echo off
setlocal
REM Release packages already contain a verified frontend and do not require Node.
if exist "%~dp0..\release.json" goto start_web
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo Node.js with npm is required to start the web UI from a source checkout.
  echo Install Node.js, reopen the terminal, then run this script again.
  exit /b 1
)
REM dist is not tracked by Git: always rebuild before serving a source checkout.
echo Preparing frontend from the current source...
call npm.cmd --prefix "%~dp0..\web\ui" ci --include=dev --no-audit --no-fund
if errorlevel 1 goto frontend_failed
call npm.cmd --prefix "%~dp0..\web\ui" run build
if errorlevel 1 goto frontend_failed

:start_web
call "%~dp0run_python.bat" -m uvicorn web.api.app:app --host 127.0.0.1 --port 8765 %*
exit /b %ERRORLEVEL%

:frontend_failed
echo Frontend preparation failed. The web server was not started.
echo Fix the npm error above and run this script again.
exit /b 1
