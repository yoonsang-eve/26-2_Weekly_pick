"""에이전트 화면 — 스케줄·주차별 결정·작업 흐름(턴 기록)·비판·회고·교훈·알림. 파일만 읽는다 (LLM 없음).

작업 흐름은 outputs/wk{N}/agent/transcript.md를 파싱해 턴 단위로 돌려준다. 실행이 여러 번이면
(outputs/wk{N}/agent_run1 등) 이전 실행도 고를 수 있다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
sys.path.insert(0, str(ROOT / "src"))

router = APIRouter()

# 도구 → 작업 흐름의 단계. 도면(agent.js)의 마디와 같은 이름이다
PHASE = {"market_overview": "국면 조사", "track_record": "국면 조사", "search": "검색",
         "sector_rotation": "범위 결정", "screen_stocks": "후보 탐색", "screen_etfs": "후보 탐색",
         "instrument_detail": "후보 탐색", "write_note": "메모", "request_critique": "비판",
         "submit_recommendation": "제출"}
TURN = re.compile(r"^## 턴 (\d+)\s*$", re.M)
CALL = re.compile(r"^→ \*\*([a-z_]+)\*\* `(.*)`\s*$")
RESULT = re.compile(r"<details><summary>← 결과( \(오류\))?</summary>\n\n(.*?)\n\n</details>", re.S)


def _json(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _csv(f: Path) -> list[dict]:
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _unit(week: int) -> float | None:
    """주차 장부의 추정 비용 (unit ≈ USD). 장부 입력은 캐시 읽기를 0.1로 가중한 값이라 근사다."""
    f = OUT / f"wk{week}" / "llm_spend.jsonl"
    if not f.exists():
        return None
    tot = 0.0
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        pi, po = (4.0, 20.0) if "opus" in r.get("model", "") else (0.1, 0.5) if "haiku" in r.get("model", "") else (2.0, 10.0)
        tot += (r.get("input", 0) * pi + r.get("output", 0) * po) / 1e6
    return round(tot, 2)


def _verdict(text: str) -> str:
    m = re.search(r"판정\**\s*[:：]\s*\**\s*(fatal|minor)", text, re.I)
    return m.group(1).lower() if m else ""


def _runs(week: int) -> list[str]:
    d = OUT / f"wk{week}"
    names = sorted(p.name for p in d.glob("agent_run*") if (p / "decision.json").exists())
    return names + (["agent"] if (d / "agent" / "decision.json").exists() else [])


def trace(md: str) -> list[dict]:
    """transcript.md → [{turn, items:[{type, ...}]}]. 결과는 앞선 도구 호출에 짝지어 붙인다."""
    parts = TURN.split(md)
    turns = []
    for i in range(1, len(parts), 2):
        no, body = int(parts[i]), parts[i + 1]
        results = [(bool(m.group(1)), m.group(2)) for m in RESULT.finditer(body)]
        body_wo = RESULT.sub("", body)
        items, calls = [], []
        para: list[str] = []

        def flush():
            t = "\n".join(para).strip()
            if t:
                items.append({"type": "text", "text": t})
            para.clear()

        for ln in body_wo.splitlines():
            m = CALL.match(ln)
            if m:
                flush()
                try:
                    args = json.loads(m.group(2))
                except Exception:
                    # 기록은 600자에서 잘린다. 화면에 필요한 키만 정규식으로 건진다
                    raw = m.group(2)
                    args = {k: v for k in ("market", "ticker", "name", "title")
                            if (v := (re.search(rf'"{k}": "([^"]*)"', raw) or [None, None])[1])}
                    args["raw"] = raw
                it = {"type": "tool", "tool": m.group(1), "phase": PHASE.get(m.group(1), "기타"), "args": args}
                items.append(it)
                calls.append(it)
            elif ln.startswith("🔍 검색:"):
                flush()
                q = ln.split(":", 1)[1].strip()
                if q:
                    items.append({"type": "search", "phase": "검색", "query": q})
            elif ln.startswith("> "):
                flush()
                items.append({"type": "system", "text": ln[2:]})
            else:
                para.append(ln)
        flush()
        for c, (err, res) in zip(calls, results):
            c["error"] = err
            c["result"] = res[:6000]
            if c["tool"] == "submit_recommendation":
                c["phase"] = "제출 거부" if res.startswith("제출 거부") else "제출"
            if c["tool"] == "request_critique":
                c["verdict"] = _verdict(res)
        turns.append({"turn": no, "items": items})
    return turns


def _result(week: int, market: str, ticker: str) -> dict:
    for r in _csv(ROOT / "log.csv"):
        if str(r["week"]) == str(week) and r["market"] == market and str(r["ticker"]).lstrip("0") == str(ticker).lstrip("0"):
            return {k: r.get(k) for k in ("ret_pct", "benchmark", "benchmark_ret_pct", "excess_pct", "buy_open", "sell_close")}
    return {}


def _schedule(cfg: dict) -> dict:
    from agent import calendar_refresh, schedule
    from settle import close_final
    now = schedule.now_kst()
    a = cfg.get("agent") or {}
    monday = schedule.target_monday(now)
    plan = schedule.week_plan(monday)
    week = schedule.week_no(monday, cfg)
    if (OUT / f"wk{week}" / "agent" / "decision.json").exists():
        # 이번 주 추천이 끝났으면 다음 예정은 그다음 주다
        monday += timedelta(weeks=1)
        plan, week = schedule.week_plan(monday), week + 1
    at = time.fromisoformat(str((a.get("schedule") or {}).get("recommend_at") or "18:00"))
    rd = schedule.run_day(plan) if plan else None
    nxt_rec = datetime.combine(rd, at, schedule.KST) if rd else None
    pending = []
    for f in sorted(OUT.glob("wk*/agent/decision.json")):
        if (f.parent / "review.json").exists():
            continue
        d = _json(f)
        m = d.get("market")
        p = (d.get("plan") or {}).get(m) or {}
        if p.get("sell_date"):
            tz, _ = schedule.LOCAL[m]
            ready = datetime.combine(datetime.fromisoformat(p["sell_date"]).date(), time(17 if m == "US" else 16), tz).astimezone(schedule.KST)
            pending.append({"week": d.get("week"), "market": m, "ticker": d.get("ticker"), "name": d.get("name"),
                            "sell_date": p["sell_date"], "ready_at": ready.isoformat(timespec="minutes"),
                            "ready": close_final(m, p["sell_date"])})
    logs = sorted((ROOT / "private" / "agent").glob("2*.log"))
    last_tick = None
    for lf in reversed(logs):
        ticks = re.findall(r"^\[tick\] (\S+ \S+) KST", lf.read_text(encoding="utf-8", errors="replace"), re.M)
        if ticks:
            last_tick = ticks[-1]
            break
    from app.calendar_data import auto
    return {"now": now.isoformat(timespec="minutes"), "target_week": week, "monday": monday.isoformat(), "plan": plan,
            "next_recommend": nxt_rec.isoformat(timespec="minutes") if nxt_rec else None,
            "decided": (OUT / f"wk{week}" / "agent" / "decision.json").exists(),
            "reviews_pending": pending, "last_tick": last_tick,
            "calendar_fetched": auto().get("fetched_at"), "calendar_due": calendar_refresh.due(now)}


@router.get("/api/agent")
def overview():
    from agent import memory
    from app.calendar_data import auto_events
    cfg = _cfg()
    a = cfg.get("agent") or {}
    weeks = []
    for wk in sorted(OUT.glob("wk*"), key=lambda p: int(p.name[2:]) if p.name[2:].isdigit() else 0):
        if not wk.name[2:].isdigit():
            continue
        w = int(wk.name[2:])
        d = _json(wk / "agent" / "decision.json")
        if not d:
            continue
        rv = _json(wk / "agent" / "review.json")
        st = d.get("stats") or {}
        weeks.append({"week": w, "market": d.get("market"), "ticker": d.get("ticker"), "name": d.get("name"),
                      "instrument_type": d.get("instrument_type"), "regime_view": d.get("regime_view"),
                      "stance": d.get("stance"), "confidence": d.get("confidence"),
                      "range": d.get("expected_return_range"), "turns": st.get("turns"),
                      "searches": st.get("web_searches"), "cost_unit": st.get("cost_unit"), "week_unit": _unit(w),
                      "runs": _runs(w), "result": _result(w, d.get("market"), d.get("ticker")),
                      "review": {k: rv.get(k) for k in ("summary", "regime_call", "luck_vs_skill")} if rv else None})
    lessons = memory.load_lessons()
    for l in lessons:
        net = len(l.get("support", [])) - len(l.get("against", []))
        l["label"] = "확립" if net >= memory.ESTABLISHED else ("반박됨" if net <= 0 and l.get("against") else "가설")
    today = datetime.now().date().isoformat()
    soon = (datetime.now().date() + timedelta(days=21)).isoformat()
    from app.notify import read
    return {"config": {k: a.get(k) for k in ("enabled", "model", "critic_model", "effort", "max_turns", "max_critiques",
                                             "cost_limit_unit", "search_budget", "instruments", "schedule")},
            "schedule": _schedule(cfg), "weeks": weeks[::-1], "lessons": lessons,
            "events": [e for e in auto_events() if today <= e["date"] <= soon],
            "notify": read("agent", 40)}


@router.get("/api/agent/week/{week}")
def week_detail(week: int, run: str = "agent"):
    if not re.fullmatch(r"agent(_run\d+)?", run):
        raise HTTPException(400)
    d = OUT / f"wk{week}" / run
    dec = _json(d / "decision.json")
    if not dec:
        raise HTTPException(404, f"wk{week} {run}에 에이전트 결정이 없다.")
    tr = (d / "transcript.md").read_text(encoding="utf-8") if (d / "transcript.md").exists() else ""
    crit = []
    for f in sorted(d.glob("critique_*.md")):
        if f.name.endswith(".prompt.txt"):
            continue
        n, t = f.stem.split("_", 2)[1:]
        txt = f.read_text(encoding="utf-8")
        crit.append({"n": int(n), "ticker": t, "verdict": _verdict(txt), "text": txt})
    notes = (d / "notes.md").read_text(encoding="utf-8") if (d / "notes.md").exists() else ""
    review = (d / "review.md").read_text(encoding="utf-8") if (d / "review.md").exists() else ""
    tr = trace(tr)
    # 작업 기록의 결과는 잘려 있어 판정 줄이 빠질 수 있다. 비판 원문에서 판정을 가져온다
    by_t = {c["ticker"].upper(): c["verdict"] for c in crit}
    for t in tr:
        for it in t["items"]:
            if it.get("tool") == "request_critique":
                it["verdict"] = by_t.get(str(it["args"].get("ticker", "")).upper(), it.get("verdict", ""))
    return {"week": week, "run": run, "runs": _runs(week), "decision": dec, "trace": tr,
            "critiques": sorted(crit, key=lambda c: c["n"]), "notes": notes, "review_md": review,
            "review": _json(d / "review.json"), "meta": _json(d / "run_meta.json"),
            "result": _result(week, dec.get("market"), dec.get("ticker"))}


@router.get("/api/account/notify")
def trade_notify():
    """모의투자 매매 알림 기록 (개인 정보라 /api/account 아래 — 보기 전용 링크에서는 막힌다)."""
    from app.notify import read
    return read("trade", 100)
