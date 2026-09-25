"""휴장일·고정 일정. 작업 패널(날짜 제안)과 시장 패널(캘린더)이 같이 쓴다.

출처가 확정되지 않은 값에는 note에 '확인 필요'를 단다. 사용자가 화면에서 추가한 일정은
data/calendar.json에 쌓인다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_EVENTS = ROOT / "data" / "calendar.json"

HOLIDAYS = {
    "KR": {"2026-08-17": "광복절 대체공휴일", "2026-09-24": "추석 연휴", "2026-09-25": "추석",
           "2026-10-05": "개천절 대체공휴일 (확인 필요)", "2026-10-09": "한글날",
           "2026-12-25": "성탄절", "2026-12-31": "연말 휴장"},
    "US": {"2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving", "2026-12-25": "Christmas"},
}

# FOMC 2026 (연준 공개 일정, 이틀 회의의 둘째 날 = 성명·기자회견. 한국시간으로는 다음 날 새벽)
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
        "2026-09-16", "2026-10-28", "2026-12-09"]


def user_events() -> list[dict]:
    if not USER_EVENTS.exists():
        return []
    try:
        return json.loads(USER_EVENTS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_user_events(ev: list[dict]) -> None:
    USER_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    USER_EVENTS.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
