"""로컬 대시보드 서버 — outputs/와 log.csv를 읽기만 한다. LLM도 시세 API도 호출하지 않는다.

    .venv/Scripts/python -m uvicorn app.server:app --port 8765
    -> http://localhost:8765
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
STATIC = Path(__file__).resolve().parent / "static"
STAGES = ["analyst", "bull", "bear", "trader"]

app = FastAPI(title="weekly_pick dashboard")


def _json(f: Path) -> dict | None:
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _csv(f: Path) -> list[dict]:
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ticker(v) -> str:
    s = str(v).strip()
    return s.zfill(6) if s.isdigit() else s


def _runs() -> list[tuple[int, str, Path]]:
    runs = []
    for wk in OUT.glob("wk*"):
        m = re.fullmatch(r"wk(\d+)", wk.name)
        if not m or not wk.is_dir():
            continue
        for mk in wk.iterdir():
            if mk.is_dir() and mk.name in ("KR", "US"):
                runs.append((int(m.group(1)), mk.name, mk))
    return sorted(runs)


def _cohort_summary(rows: list[dict]) -> dict | None:
    rets = [(r["role"], _num(r["ret_pct"])) for r in rows]
    rets = [(role, v) for role, v in rets if v is not None]
    if not rets:
        return None
    avg = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    pick = next((v for role, v in rets if role == "pick"), None)
    return {
        "candidates_avg": avg([v for _, v in rets]),
        "finalists_avg": avg([v for role, v in rets if role in ("pick", "finalist")]),
        "pick": pick,
        "n": len(rets),
        # pick이 후보 중 몇 위였나 (1 = 최고)
        "pick_rank": None if pick is None else 1 + sum(v > pick for _, v in rets),
    }


def _stage_meta(meta: dict | None) -> dict:
    out = {}
    for s in STAGES:
        st = (meta or {}).get("stages", {}).get(s, {})
        u = st.get("usage") or {}
        out[s] = {
            "model": st.get("model"),
            "rendered_at": st.get("rendered_at"),
            "elapsed_sec": st.get("elapsed_sec"),
            "web_search_calls": st.get("web_search_calls"),
            "input_tokens": u.get("input_tokens"),
            "output_tokens": u.get("output_tokens"),
        }
    return out


def _summary(week: int, market: str, d: Path, log: dict) -> dict:
    pick = _json(d / "pick.json")
    meta = _json(d / "run_meta.json")
    row = log.get((str(week), market))
    return {
        "week": week,
        "market": market,
        "pick": pick,
        "mode": (meta or {}).get("mode"),
        "prompt_version": (meta or {}).get("prompt_version"),
        "config_version": (meta or {}).get("config_version"),
        "stages": _stage_meta(meta),
        "responses": [s for s in STAGES if (d / "responses" / f"{s}.md").exists()],
        "settled": {k: row.get(k) for k in row} if row else None,
        "cohort": _cohort_summary(_csv(d / "cohort.csv")),
        "events_mtime": (d / "events.jsonl").stat().st_mtime if (d / "events.jsonl").exists() else None,
    }


def _log() -> dict:
    return {(r["week"], r["market"]): r for r in _csv(ROOT / "log.csv")}


@app.get("/api/runs")
def runs():
    log = _log()
    return [_summary(w, m, d, log) for w, m, d in _runs()]


@app.get("/api/runs/{week}/{market}")
def run_detail(week: int, market: str):
    d = OUT / f"wk{week}" / market
    if not d.is_dir():
        raise HTTPException(404, "no such run")
    s = _summary(week, market, d, _log())
    cand = _csv(d / "candidates.csv")
    for c in cand:
        c["ticker"] = _ticker(c.get("ticker"))
    cohort = _csv(d / "cohort.csv")
    for c in cohort:
        c["ticker"] = _ticker(c.get("ticker"))
    s.update(candidates=cand, cohort_rows=cohort,
             screen_meta=_json(d / "screen_meta.json"))
    return s


@app.get("/api/runs/{week}/{market}/text/{kind}/{stage}", response_class=PlainTextResponse)
def run_text(week: int, market: str, kind: str, stage: str):
    """kind=response -> responses/{stage}.md, kind=prompt -> rendered_{stage}.txt"""
    if stage not in STAGES or kind not in ("response", "prompt"):
        raise HTTPException(400)
    d = OUT / f"wk{week}" / market
    f = d / "responses" / f"{stage}.md" if kind == "response" else d / f"rendered_{stage}.txt"
    if not f.exists():
        raise HTTPException(404)
    return f.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 이벤트 (데스크 UI)
def _read_events(f: Path) -> list[dict]:
    out = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass        # 쓰는 도중의 마지막 줄
    return out


def _synth_events(week: int, market: str, d: Path) -> list[dict]:
    """events.jsonl이 없던 시절(wk3·wk4)의 실행을 저장된 산출물로 복원한다.

    실제로 기록된 값(시각·소요시간·검색 횟수·선택 결과·탈락 사유·정산)만 쓰고,
    당시 검색어처럼 남아 있지 않은 것은 만들어 넣지 않는다.
    """
    ev: list[dict] = []

    def add(ts: datetime, agent: str, kind: str, msg: str = "", **data):
        ev.append({"ts": ts.isoformat(timespec="seconds"), "agent": agent, "kind": kind,
                   "msg": msg, "data": {**data, "synth": True}})

    sm = _json(d / "screen_meta.json") or {}
    meta = _json(d / "run_meta.json") or {}
    pick = _json(d / "pick.json") or {}
    cand = _csv(d / "candidates.csv")
    names = {_ticker(c["ticker"]): c.get("name", "") for c in cand}

    stages = meta.get("stages", {})
    first = min((datetime.fromisoformat(v["rendered_at"]) for v in stages.values()
                 if v.get("rendered_at")), default=None)
    t_scr = datetime.fromisoformat(sm["screened_at"]) if sm.get("screened_at") else \
        (first - timedelta(minutes=30) if first else datetime(2000, 1, 1))
    add(t_scr - timedelta(seconds=90), "screener", "start",
        f"기준일 {sm.get('ref_date', '?')} 종가")
    add(t_scr, "screener", "done", f"후보 {len(cand)}종목 확정", n=len(cand),
        tickers=[f"{_ticker(c['ticker'])} {c.get('name', '')}" for c in cand])

    finalists = [_ticker(pick["ticker"])] + [_ticker(r["ticker"]) for r in pick.get("rejected", [])] \
        if pick.get("ticker") else []
    order = [s for s in STAGES if stages.get(s, {}).get("rendered_at")]
    for i, st in enumerate(order):
        m = stages[st]
        t0 = datetime.fromisoformat(m["rendered_at"])
        if m.get("elapsed_sec"):
            t1 = t0 + timedelta(seconds=m["elapsed_sec"])
        elif i + 1 < len(order):          # manual 모드: 다음 단계 렌더링 직전에 끝난 것으로 본다
            t1 = datetime.fromisoformat(stages[order[i + 1]]["rendered_at"]) - timedelta(seconds=5)
        else:
            t1 = t0 + timedelta(minutes=3)
        model = m.get("model") or "manual"
        add(t0, st, "start", f"{model} 호출" if meta.get("mode") == "auto" else f"{model} (manual)",
            model=model)
        extra = {}
        if st == "analyst" and finalists:
            for t in finalists:
                add(t1 - timedelta(seconds=1), st, "note", f"통과 {t} {names.get(t, '')}")
            extra["finalists"] = finalists
        if st == "bear":
            for r in pick.get("rejected", []):
                t = _ticker(r["ticker"])
                add(t1 - timedelta(seconds=1), st, "note",
                    f"{names.get(t, t)} · {r.get('reason', '')[:60]}")
        if st == "trader" and pick:
            extra["pick"] = _ticker(pick.get("ticker"))
        searches = m.get("web_search_calls")
        msg = " · ".join(x for x in [
            f"{m['elapsed_sec']:.0f}초" if m.get("elapsed_sec") else "",
            f"검색 {searches}회" if searches else ""] if x) or "완료"
        add(t1, st, "done", msg, elapsed_sec=m.get("elapsed_sec"), searches=searches,
            input_tokens=(m.get("usage") or {}).get("input_tokens"),
            output_tokens=(m.get("usage") or {}).get("output_tokens"), **extra)

    row = _log().get((str(week), market))
    if row and row.get("ret_pct"):
        t = datetime.fromisoformat(row["sell_date"]) + timedelta(hours=16)
        add(t, "settle", "done", f"{float(row['ret_pct']):+.2f}% (벤치마크 {row['benchmark_ret_pct']}%)",
            ret_pct=float(row["ret_pct"]), benchmark_ret_pct=_num(row.get("benchmark_ret_pct")),
            buy_open=_num(row.get("buy_open")), sell_close=_num(row.get("sell_close")),
            buy_date=row["buy_date"], sell_date=row["sell_date"])
    return sorted(ev, key=lambda e: e["ts"])


def _events(week: int, market: str) -> tuple[list[dict], str]:
    d = OUT / f"wk{week}" / market
    if not d.is_dir():
        raise HTTPException(404, "no such run")
    real = _read_events(d / "events.jsonl")
    if real:
        return real, "recorded"
    return _synth_events(week, market, d), "reconstructed"


@app.get("/api/runs/{week}/{market}/events")
def run_events(week: int, market: str):
    ev, source = _events(week, market)
    return {"source": source, "events": ev}


@app.get("/api/runs/{week}/{market}/stream")
async def run_stream(week: int, market: str, request: Request, since: int = 0):
    """events.jsonl을 tail해서 Server-Sent Events로 흘린다. since = 이미 받은 줄 수."""
    f = OUT / f"wk{week}" / market / "events.jsonl"

    async def gen():
        n = since
        yield ": connected\n\n"
        while not await request.is_disconnected():
            ev = _read_events(f)
            for e in ev[n:]:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            n = max(n, len(ev))
            yield ": ping\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/desk")
def desk():
    return FileResponse(STATIC / "desk.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
