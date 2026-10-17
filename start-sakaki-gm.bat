@echo off
setlocal
cd /d "%~dp0"

set "PYLAUNCHER="
where py >nul 2>&1 && set "PYLAUNCHER=py -3"
if not defined PYLAUNCHER (
  where python >nul 2>&1 && set "PYLAUNCHER=python"
)
if not defined PYLAUNCHER (
  echo Python 3 was not found on PATH. Install Python from python.org and try again.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  %PYLAUNCHER% -m venv .venv
  if errorlevel 1 exit /b 1
)

set "DOINSTALL=0"
if not exist ".venv\requirements.stamp" set "DOINSTALL=1"
if "%DOINSTALL%"=="0" (
  .venv\Scripts\python.exe -c "import os,sys; stamp=os.path.getmtime('.venv/requirements.stamp'); sys.exit(0 if os.path.getmtime('requirements.txt')<=stamp and os.path.getmtime('pyproject.toml')<=stamp else 1)"
  if errorlevel 1 set "DOINSTALL=1"
)
if "%DOINSTALL%"=="1" (
  .venv\Scripts\python.exe -m pip install -U pip
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -c "import pathlib; pathlib.Path('.venv/requirements.stamp').touch()"
)

.venv\Scripts\python.exe -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo Tkinter is missing from this Python. Reinstall Python and include Tcl/Tk.
  exit /b 1
)

.venv\Scripts\python.exe -m sakaki_gm
exit /b %errorlevel%
