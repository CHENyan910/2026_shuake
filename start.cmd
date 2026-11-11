@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment is missing. See LOCAL_SETUP.txt.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" start.py
if errorlevel 1 pause
