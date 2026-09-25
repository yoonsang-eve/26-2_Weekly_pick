@echo off
rem 이번 주 결과를 사이트에 올린다 (git push -> GitHub가 굽고 게시한다. 몇 분 걸린다)
cd /d "%~dp0"
chcp 65001 >nul
git add outputs log.csv final_log.csv data config.yaml 2>nul
git commit -m "주간 결과 게시" >nul 2>&1
git push
echo.
echo 올렸습니다. 3~5분 뒤 사이트에 반영됩니다.
pause
