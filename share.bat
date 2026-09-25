@echo off
rem 보기 전용 링크 만들기. 이 창을 닫으면 링크가 끊긴다.
cd /d "%~dp0"
chcp 65001 >nul
".venv\Scripts\python.exe" share.py
pause
