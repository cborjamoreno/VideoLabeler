@echo off
REM Build VideoLabeler.exe on Windows. Needs Python 3.9+ installed.
REM Double-click this file, or run it from a terminal in this folder.
REM Result: dist\VideoLabeler.exe  (a single file you can copy anywhere)

setlocal
cd /d "%~dp0"

echo.
echo === Looking for Python ===
REM Prefer the "py" launcher that the python.org installer provides: plain
REM "python" may hit the Microsoft Store stub, which silently does nothing.
set PYTHON=
where py >nul 2>nul && set PYTHON=py -3
if not defined PYTHON (
    where python >nul 2>nul && set PYTHON=python
)
if not defined PYTHON (
    echo.
    echo ERROR: Python was not found.
    echo        Install it from https://www.python.org/downloads/windows/
    echo        and TICK "Add python.exe to PATH" on the first setup screen.
    pause
    exit /b 1
)

%PYTHON% --version
if errorlevel 1 (
    echo.
    echo ERROR: Python is registered but does not run. This usually means the
    echo        Microsoft Store placeholder is in the way. Install real Python
    echo        from python.org, or turn off the "App execution aliases" for
    echo        python.exe in Windows Settings.
    pause
    exit /b 1
)

echo.
echo === Creating a clean build environment ===
%PYTHON% -m venv build-env
if errorlevel 1 (
    echo ERROR: could not create the build environment.
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
python -c "from PyQt6.QtMultimedia import QMediaPlayer; print('audio OK')" 2>nul || echo WARNING: QtMultimedia is missing. The app will build, but with no sound.

echo.
echo === Building ===
REM One directory per command: "rmdir a b" is not valid.
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
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
