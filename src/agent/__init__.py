"""weekly_pick 에이전트 (prompt v4~).

고정 프롬프트 체인(screener → analyst → bull → bear → trader → selector) 대신, 도구를 쥔 LLM 하나가
시장 국면을 먼저 조사하고 탐색 범위를 스스로 정해 후보를 찾고, 별도 컨텍스트의 비판자에게 반박을
받아 통과하지 못하면 되돌아가 다시 탐색하는 루프다. 자세한 설계는 docs/agent_design.md.

    data.py      가격 데이터 (주식 패널·ETF·지수·국면 통계). LLM 없음
    tools.py     에이전트가 부르는 도구의 스키마와 실행
    loop.py      Messages API 도구 사용 루프 (pause_turn·예산·기록)
    memory.py    지난 주차 기록과 교훈 (과적합 방지 규칙 포함)
    schedule.py  '언제 추천하고 언제 보고서를 쓰나' 판단
    run.py       CLI: recommend / review / tick / tools-test / check
"""
