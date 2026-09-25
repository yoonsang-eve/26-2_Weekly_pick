@echo off
rem 내 데스크(모든 기능) 켜기 — http://localhost:8765
cd /d "%~dp0"
chcp 65001 >nul
start "" http://localhost:8765
".venv\Scripts\python.exe" -m uvicorn app.server:app --port 8765
pause
