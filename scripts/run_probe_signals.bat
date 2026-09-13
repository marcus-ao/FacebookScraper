@echo off
REM ---------------------------------------------------------------------
REM Derive the G6/G6c production evidence from a v2 probe dump.
REM
REM   run_probe_signals.bat --status
REM       Is the production submit gate open? If not, what is missing?
REM
REM   run_probe_signals.bat --check state\publish_probe_<stamp>.json
REM       Read-only triage. Run this FIRST, right after recording.
REM       It tells you within seconds whether the recording is usable.
REM
REM   run_probe_signals.bat --emit state\publish_probe_<stamp>.json
REM       Derive, re-verify every item against the dump, then write
REM       publish\signals_backfilled.py. Refuses to write anything that
REM       does not verify.
REM
REM Optional: --caption "a few words from the test post"
REM           --success-name "scheduled"
REM
REM Never touches a browser, never sends a request, never posts anything.
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM ---------------------------------------------------------------------
setlocal
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0.."


call "%~dp0run_python.bat" "tools\_scaffolding\probe_signals.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
