@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
  py -3 setup_windows.py
  goto finished
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
  python setup_windows.py
  goto finished
)
echo Python 3.11 ou plus recent est requis.
echo Installe Python pour Windows depuis https://www.python.org/downloads/windows/
echo Active l'option Add python.exe to PATH, puis relance ce fichier.
pause
exit /b 1
:finished
if errorlevel 1 (
  echo Installation interrompue. Consulte l'erreur ci-dessus.
  pause
  exit /b 1
)
echo Installation terminee. Double-clique sur run_jarvis.bat pour demarrer.
pause
