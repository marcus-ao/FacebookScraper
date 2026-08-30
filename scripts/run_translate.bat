@echo off
REM ---------------------------------------------------------------------
REM German translation entry point. Implementation plan group F.
REM
REM Usage:
REM     run_translate.bat --check          verify the API gateway config
REM     run_translate.bat --limit 3        translate 3 posts (trial run)
REM     run_translate.bat                  translate everything untranslated
REM     run_translate.bat --review         build the human review checklist
REM
REM Prerequisite: setup.bat has been run, and the API key environment
REM variable named in config.toml [translate].api_key_env is set.
REM
REM Pure ASCII on purpose -- see the comment block in setup.bat.
REM All logic and all Chinese output live in translate.py.
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

".venv\Scripts\python.exe" "translate.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
