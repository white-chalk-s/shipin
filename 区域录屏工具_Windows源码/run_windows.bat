@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Check that Python 3.11/3.12 64-bit is installed.
  pause
  exit /b 1
)
py region_recorder.py
pause
