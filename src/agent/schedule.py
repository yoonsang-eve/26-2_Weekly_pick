"""언제 추천하고 언제 보고서를 쓰는가. LLM 없음.

평가 주간은 월~금이다. 시장마다 휴장이 달라 매수일(그 주 첫 거래일 시가)·매도일(마지막 거래일 종가)·
보유 영업일 수를 따로 계산한다 (CLAUDE.md 1절 0번).

추천 시각: 다음 평가 주간의 첫 거래일 '전날'(한국 날짜) — 평소엔 일요일, 월요일이 휴장이면 연휴
마지막 날. 그 날 config agent.schedule.recommend_at 이후의 첫 tick에서 돈다. PC가 꺼져 있어 놓쳤으면
그 시장의 매수 시가 전까지는 따라잡아 돈다. 매수 시가가 지난 시장은 그 주 후보에서 뺀다.

보고 시각: 그 주에 고른 종목 시장의 매도일 종가가 현지 시각으로 확정된 뒤 (settle.close_final).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from app.calendar_data import FOMC, HOLIDAYS  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
LOCAL = {"KR": (ZoneInfo("Asia/Seoul"), time(9, 0)), "US": (ZoneInfo("America/New_York"), time(9, 30))}


def is_trading(market: str, d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS[market]


def week_plan(monday: date) -> dict:
    """{시장: {buy_date, sell_date, holding_days, holidays}} — 그 주에 거래일이 없는 시장은 빠진다."""
    fri = monday + timedelta(days=4)
    out = {}
    for m in ("KR", "US"):
        days = [monday + timedelta(days=i) for i in range(5) if is_trading(m, monday + timedelta(days=i))]
        if days:
            out[m] = {"buy_date": days[0].isoformat(), "sell_date": days[-1].isoformat(),
                      "holding_days": len(days),
                      "holidays": [f"{k} {v}" for k, v in HOLIDAYS[m].items()
                                   if monday.isoformat() <= k <= fri.isoformat()]}
    return out


def buy_open_kst(market: str, buy_date: str) -> datetime:
    tz, t = LOCAL[market]
    return datetime.combine(date.fromisoformat(buy_date), t, tz).astimezone(KST)


def target_monday(now: datetime) -> date:
    """추천 대상 주간의 월요일. 이번 주에 매수 시가가 아직 안 온 시장이 하나라도 있으면 이번 주, 아니면 다음 주.

    일요일 추천을 놓치고 월요일 오전에 켜졌다면 한국장은 열렸어도 미국장으로는 낼 수 있다.
    """
    today = now.astimezone(KST).date()
    mon = today - timedelta(days=today.weekday())
    plan = week_plan(mon)
    if plan and now < max(buy_open_kst(m, p["buy_date"]) for m, p in plan.items()):
        return mon
    return mon + timedelta(weeks=1)


def run_day(plan: dict) -> date:
    """추천을 돌릴 날: 두 시장 중 먼저 열리는 거래일(한국 날짜)의 전날."""
    first = min(buy_open_kst(m, p["buy_date"]).date() for m, p in plan.items())
    return first - timedelta(days=1)


def eligible(plan: dict, now: datetime, margin_min: int = 30) -> dict:
    """매수 시가까지 margin 이상 남은 시장만."""
    return {m: p for m, p in plan.items()
            if now <= buy_open_kst(m, p["buy_date"]) - timedelta(minutes=margin_min)}


def week_no(monday: date, cfg: dict) -> int:
    """config.run(week_no, buy_date)을 기준점으로 주차 번호를 센다. app/tasks._suggest와 같은 규칙."""
    run = cfg["run"]
    cur = date.fromisoformat(str(run["buy_date"]))
    cur -= timedelta(days=cur.weekday())
    return int(run["week_no"]) + (monday - cur).days // 7


def macro_dates(plan: dict) -> list[str]:
    """평가 주간에 걸리는 고정 일정 (FOMC). 나머지는 에이전트가 검색으로 채운다."""
    lo = min(p["buy_date"] for p in plan.values())
    hi = max(p["sell_date"] for p in plan.values())
    lo_d = (date.fromisoformat(lo) - timedelta(days=3)).isoformat()
    return [f"{d} FOMC 성명 (한국시간 다음 날 새벽)" for d in FOMC if lo_d <= d <= hi]


def now_kst() -> datetime:
    return datetime.now(KST)
