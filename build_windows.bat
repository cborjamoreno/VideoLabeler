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
echo === Packing for delivery ===
REM What the annotators receive: the .exe plus their instructions, nothing else.
rmdir /s /q "dist\VideoLabeler-windows" 2>nul
del /q VideoLabeler-windows.zip 2>nul
mkdir "dist\VideoLabeler-windows"
copy /y "dist\VideoLabeler.exe" "dist\VideoLabeler-windows\" >nul
if exist README_USERS.txt copy /y README_USERS.txt "dist\VideoLabeler-windows\" >nul
powershell -NoProfile -Command "Compress-Archive -Path 'dist\VideoLabeler-windows' -DestinationPath 'VideoLabeler-windows.zip' -Force"

echo.
echo === Done ===
echo Send this file to the annotators, and nothing else:
echo     %cd%\VideoLabeler-windows.zip
echo.
echo It contains the .exe and a short usage guide. Tell them to
echo unzip it, keep the .exe somewhere writable (Desktop, not Program Files),
echo and click "More info" -^> "Run anyway" the first time SmartScreen warns.
pause
