@echo off
REM ---------------------------------------------------------------------
REM Fill one German post into Business Suite. Default: STOP before submit.
REM Plan group G (G2-G7). See docs\PUBLISH_PLAN.md.
REM
REM This DOES drive the browser. It presses submit only with explicit
REM --submit, which also forces the reviewed G1/v2 evidence gates.
REM
REM Usage:
REM     run_publish_post.bat --post-id 122123185335379375 --at 2026-09-08T10:00
REM     run_publish_post.bat --post-id 122123185335379375 --at 2026-09-08T10:00 --submit
REM     run_publish_post.bat --post-id 122123185335379375 --mark-scheduled
REM
REM Prerequisites:
REM   1. start_chrome_publish.bat is running and the DE account is logged in;
REM   2. the post already has a current German translation;
REM   3. [publish].ui_timezone is filled in from a real G1 probe.
REM
REM Offline preview only (no browser): run_publish.bat
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM All logic and all Chinese output live in tools\publish_post.py.
REM ---------------------------------------------------------------------
setlocal
REM Console code page here is 936; a redirected stdout would fall back to
REM GBK and crash on the first non-encodable character. See core\console.py.
set "PYTHONIOENCODING=utf-8"

REM scripts\ lives one level below the project root -- go up first.
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "tools\publish_post.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
