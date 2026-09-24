@echo off
REM Same-line CALL clears cmd's Ctrl+C flag so it does not ask "Terminate batch job (Y/N)?".
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" goto :venv
py -3 tools\runtime.py exec %* & call set "RC=%%ERRORLEVEL%%" & call;
exit /b %RC%
:venv
".venv\Scripts\python.exe" tools\runtime.py exec %* & call set "RC=%%ERRORLEVEL%%" & call;
exit /b %RC%
