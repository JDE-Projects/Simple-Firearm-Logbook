@echo off
setlocal
echo =====================================================
echo  Simple Firearm Logbook - Build Script
echo =====================================================
echo.

cd /d "%~dp0"

REM --- skip interactive pauses when running in CI (GitHub Actions sets CI) ---
set "PAUSE=pause"
if defined CI set "PAUSE="

REM --- check Python: requirements.txt is locked for 64-bit Python 3.14 ---
python -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: This build needs 64-bit Python 3.14 on PATH.
    echo Install it from https://python.org and tick "Add Python to PATH".
    %PAUSE%
    exit /b 1
)

REM --- build inside a private, throwaway Python environment so the PC's own
REM --- Python is never changed. It is rebuilt from scratch every run.
set "VENV=build\venv"
set "VPY=%VENV%\Scripts\python.exe"
if exist "%VENV%\" (
    echo Removing the previous build environment...
    rmdir /s /q "%VENV%"
)
if exist "%VENV%\" (
    echo ERROR: Could not remove the previous build environment in %VENV%.
    echo Close any program using files in it, then run this script again.
    %PAUSE%
    exit /b 1
)
echo Creating a fresh build environment in %VENV% ...
python -m venv "%VENV%"
if errorlevel 1 (
    echo ERROR: Could not create the build environment in %VENV%.
    %PAUSE%
    exit /b 1
)
"%VPY%" --version
"%VPY%" -m pip --version
echo.
echo Installing the locked package list, each package checked against its hash...
"%VPY%" -m pip install --disable-pip-version-check --require-hashes --build-constraint build-constraints.txt -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install the locked packages from requirements.txt.
    %PAUSE%
    exit /b 1
)
echo.
echo Building executable, onedir so the bundled Qt stays replaceable...
set QT_API=pyside6
REM --noconfirm replaces the previous dist folder without asking.
"%VPY%" -m PyInstaller --noconfirm --onedir --windowed --name "Simple Firearm Logbook" ^
  --icon "simple_firearm_logbook.ico" ^
  --add-data "sfl\resources;resources" ^
  --add-data "licenses;licenses" ^
  --collect-all PySide6 ^
  --collect-all qtpy ^
  --hidden-import truststore ^
  simple_firearm_logbook.py
if errorlevel 1 (
    echo ERROR: PyInstaller failed. See the messages above.
    %PAUSE%
    exit /b 1
)
"%VPY%" tools\trim_bundle.py "dist\Simple Firearm Logbook"
if errorlevel 1 (
    echo ERROR: Qt bundle trim failed or was refused. See the messages above.
    %PAUSE%
    exit /b 1
)
echo.
echo =====================================================
echo  Done. Your app folder is in:
echo    dist\Simple Firearm Logbook\
echo  Run:  dist\Simple Firearm Logbook\Simple Firearm Logbook.exe
echo =====================================================
echo.
%PAUSE%
