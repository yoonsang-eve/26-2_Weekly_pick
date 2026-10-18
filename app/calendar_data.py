"""휴장일·고정 일정. 작업 패널(날짜 제안)과 시장 패널(캘린더), 에이전트 스케줄이 같이 쓴다.

세 겹으로 쌓인다.
  1. 아래 HOLIDAYS / FOMC — 손으로 적은 고정값 (학기 초에 확인한 것)
  2. data/calendar_auto.json — 에이전트가 매주 웹 검색으로 찾아 넣는 휴장일과 대형 일정
     (src/agent/calendar_refresh.py). 고정값을 지우지는 않고 더하기만 한다
  3. data/calendar.json — 사용자가 화면에서 직접 추가한 일정

HOLIDAYS는 다른 모듈이 `from app.calendar_data import HOLIDAYS`로 가져다 쓰므로, 자동 갱신분은
merge_auto()가 같은 dict 객체에 제자리로 합친다 (새 dict를 만들면 가져간 쪽이 옛 값을 본다).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_EVENTS = ROOT / "data" / "calendar.json"
AUTO = ROOT / "data" / "calendar_auto.json"

HOLIDAYS = {
    "KR": {"2026-08-17": "광복절 대체공휴일", "2026-09-24": "추석 연휴", "2026-09-25": "추석",
           "2026-10-05": "개천절 대체공휴일", "2026-10-09": "한글날",
           "2026-12-25": "성탄절", "2026-12-31": "연말 휴장"},
    "US": {"2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving", "2026-12-25": "Christmas"},
}
_STATIC = copy.deepcopy(HOLIDAYS)

# FOMC 2026 (연준 공개 일정, 이틀 회의의 둘째 날 = 성명·기자회견. 한국시간으로는 다음 날 새벽)
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
        "2026-09-16", "2026-10-28", "2026-12-09"]


def auto() -> dict:
    try:
        return json.loads(AUTO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def merge_auto() -> dict:
    """자동 탐색 휴장일을 HOLIDAYS에 제자리로 합친다. 고정값과 겹치면 고정값의 이름을 쓴다."""
    a = auto()
    for m in ("KR", "US"):
        HOLIDAYS[m].clear()
        for h in (a.get("holidays") or {}).get(m, []):
            HOLIDAYS[m][h["date"]] = f"{h.get('name', '휴장')} (자동)"
        HOLIDAYS[m].update(_STATIC[m])
    return HOLIDAYS


def auto_events() -> list[dict]:
    """자동 탐색한 대형 일정 [{date, title, market, time_kst?, importance?}]."""
    return list(auto().get("events") or [])


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


merge_auto()
