@echo off
REM JarvisSupervisor launcher (externe, indépendant du package jarvis)
cd /d "%~dp0.."
".venv\Scripts\python.exe" supervisor\jarvis_supervisor.py %*