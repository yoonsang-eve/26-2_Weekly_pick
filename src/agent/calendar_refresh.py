"""휴장일·대형 일정 주간 갱신 (LLM + 웹 검색, 1회 약 0.1 unit).

손으로 적은 휴장일 표(app/calendar_data.py)는 학기 초 값이라 빠진 날이 생긴다. 휴장일이 틀리면 매수일·
추천일 계산이 통째로 어긋나므로, 매주 한 번 앞으로 5주의 휴장일과 대형 일정을 검색해 data/calendar_auto.json에
넣는다. 고정값은 지우지 않고 더하기만 한다. 휴장일이 새로 생기거나 사라지면 알림을 남긴다.

언제: tick이 토·일에 이번 주 갱신이 없으면 돌린다 (일요일 추천 전에 끝나도록). 7일 넘게 비었으면 요일 무관.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schedule

ROOT = Path(__file__).resolve().parent.parent.parent


def due(now: datetime) -> bool:
    from app.calendar_data import auto
    a = auto()
    if not a.get("fetched_at"):
        return True
    last = datetime.fromisoformat(a["fetched_at"]).date()
    today = now.astimezone(schedule.KST).date()
    if (today - last).days >= 7:
        return True
    if today.weekday() >= 5:                          # 토·일: 이번 주 금요일 이후 갱신이 없으면
        fri = today - timedelta(days=today.weekday() - 4)
        return last < fri
    return False


def _valid_day(s: str, lo: date, hi: date) -> bool:
    try:
        d = date.fromisoformat(str(s)[:10])
    except ValueError:
        return False
    return lo <= d <= hi


def refresh(cfg: dict, provider, render, weeks: int = 5) -> dict:
    """검색 → 검증 → 병합 → 저장. 돌려주는 값: {'added': [...], 'removed': [...], 'events': n}."""
    from app import calendar_data as cal
    from agent import tools

    a = cfg.get("agent") or {}
    today = schedule.now_kst().date()
    lo, hi = today, today + timedelta(weeks=weeks)
    n = int((a.get("search_budget") or {}).get("calendar") or 4)
    prompt = render(start=lo.isoformat(), end=hi.isoformat(), today=today.isoformat(), n=n)
    reply = provider.complete(model=a.get("calendar_model") or a.get("model"), prompt=prompt, max_tokens=8000,
                              temperature=None, web_search=True, max_searches=n)
    got = tools.parse_json_block(reply.text)
    if not got:
        raise ValueError(f"일정 JSON을 읽지 못했다: {reply.text[:300]}")

    old = cal.auto()
    before = {m: {h["date"]: h for h in (old.get("holidays") or {}).get(m, [])} for m in ("KR", "US")}
    hol = {}
    for m in ("KR", "US"):
        # 이번 창 밖의 지난 기록은 그대로 두고, 창 안은 새 결과로 바꾼다
        keep = [h for h in before[m].values() if not _valid_day(h["date"], lo, hi)]
        new = [{"date": str(h["date"])[:10], "name": h.get("name", ""), "source": h.get("source", "")}
               for h in (got.get("holidays") or {}).get(m, [])
               if _valid_day(h.get("date"), lo, hi) and date.fromisoformat(str(h["date"])[:10]).weekday() < 5]
        hol[m] = sorted(keep + new, key=lambda h: h["date"])
    ev_keep = [e for e in old.get("events") or [] if not _valid_day(e.get("date"), lo, hi)]
    ev_new = [{k: e.get(k, "") for k in ("date", "time_kst", "market", "title", "source")}
              for e in got.get("events") or [] if _valid_day(e.get("date"), lo, hi) and e.get("title")]
    for e in ev_new:
        e["date"] = str(e["date"])[:10]
    rec = {"fetched_at": datetime.now().isoformat(timespec="seconds"), "window": [lo.isoformat(), hi.isoformat()],
           "model": reply.model, "usage": reply.usage, "holidays": hol,
           "events": sorted(ev_keep + ev_new, key=lambda e: e["date"])}
    cal.AUTO.parent.mkdir(parents=True, exist_ok=True)
    cal.AUTO.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    cal.merge_auto()

    after = {m: {h["date"] for h in hol[m]} for m in ("KR", "US")}
    added = [f"{m} {d} {next(h['name'] for h in hol[m] if h['date'] == d)}"
             for m in ("KR", "US") for d in sorted(after[m] - set(before[m])) if d not in cal._STATIC[m]]
    removed = [f"{m} {d}" for m in ("KR", "US") for d in sorted(set(before[m]) - after[m])
               if _valid_day(d, lo, hi)]
    print(f"[일정] {lo} ~ {hi}: 휴장 KR {len(hol['KR'])} · US {len(hol['US'])}, 대형 일정 {len(ev_new)}건"
          + (f", 새 휴장 {added}" if added else "") + (f", 사라진 휴장 {removed}" if removed else ""))
    return {"added": added, "removed": removed, "events": len(ev_new)}
