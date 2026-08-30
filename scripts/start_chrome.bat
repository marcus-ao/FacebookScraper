@echo off
REM ---------------------------------------------------------------------
REM Start the dedicated Chrome profile with a debugging port open.
REM Implementation plan task A3.
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM All logic and all Chinese output live in tools\start_chrome.py, which
REM reads the Chrome path, profile dir and debug port from config.toml.
REM There is no second copy of those values to keep in sync any more.
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

".venv\Scripts\python.exe" "tools\start_chrome.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
