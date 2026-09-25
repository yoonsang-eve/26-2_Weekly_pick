"""로컬 대시보드 서버 — outputs/와 log.csv를 읽기만 한다. LLM도 시세 API도 호출하지 않는다.

    .venv/Scripts/python -m uvicorn app.server:app --port 8765
    -> http://localhost:8765
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
