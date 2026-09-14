@echo off
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

if not exist "state" mkdir "state"

REM Labels preserve the command exit code without block-time expansion.
if /i "%~1"=="check-alive" goto :alive
if /i "%~1"=="run" goto :run
if "%~1"=="" goto :interactive

call "%~dp0run_python.bat" -m pipeline %*
exit /b %ERRORLEVEL%

:alive
call "%~dp0run_python.bat" -m pipeline %* >> "state\pipeline.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\pipeline.log"
exit /b %RC%

:run
call "%~dp0run_python.bat" -m pipeline %* >> "state\pipeline.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\pipeline.log"
exit /b %RC%

:interactive
call "%~dp0run_python.bat" -m pipeline status
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
