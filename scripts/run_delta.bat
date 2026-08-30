@echo off
REM ---------------------------------------------------------------------
REM Daily delta entry point. Usage:
REM     run_delta.bat                  (double click; runs now, then pauses)
REM     run_delta.bat --if-stale       (what the scheduled task calls)
REM     run_delta.bat --status         (no network, just prints state)
REM     run_delta.bat --reset-failures (after you fixed whatever broke)
REM
REM All output is APPENDED to state\delta.log. Never overwritten:
REM this path runs unattended, and the previous run's output is often
REM the only evidence of why today's run behaves the way it does.
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM Every Chinese message lives in routes\delta.py.
REM ---------------------------------------------------------------------
setlocal
REM Console code page here is 936. Output is redirected into a file below,
REM so Python falls back to GBK and dies on the first non-encodable
REM character -- after the network request, before the archive write.
REM This is not hypothetical; it was measured. See core\console.py.
set "PYTHONIOENCODING=utf-8"
REM scripts\ lives one level below the project root -- go up first.
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup.bat first.
  exit /b 1
)

if not exist "state" mkdir "state"

REM The run header (timestamp) is printed by Python, not by echo:
REM cmd writes %DATE% in the console code page, which would put GBK bytes
REM in the middle of an otherwise UTF-8 log file.
".venv\Scripts\python.exe" -m routes.delta %* >> "state\delta.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\delta.log"

REM No arguments means somebody double clicked it: show the tail and wait.
REM The scheduled task always passes --if-stale, so it never pauses here.
if "%~1"=="" (
  powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 40 'state\delta.log'"
  echo.
  echo Full log: state\delta.log
  pause
)
exit /b %RC%
