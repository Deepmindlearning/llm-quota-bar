@echo off
rem LLM quota bar launcher (double-click, no console window)
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" main.py
exit
