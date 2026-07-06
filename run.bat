@echo off
rem Vertex Chat - Windows launcher. Double-click this file.
setlocal
title Vertex Chat
cd /d "%~dp0"

rem --- Running from inside the ZIP? Explorer extracts only this .bat ---------
if not exist "%~dp0start.py" (
    echo.
    echo   It looks like you are running this from INSIDE the ZIP file.
    echo.
    echo   1. Go back to the downloaded ZIP
    echo   2. Right-click it and choose "Extract All..."
    echo   3. Open the extracted folder and double-click run.bat there
    echo.
    goto :end
)

rem --- Try the official Python launcher (verify it actually works) -----------
py -3 -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    py -3 start.py %*
    goto :end
)

rem --- Fall back to python, but skip the fake Microsoft Store stub ----------
rem (On fresh Windows, "python" is a stub that just opens the Store and
rem  exits with an error. A real Python can run "-c import sys".)
python -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    python start.py %*
    goto :end
)

echo.
echo   Python is not installed (or only the Microsoft Store shortcut exists).
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download Python 3.12 or 3.13 and run the installer
echo   3. IMPORTANT: tick "Add python.exe to PATH" on the first screen
echo   4. Double-click run.bat again
echo.

:end
echo.
pause
