# 주간 추천 에이전트(src/agent/run.py tick)를 Windows 작업 스케줄러에 등록한다.
#   등록:  powershell -ExecutionPolicy Bypass -File agent_register.ps1
#   해제:  powershell -ExecutionPolicy Bypass -File agent_register.ps1 -Remove
#
# 매시 5분에 tick을 부른다. tick은 할 일이 없으면 바로 끝난다 (LLM을 부르지 않는다).
#   추천: 다음 평가 주간 첫 거래일 전날(일요일 또는 연휴 마지막 날) config agent.schedule.recommend_at 이후
#   보고: 고른 종목 시장의 매도일 종가가 확정된 뒤 → 정산 · 회고(LLM) · 교훈 갱신 · 발표 자료
# 결과는 알림(메일 설정 시 메일 + Windows 알림)으로 온다. 로그: private\agent\YYYYMMDD.log
#
# 추천·회고는 LLM API를 호출한다. 등록하는 순간부터 자동으로 비용이 발생한다는 뜻이다.
# 주차 상한은 config agent.token_budget, 끄려면 config agent.enabled: false 또는 -Remove.
# PC가 절전이면 깨운다 (전원 옵션 '절전 모드 해제 타이머 허용'). 완전히 꺼져 있으면 켜진 뒤 첫 tick에서 따라잡는다.
param([switch]$Remove)

$name = "weekly_pick agent"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "removed: $name"
    return
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { throw "pythonw.exe not found: $py (run setup.bat first)" }

$t = New-ScheduledTaskTrigger -Daily -At "00:05"
$t.Repetition = (New-ScheduledTaskTrigger -Once -At "00:05" -RepetitionInterval (New-TimeSpan -Hours 1) `
                 -RepetitionDuration (New-TimeSpan -Hours 23 -Minutes 55)).Repetition
$action = New-ScheduledTaskAction -Execute $py -Argument "src\agent\run.py tick --log" -WorkingDirectory $root
# 추천 루프는 30~60분 걸릴 수 있다. 3시간이 지나면 강제 종료한다.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $t -Settings $settings -Force `
    -Description "weekly_pick: hourly tick. Recommends on the day before the evaluation week, reviews after it ends. Log: private\agent\" | Out-Null
Write-Output "registered: $name"
