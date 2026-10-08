@echo off
setlocal
cd /d "%~dp0"
py gui.py
if errorlevel 1 pause
