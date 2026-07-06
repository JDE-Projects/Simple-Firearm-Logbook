@echo off
echo =====================================================
echo  Simple Firearm Logbook - Build Script
echo =====================================================
echo.

cd /d "%~dp0"

REM --- skip interactive pauses when running in CI (GitHub Actions sets CI) ---
set "PAUSE=pause"
if defined CI set "PAUSE="

REM --- check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Install Python 3 from https://python.org and tick "Add Python to PATH".
    %PAUSE%
    exit /b 1
)

echo Ensuring the LGPL Qt binding is the one bundled...
pip uninstall -y PyQt6 PyQt6-WebEngine PyQt6-Qt6 PyQt6-sip >nul 2>&1
echo.
echo Installing pinned dependencies from requirements.txt ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies from requirements.txt.
    %PAUSE%
    exit /b 1
)
echo.
echo Building executable (onedir, so the bundled Qt stays replaceable)...
set QT_API=pyside6
pyinstaller --onedir --windowed --name "Simple Firearm Logbook" ^
  --icon "simple_firearm_logbook.ico" ^
  --splash "simple_firearm_logbook-splash.png" ^
  --add-data "simple_firearm_logbook-UI.html;." ^
  --add-data "simple_firearm_logbook.png;." ^
  --add-data "fonts;fonts" ^
  --collect-all PySide6 ^
  --collect-all qtpy ^
  simple_firearm_logbook.py
echo.
echo =====================================================
echo  Done. Your app folder is in:
echo    dist\Simple Firearm Logbook\
echo  Run:  dist\Simple Firearm Logbook\Simple Firearm Logbook.exe
echo =====================================================
echo.
%PAUSE%
