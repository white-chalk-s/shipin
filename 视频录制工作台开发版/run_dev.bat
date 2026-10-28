@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到开发环境，请先按 README 创建 Python 3.12 虚拟环境。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" region_recorder.py
if errorlevel 1 pause
