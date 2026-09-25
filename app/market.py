"""시장 패널 — 지수·환율·금리·변동성·원자재·크립토 시세판, 히트맵, 요약, 캘린더.

구성은 Finviz 홈(시장 지도 + 자산군 표 + 경제 캘린더)과 TradingView 마켓 개요(자산군별 표 +
스파크라인)를 따른다. 시세는 yfinance / FinanceDataReader. LLM을 쓰지 않는다 — 요약은 숫자로만 쓴다.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import calendar_data as cal

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "market"
OUT = ROOT / "outputs"
router = APIRouter()

BOARD = [
    ("지수", [("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ"), ("^GSPC", "S&P 500"), ("^IXIC", "나스닥"),
             ("^DJI", "다우"), ("^SOX", "필라델피아 반도체"), ("^N225", "닛케이 225")]),
    ("환율·금리", [("KRW=X", "원/달러"), ("JPYKRW=X", "원/100엔"), ("DX-Y.NYB", "달러인덱스"), ("^TNX", "미 10년물 금리")]),
    ("변동성", [("^VIX", "VIX")]),
    ("원자재", [("CL=F", "WTI 유가"), ("GC=F", "금")]),
    ("크립토", [("BTC-USD", "비트코인"), ("ETH-USD", "이더리움"), ("SOL-USD", "솔라나")]),
]
SCALE = {"JPYKRW=X": 100}                       # 원/엔은 100엔 기준으로 보여 준다

_mem: dict = {}


def _cached(key: str, ttl: int, fn):
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _mem[key] = (time.time(), v)
    return v


def _yf(symbols: list[str], period: str = "6mo") -> dict[str, pd.DataFrame]:
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(symbols, period=period, progress=False, auto_adjust=False, group_by="ticker", threads=True)
    out = {}
    for s in symbols:
        try:
            h = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            h = h.dropna(subset=["Close"])
            h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
            out[s] = h
        except KeyError:
            pass
    return out


def _chg(c: pd.Series, n: int) -> float | None:
    return float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100) if len(c) > n else None


# ------------------------------------------------------------------ 시세판
def _board() -> dict:
    syms = [s for _, items in BOARD for s, _ in items]
    data = _yf(syms, "6mo")
    groups = []
    for g, items in BOARD:
        rows = []
        for s, label in items:
            h = data.get(s)
            if h is None or h.empty:
                rows.append({"symbol": s, "label": label, "last": None})
                continue
            c = h["Close"] * SCALE.get(s, 1)
            ytd = c[c.index >= pd.Timestamp(date.today().year, 1, 1)]
            rows.append({"symbol": s, "label": label, "last": float(c.iloc[-1]), "asof": f"{c.index[-1]:%Y-%m-%d}",
                         "d1": _chg(c, 1), "w1": _chg(c, 5), "m1": _chg(c, 21),
                         "ytd": float((c.iloc[-1] / ytd.iloc[0] - 1) * 100) if len(ytd) > 1 else None,
                         "abs_w1": float(c.iloc[-1] - c.iloc[-6]) if len(c) > 5 else None,
                         "abs_m1": float(c.iloc[-1] - c.iloc[-22]) if len(c) > 21 else None,
                         "spark": [round(float(v), 4) for v in c.tail(44)]})
        groups.append({"group": g, "rows": rows})
    return {"groups": groups, "fetched_at": datetime.now().isoformat(timespec="minutes")}


@router.get("/api/market/board")
def board():
    try:
        return _cached("board", 600, _board)
    except Exception as e:
        raise HTTPException(502, f"시세 조회 실패: {e}")


# ------------------------------------------------------------------ 요약 (숫자만)
def _row(b: dict, sym: str) -> dict:
    return next((r for g in b["groups"] for r in g["rows"] if r["symbol"] == sym), {})


def _summary(period: str) -> dict:
    b = _cached("board", 600, _board)
    k = "w1" if period == "week" else "m1"
    ka = "abs_w1" if period == "week" else "abs_m1"
    word = "최근 1주" if period == "week" else "최근 1개월"
    f = lambda r: f"{r[k]:+.2f}%" if r.get(k) is not None else "—"  # noqa: E731
    ks, kq, sp, nq, sox = (_row(b, s) for s in ("^KS11", "^KQ11", "^GSPC", "^IXIC", "^SOX"))
    fx, dxy, vix, tnx, btc = (_row(b, s) for s in ("KRW=X", "DX-Y.NYB", "^VIX", "^TNX", "BTC-USD"))
    L = [f"{word} 한국은 KOSPI {f(ks)}, KOSDAQ {f(kq)}. 미국은 S&P 500 {f(sp)}, 나스닥 {f(nq)}."]
    if sox.get(k) is not None:
        L.append(f"필라델피아 반도체 {f(sox)} — 국내 반도체·장비주 방향을 가늠하는 지표다.")
    if fx.get("last"):
        L.append(f"원/달러 {fx['last']:,.1f}원 ({fx.get(ka, 0):+,.1f}원), 달러인덱스 {dxy.get('last', 0):.2f} ({f(dxy)}).")
    if vix.get("last"):
        v = vix["last"]
        zone = "낮음(15 미만)" if v < 15 else "보통(15~20)" if v < 20 else "경계(20~30)" if v < 30 else "공포(30 이상)"
        L.append(f"VIX {v:.1f} ({vix.get(ka, 0):+.1f}p), 구간은 {zone}.")
    if tnx.get("last"):
        L.append(f"미 10년물 금리 {tnx['last']:.2f}% ({tnx.get(ka, 0) * 100:+.0f}bp).")
    if btc.get("last"):
        L.append(f"비트코인 ${btc['last']:,.0f} ({f(btc)}).")
    tags = []
    if ks.get(k) is not None and sp.get(k) is not None:
        tags.append("한국 강세" if ks[k] > sp[k] else "미국 강세")
    if vix.get("last"):
        tags.append("위험 선호" if vix["last"] < 18 else "위험 회피" if vix["last"] > 24 else "중립")
    if fx.get(ka) is not None:
        tags.append("원화 약세" if fx[ka] > 0 else "원화 강세")
    # 다가오는 일정
    today = date.today()
    horizon = today + timedelta(days=7 if period == "week" else 31)
    ev = [e for e in _events(today, horizon) if e["kind"] in ("휴장", "FOMC", "실적", "일정")]
    if ev:
        L.append("다가오는 일정: " + ", ".join(f"{e['date'][5:].replace('-', '/')} {e['title']}" for e in ev[:6]) + ".")
    return {"period": period, "lines": L, "tags": tags, "asof": b["fetched_at"]}


@router.get("/api/market/summary")
def summary(period: str = "week"):
    if period not in ("week", "month"):
        raise HTTPException(400)
    try:
        return _summary(period)
    except Exception as e:
        raise HTTPException(502, f"요약 실패: {e}")


# ------------------------------------------------------------------ 히트맵
def _heat_kr() -> dict:
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])]
    # 우선주·스팩 제외 (screener와 같은 기준의 간이판)
    df = df[~df["Name"].str.contains(r"스팩|\d+우B?$|우$|우B$|리츠", regex=True)]
    tiles = []
    for mk, n in (("KOSPI", 70), ("KOSDAQ", 40)):
        top = df[df["Market"] == mk].nlargest(n, "Marcap")
        for _, r in top.iterrows():
            tiles.append({"group": mk, "ticker": r["Code"], "label": r["Name"], "cap": float(r["Marcap"]),
                          "d1": float(r["ChagesRatio"]) if pd.notna(r.get("ChagesRatio")) else None})
    # 1주 수익률: 상위 종목만 일봉으로 계산 (하루 캐시)
    from app.portfolio import daily
    def w1(t):
        try:
            c = daily("KR", t["ticker"], date.today() - timedelta(days=20))["Close"]
            return _chg(c, 5)
        except Exception:
            return None
    with ThreadPoolExecutor(8) as ex:
        for t, v in zip(tiles, ex.map(w1, tiles)):
            t["w1"] = v
    return {"market": "KR", "grouping": "시장 (업종 데이터 소스가 막혀 있어 KOSPI/KOSDAQ로 묶는다)", "tiles": tiles}


def _heat_us() -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    import yaml
    from screener import fetch_us_marketcap
    uni = list(csv.DictReader((ROOT / "data" / "us_universe.csv").open(encoding="utf-8")))
    syms = [u["symbol"] for u in uni]
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    ref = pd.Timestamp(date.today() - timedelta(days=date.today().weekday()))       # 주 1회 시총 갱신
    caps = fetch_us_marketcap(syms, ref, cfg, True)
    top = sorted(uni, key=lambda u: -(caps.get(u["symbol"]) or 0))[:150]
    data = _yf([u["symbol"] for u in top], "1mo")
    tiles = []
    for u in top:
        h = data.get(u["symbol"])
        if h is None or h.empty:
            continue
        c = h["Close"]
        tiles.append({"group": u["sector"], "ticker": u["symbol"], "label": u["symbol"], "name": u["name"],
                      "cap": float(caps.get(u["symbol"]) or 0), "d1": _chg(c, 1), "w1": _chg(c, 5)})
    return {"market": "US", "grouping": "S&P 500 섹터 (GICS)", "tiles": tiles}


@router.get("/api/market/heatmap")
def heatmap(mkt: str = "US"):
    if mkt not in ("KR", "US"):
        raise HTTPException(400)
    try:
        return _cached(f"heat_{mkt}", 1800, _heat_kr if mkt == "KR" else _heat_us)
    except Exception as e:
        raise HTTPException(502, f"히트맵 실패: {e}")


# ------------------------------------------------------------------ 캘린더
def _earnings() -> list[dict]:
    """이번 주차 미국 후보 20종목의 다음 실적 발표일 (yfinance)."""
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    f = OUT / f"wk{cfg['run']['week_no']}" / "US" / "candidates.csv"
    if not f.exists():
        return []
    import yfinance as yf
    syms = [r["ticker"] for r in csv.DictReader(f.open(encoding="utf-8-sig"))]

    def one(s):
        try:
            c = yf.Ticker(s).calendar or {}
            d = (c.get("Earnings Date") or [None])[0]
            return {"date": str(d), "kind": "실적", "title": f"{s} 실적", "market": "US"} if d else None
        except Exception:
            return None
    with ThreadPoolExecutor(8) as ex:
        return [x for x in ex.map(one, syms) if x]


def _pipeline_dates() -> list[dict]:
    ev = []
    for wk in OUT.glob("wk*"):
        if not wk.name[2:].isdigit():
            continue
        for m in ("KR", "US"):
            sm = wk / m / "screen_meta.json"
            if sm.exists():
                d = json.loads(sm.read_text(encoding="utf-8"))
                if d.get("buy_date"):
                    ev.append({"date": d["buy_date"], "kind": "주차", "title": f"{wk.name[2:]}주차 매수 ({m})", "market": m})
                if d.get("sell_date"):
                    ev.append({"date": d["sell_date"], "kind": "주차", "title": f"{wk.name[2:]}주차 매도 ({m})", "market": m})
    import yaml
    run = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["run"]
    b = date.fromisoformat(str(run["buy_date"]))
    ev.append({"date": (b - timedelta(days=1)).isoformat(), "kind": "주차", "title": f"{run['week_no']}주차 제출 마감", "market": ""})
    return ev


def _events(start: date, end: date) -> list[dict]:
    ev = []
    for m, hs in cal.HOLIDAYS.items():
        ev += [{"date": d, "kind": "휴장", "title": f"{'한국' if m == 'KR' else '미국'} 휴장: {t}", "market": m} for d, t in hs.items()]
    ev += [{"date": d, "kind": "FOMC", "title": "FOMC 결정 (한국시간 다음 날 새벽)", "market": "US"} for d in cal.FOMC]
    ev += _pipeline_dates()
    ev += [{**e, "kind": e.get("kind") or "일정", "user": True} for e in cal.user_events()]
    try:
        ev += _cached("earnings", 6 * 3600, _earnings)
    except Exception:
        pass
    s, e = start.isoformat(), end.isoformat()
    return sorted((x for x in ev if s <= x["date"][:10] <= e), key=lambda x: x["date"])


@router.get("/api/market/calendar")
def calendar(month: str | None = None):
    m = date.fromisoformat((month or date.today().strftime("%Y-%m")) + "-01")
    start = m - timedelta(days=m.weekday())
    end = start + timedelta(days=41)
    return {"month": m.strftime("%Y-%m"), "start": start.isoformat(), "events": _events(start, end)}


class EventIn(BaseModel):
    date: str
    title: str
    kind: str = "일정"
    market: str = ""


@router.post("/api/market/calendar")
def add_event(e: EventIn):
    date.fromisoformat(e.date)
    ev = cal.user_events()
    ev.append({"id": uuid.uuid4().hex[:8], **e.model_dump()})
    cal.save_user_events(ev)
    return {"ok": True}


@router.delete("/api/market/calendar/{eid}")
def del_event(eid: str):
    cal.save_user_events([x for x in cal.user_events() if x.get("id") != eid])
    return {"ok": True}
