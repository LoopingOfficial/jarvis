@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  call install_windows.bat
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" jarvis.py
if errorlevel 1 (
  echo Jarvis s'est arrete avec une erreur. Consulte le message ci-dessus.
  pause
  exit /b 1
)
