@echo off
REM Keep batch wrappers ASCII with CRLF; user-facing output belongs in Python.
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

where py >nul 2>&1
if not errorlevel 1 (set "BOOT=py -3" & goto RUN)
where python >nul 2>&1
if not errorlevel 1 (set "BOOT=python" & goto RUN)

echo [!] No Python found on PATH.
echo     Install Python 3.11+ and tick "Add python.exe to PATH", or install uv.
pause
exit /b 1

:RUN
%BOOT% "tools\setup.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
