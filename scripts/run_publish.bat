@echo off
REM ---------------------------------------------------------------------
REM Offline compose preview for the German posts. Plan group G (G0b).
REM
REM This NEVER touches a browser, never sends a request and never writes
REM a file. It only runs the offline gates and prints what would be sent.
REM
REM Usage:
REM     run_publish.bat --latest 3
REM     run_publish.bat --post-id 122123185335379375 --at 2026-09-05T10:00
REM     run_publish.bat --latest 3 --strict     require a verified G1 probe
REM
REM Prerequisite: setup.bat has been run, and the posts already have a
REM current German translation (run_translate.bat).
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM All logic and all Chinese output live in tools\compose_publish.py.
REM ---------------------------------------------------------------------
setlocal
REM Console code page here is 936; a redirected stdout would fall back to
REM GBK and crash on the first non-encodable character. See core\console.py.
set "PYTHONIOENCODING=utf-8"

REM scripts\ lives one level below the project root -- go up first.
cd /d "%~dp0.."


call "%~dp0run_python.bat" "tools\compose_publish.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
