@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo J.A.R.V.I.S Dependency Installer
echo ========================================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
  echo Using Python launcher: py -3
  py -3 -m pip install --upgrade pip
  py -3 -m pip install -r requirements.txt
  goto :done
)
where python >nul 2>&1
if %errorlevel%==0 (
  echo Using python
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  goto :done
)
echo ERROR: Python was not found on PATH.
echo Install Python 3.11 or newer from python.org and enable "Add Python to PATH".
pause
exit /b 1
:done
echo.
echo Dependencies installed successfully.
echo You can now run run_jarvis.bat or run_jarvis.vbs.
pause
