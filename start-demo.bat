@echo off
setlocal
cd /d "%~dp0"
py gui.py --demo
if errorlevel 1 pause
