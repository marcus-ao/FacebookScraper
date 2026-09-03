@echo off
REM ---------------------------------------------------------------------
REM Pipeline reconciler entry point (L group). Usage:
REM     run_pipeline.bat                (double click; status, then pauses)
REM     run_pipeline.bat status         (backlog per stage, zero network)
REM     run_pipeline.bat preflight      (what is still missing before go live)
REM     run_pipeline.bat activate --g8-verified
REM     run_pipeline.bat run            (manual/assisted according to config)
REM     run_pipeline.bat approve --item-id ID [--item-id ID]
REM     run_pipeline.bat check-alive    (dead man switch; scheduled task)
REM
REM status is read only. manual run only reconciles local truth sources.
REM assisted run may call delta/translate/images but never opens publish Chrome;
REM approve is the only pipeline command that may submit, after confirmation.
REM check-alive's only side effect is the alert line core\notify.py appends
REM to state\alerts.log (plus a desktop toast).
REM
REM check-alive exit codes: 0 alive / 1 cannot check / 2 dead and alerted.
REM Task Scheduler surfaces this as Last Result, which is a second channel
REM independent of the toast. That is deliberate -- see pipeline\cli.py.
REM
REM status output is NOT redirected: it is a snapshot you read right now.
REM check-alive output IS appended to state\pipeline.log, because that one
REM runs unattended and the previous run is often the only evidence left.
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM Every Chinese message lives in pipeline\cli.py.
REM ---------------------------------------------------------------------
setlocal
REM Console code page here is 936. check-alive output is redirected into a
REM file below, so Python would fall back to GBK and die on the first
REM non-encodable character. Not hypothetical; see core\console.py.
set "PYTHONIOENCODING=utf-8"
REM scripts\ lives one level below the project root -- go up first.
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run setup.bat first.
  exit /b 1
)

if not exist "state" mkdir "state"

REM Labels instead of parenthesised blocks: %ERRORLEVEL% inside a block is
REM expanded when the block is parsed, not when it runs, so the captured
REM code would always be the one from before the command.
if /i "%~1"=="check-alive" goto :alive
if /i "%~1"=="run" goto :run
if "%~1"=="" goto :interactive

".venv\Scripts\python.exe" -m pipeline %*
exit /b %ERRORLEVEL%

:alive
".venv\Scripts\python.exe" -m pipeline %* >> "state\pipeline.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\pipeline.log"
exit /b %RC%

:run
".venv\Scripts\python.exe" -m pipeline %* >> "state\pipeline.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [exit=%RC%]>> "state\pipeline.log"
exit /b %RC%

:interactive
".venv\Scripts\python.exe" -m pipeline status
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
