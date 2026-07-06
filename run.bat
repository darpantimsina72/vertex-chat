@echo off
rem Vertex Chat — Windows launcher. Double-click this file.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python start.py
    goto :end
)

echo.
echo Python not found. Install it from https://www.python.org/downloads/
echo IMPORTANT: tick "Add python.exe to PATH" during install, then run this again.
echo.

:end
pause
