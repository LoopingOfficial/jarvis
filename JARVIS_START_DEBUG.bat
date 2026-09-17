@echo off
REM JARVIS_START_DEBUG.bat - Boot Screen avec console visible (diagnostic).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "JARVIS_BOOT_DEBUG=1"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement virtuel introuvable : lance install_windows.bat d'abord.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m jarvis.boot %*
set "RC=%errorlevel%"
if "%RC%"=="0" exit /b 0
if "%RC%"=="130" exit /b 0
if "%RC%"=="-1073741510" exit /b 0
if "%RC%"=="3221225786" exit /b 0
echo.
echo Boot Screen termine ^(code %RC%^).
echo Utilise JARVIS_DOCTOR.bat pour le diagnostic detaille.
pause
exit /b %RC%
