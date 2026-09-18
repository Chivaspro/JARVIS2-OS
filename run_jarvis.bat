@echo off
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>&1
if %errorlevel%==0 set "PY=py -3"
if not defined PY (
  where python >nul 2>&1
  if %errorlevel%==0 set "PY=python"
)
if not defined PY (
  echo Python was not found.
  echo Install Python 3.11+ and enable Add Python to PATH.
  pause
  exit /b 1
)
%PY% -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
  echo PyQt6 is not installed. Installing project dependencies...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Dependency installation failed. See the error above.
    pause
    exit /b 1
  )
)
%PY% main.py
if errorlevel 1 (
  echo.
  echo J.A.R.V.I.S exited with an error.
  echo Check jarvis_startup_error.log for details.
  pause
)
