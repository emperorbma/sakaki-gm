@echo off
REM Headless campaign table server (no Tk lobby or desk).
setlocal
cd /d "%~dp0"
call "%~dp0start-sakaki-gm.bat" --server %*
exit /b %errorlevel%
