@echo off
REM JARVIS_START.bat - demarre JARVIS avec le Boot Screen (aucune console).
REM Le Boot Screen affiche l'etat reel du demarrage puis ouvre l'interface.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Environnement virtuel introuvable : lance install_windows.bat d'abord.
  pause
  exit /b 1
)

REM pythonw = pas de fenetre console. Le Boot Screen ecrit ses logs dans logs\startup.
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m jarvis.boot %*
  exit /b 0
)

REM Repli si pythonw absent : Boot Screen en console.
"%PY%" -m jarvis.boot %*
set "RC=%errorlevel%"
if "%RC%"=="0" exit /b 0
if "%RC%"=="130" exit /b 0
if "%RC%"=="-1073741510" exit /b 0
if "%RC%"=="3221225786" exit /b 0
echo.
echo JARVIS START FAILED ^(code %RC%^).
echo Utilise JARVIS_DOCTOR.bat pour le diagnostic detaille.
pause
exit /b %RC%
