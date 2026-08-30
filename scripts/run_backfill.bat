@echo off
REM ---------------------------------------------------------------------
REM Backfill entry point. Usage:
REM     run_backfill.bat facebook
REM     run_backfill.bat instagram
REM
REM Prerequisite: start_chrome.bat has been run and the scraping account
REM is logged in inside that window.
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM Argument validation and all Chinese output live in routes\backfill.py.
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

".venv\Scripts\python.exe" -m routes.backfill %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
