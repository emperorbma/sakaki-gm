@echo off
REM Host a campaign table server (passes --server through to Sakaki GM).
setlocal
cd /d "%~dp0"
call "%~dp0start-sakaki-gm.bat" --server %*
exit /b %errorlevel%
