@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Building Windows Region Recorder EXE
echo ==========================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher not found. Install Python 3.11 or 3.12 64-bit first.
    echo During installation, enable "Add Python to PATH".
    pause
    exit /b 1
)

py -m pip install --upgrade pip
if errorlevel 1 goto :error

py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

py -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name "区域录屏工具" ^
  --collect-all mss ^
  --collect-all pynput ^
  --collect-all cv2 ^
  region_recorder.py

if errorlevel 1 goto :error

echo.
echo Build complete:
echo %CD%\dist\区域录屏工具.exe
echo.
pause
exit /b 0

:error
echo.
echo Build failed. Read the error message above.
pause
exit /b 1
