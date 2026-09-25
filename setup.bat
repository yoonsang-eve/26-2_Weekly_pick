@echo off
rem 처음 한 번: 가상환경을 만들고 필요한 패키지를 설치한다
cd /d "%~dp0"
chcp 65001 >nul
if not exist .venv (
  python -m venv .venv || (echo Python 3.11 이상이 필요합니다. https://www.python.org/downloads/ & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-deck.txt -r requirements-app.txt
if not exist .env copy .env.example .env >nul
echo.
echo 설치가 끝났습니다. .env에 키를 채운 뒤 start.bat을 더블클릭하세요.
pause
