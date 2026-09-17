@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement virtuel introuvable : lance install_windows.bat d'abord.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m jarvis.startup --stop %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
