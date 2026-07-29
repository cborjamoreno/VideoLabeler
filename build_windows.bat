@echo off
REM Build VideoLabeler.exe on Windows. Needs Python 3.9+ installed.
REM Double-click this file, or run it from a terminal in this folder.
REM Result: dist\VideoLabeler.exe  (a single file you can copy anywhere)

setlocal
cd /d "%~dp0"

echo.
echo === Creating a clean build environment ===
python -m venv build-env
if errorlevel 1 (
    echo.
    echo ERROR: Python was not found. Install it from python.org and tick
    echo        "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

call build-env\Scripts\activate.bat

echo.
echo === Installing dependencies (PyQt6 via pip: this is what gives audio) ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    pause
    exit /b 1
)

echo.
echo === Checking that audio support is present ===
python -c "from PyQt6.QtMultimedia import QMediaPlayer; print('audio OK')"

echo.
echo === Building ===
rmdir /s /q build dist 2>nul
pyinstaller videolabeler.spec --noconfirm
if errorlevel 1 (
    echo ERROR: the build failed.
    pause
    exit /b 1
)

echo.
echo === Done ===
echo Your executable: %cd%\dist\VideoLabeler.exe
echo Annotations will be saved to an "annotations" folder next to it.
pause
