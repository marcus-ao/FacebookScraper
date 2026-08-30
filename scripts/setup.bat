@echo off
REM ---------------------------------------------------------------------
REM One-time environment setup (Windows). Implementation plan task A1.
REM
REM This file is deliberately pure ASCII. cmd.exe mis-parses non-ASCII
REM characters in .bat files: a line gets split part-way through and the
REM tail is executed as a command. Reproduced on this machine both with
REM and without "chcp 65001". All Chinese output therefore lives in
REM tools\setup.py, where Python writes to the console via the Unicode
REM API and renders correctly under any code page.
REM
REM Keep this file ASCII-only and CRLF-terminated.
REM ---------------------------------------------------------------------
setlocal
REM Console code page here is 936; a redirected stdout would fall back to
REM GBK and crash on the first non-encodable character. See core\console.py.
set "PYTHONIOENCODING=utf-8"
REM scripts\ lives one level below the project root -- go up first.
cd /d "%~dp0.."

REM Bootstrap with a system Python: the venv may not exist yet, and
REM setup.py might need to recreate it.
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
