@echo off
setlocal
rem DeepQuant one-click launcher (Windows).
rem Keep this file ASCII-only: the console codepage may not be UTF-8.
cd /d "%~dp0"

set "PY="
if exist "%~dp0..\venv\Scripts\python.exe" set "PY=%~dp0..\venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo [DeepQuant] python = %PY%
"%PY%" "%~dp0scripts\start_deepquant.py" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [DeepQuant] exited with code %CODE%
  pause
)
endlocal
