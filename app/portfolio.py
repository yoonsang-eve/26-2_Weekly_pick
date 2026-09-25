"""포트폴리오 계산 — 수익 곡선, 배분, 종목 상세(차트·매매·이벤트).

증권사에서 받는 것은 '지금 잔고'와 '체결 내역'뿐이다. 과거 평가금액 곡선은
체결 내역 + 일별 종가로 되짚어 만든다:

    현금(t)  = 지금 현금 + (t 이후 매수 금액) - (t 이후 매도 금액)
    평가(t)  = Σ 보유수량(t) × 종가(t)      (해외는 그날 환율로 원화 환산)
    총자산(t) = 현금(t) + 평가(t)

입출금과 수수료는 체결 내역에 없어서 반영되지 않는다 — 화면에 그렇게 적는다.

demo=True면 지난 추천 종목을 추천일 시가에 샀다고 가정한 예시 포트폴리오를 만든다
(화면 확인용. 실제 계좌 아님).
"""
from __future__ import annotations

import csv
import json
import math
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
PCACHE = ROOT / "cache" / "prices"


# ------------------------------------------------------------------ 시세 (캐시)
def _tk(t) -> str:
    s = str(t).strip()
    return s.zfill(6) if s.isdigit() else s


def daily(market: str, ticker: str, start: date) -> pd.DataFrame:
    """일봉 OHLCV. 하루 한 번만 받아 cache/prices/에 둔다."""
    PCACHE.mkdir(parents=True, exist_ok=True)
    f = PCACHE / f"{market}_{ticker.replace('^', '_')}_{date.today():%Y%m%d}.parquet"
    if f.exists():
        h = pd.read_parquet(f)
    else:
        s = date.today() - timedelta(days=400)
        if market == "KR":
            import FinanceDataReader as fdr
            h = fdr.DataReader(ticker, s)
        else:
            import yfinance as yf
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                h = yf.download(ticker, start=s, progress=False, auto_adjust=False)
            if isinstance(h.columns, pd.MultiIndex):
                h.columns = h.columns.get_level_values(0)
        h = h[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in h.columns]].dropna(subset=["Close"])
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        if not h.empty:
            h.to_parquet(f)
    return h[h.index >= pd.Timestamp(start)]


def fx_usdkrw(start: date) -> pd.Series:
    h = daily("US", "KRW=X", start)
    return h["Close"] if not h.empty else pd.Series(dtype=float)


def kospi(start: date) -> pd.Series:
    # FDR 지수 데이터는 며칠 늦게 들어온다 (settle.py와 같은 이유로 yfinance를 쓴다)
    h = daily("US", "^KS11", start)
    return h["Close"] if not h.empty else pd.Series(dtype=float)


# ------------------------------------------------------------------ 추천 기록
def _log() -> dict:
    f = ROOT / "log.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8-sig") as fh:
        return {(int(r["week"]), r["market"]): r for r in csv.DictReader(fh)}


def _buy_date(p: dict) -> str | None:
    """매수일. 정산 기록 > screen_meta > 기준일(금) 다음 월요일."""
    if p["log"]:
        return p["log"]["buy_date"]
    m = p["meta"]
    if m.get("buy_date"):
        return m["buy_date"]
    if m.get("ref_date"):
        d = date.fromisoformat(m["ref_date"]) + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.isoformat()
    return None


def picks() -> list[dict]:
    out, log = [], _log()
    for wk in sorted(OUT.glob("wk*")):
        if not wk.name[2:].isdigit():
            continue
        for m in ("KR", "US"):
            d = wk / m
            if (d / "pick.json").exists():
                p = json.loads((d / "pick.json").read_text(encoding="utf-8"))
                sm = json.loads((d / "screen_meta.json").read_text(encoding="utf-8")) if (d / "screen_meta.json").exists() else {}
                out.append({"week": int(wk.name[2:]), "market": m, "pick": p, "meta": sm,
                            "log": log.get((int(wk.name[2:]), m))})
    return out


def pipeline_history(market: str, ticker: str) -> list[dict]:
    """이 종목이 파이프라인에 등장한 주차들 (후보/최종후보/추천)."""
    ev = []
    for wk in sorted(OUT.glob("wk*"), key=lambda p: int(p.name[2:]) if p.name[2:].isdigit() else 0):
        if not wk.name[2:].isdigit():
            continue
        d, w = wk / market, int(wk.name[2:])
        cand = d / "candidates.csv"
        if not cand.exists():
            continue
        with cand.open(encoding="utf-8-sig") as fh:
            if not any(_tk(r["ticker"]) == ticker for r in csv.DictReader(fh)):
                continue
        pick = json.loads((d / "pick.json").read_text(encoding="utf-8")) if (d / "pick.json").exists() else {}
        sm = json.loads((d / "screen_meta.json").read_text(encoding="utf-8")) if (d / "screen_meta.json").exists() else {}
        ret = None
        if (d / "cohort.csv").exists():
            with (d / "cohort.csv").open(encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if _tk(r["ticker"]) == ticker and r.get("ret_pct"):
                        ret = float(r["ret_pct"])
        when = sm.get("buy_date") or sm.get("ref_date") or ""
        if _tk(pick.get("ticker", "")) == ticker:
            ev.append({"date": when, "kind": "추천", "title": f"{w}주차 최종 추천",
                       "body": f"촉매: {pick.get('catalyst', '')}  /  리스크: {pick.get('primary_risk', '')}",
                       "value": ret, "week": w})
            continue
        rej = next((r for r in pick.get("rejected", []) if _tk(r.get("ticker")) == ticker), None)
        if rej:
            ev.append({"date": when, "kind": "추천", "title": f"{w}주차 최종후보에서 탈락",
                       "body": rej.get("reason", ""), "value": ret, "week": w})
        else:
            ev.append({"date": when, "kind": "추천", "title": f"{w}주차 스크리닝 후보",
                       "body": "Analyst 단계에서 탈락", "value": ret, "week": w})
    return ev


# ------------------------------------------------------------------ 예시 포트폴리오
DEMO_CASH0 = 10_000_000


def demo_state() -> dict:
    """지난 추천을 추천일 시가에 샀다고 가정. 첫 추천은 정산일에 팔고, 나머지는 보유."""
    ps = picks()
    trades = []
    start = min((date.fromisoformat(_buy_date(p) or str(date.today())) for p in ps),
                default=date.today()) - timedelta(days=10)
    fx = fx_usdkrw(start)
    for i, p in enumerate(ps):
        t, m = _tk(p["pick"]["ticker"]), p["market"]
        buy = _buy_date(p)
        if not buy:
            continue
        h = daily(m, t, date.fromisoformat(buy))
        if h.empty:
            continue
        px = float(h.iloc[0]["Open"])
        budget = 3_000_000 if m == "KR" else 2_000
        qty = math.floor(budget / px)
        trades.append({"date": f"{h.index[0]:%Y-%m-%d}", "time": "090000", "side": "buy", "market": m, "ticker": t,
                       "name": p["pick"].get("name", ""), "qty": qty, "price": px, "amount": qty * px})
        if i == 0 and p["log"]:                    # 첫 추천은 평가 종료일에 매도했다고 둔다 (실현손익 예시)
            sp = float(p["log"]["sell_close"])
            trades.append({"date": p["log"]["sell_date"], "time": "153000", "side": "sell", "market": m, "ticker": t,
                           "name": p["pick"].get("name", ""), "qty": qty, "price": sp, "amount": qty * sp})
    cash = DEMO_CASH0
    for tr in trades:
        k = _krw(tr["amount"], tr["market"], tr["date"], fx)
        cash += -k if tr["side"] == "buy" else k
    pos = []
    for (m, t), g in _group(trades).items():
        qty = sum(x["qty"] if x["side"] == "buy" else -x["qty"] for x in g)
        if qty <= 0:
            continue
        buys = [x for x in g if x["side"] == "buy"]
        avg = sum(x["amount"] for x in buys) / sum(x["qty"] for x in buys)
        h = daily(m, t, date.today() - timedelta(days=10))
        price = float(h["Close"].iloc[-1]) if not h.empty else avg
        prev = float(h["Close"].iloc[-2]) if len(h) > 1 else price
        pos.append({"market": m, "ticker": t, "name": g[0]["name"], "qty": qty, "avg_price": avg, "price": price,
                    "value": qty * price, "pnl": qty * (price - avg), "pnl_pct": (price / avg - 1) * 100,
                    "currency": "KRW" if m == "KR" else "USD", "day_pct": (price / prev - 1) * 100})
    return {"demo": True, "env": "예시", "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cash_krw": cash, "positions": pos, "trades": trades, "raw_summary": {}}


def _group(trades):
    g: dict = {}
    for t in trades:
        g.setdefault((t["market"], t["ticker"]), []).append(t)
    return g


def _krw(amount: float, market: str, day: str, fx: pd.Series) -> float:
    if market == "KR":
        return amount
    if fx.empty:
        return amount * 1400
    r = fx[fx.index <= pd.Timestamp(day)]
    return amount * float(r.iloc[-1] if len(r) else fx.iloc[0])


# ------------------------------------------------------------------ 요약·배분·곡선
def summarize(state: dict) -> dict:
    fx = fx_usdkrw(date.today() - timedelta(days=10)) if any(p["currency"] == "USD" for p in state["positions"]) else pd.Series(dtype=float)
    rate = float(fx.iloc[-1]) if not fx.empty else None
    pos = []
    for p in state["positions"]:
        k = p["value"] if p["currency"] == "KRW" else p["value"] * (rate or 1400)
        cost = p["qty"] * p["avg_price"] * (1 if p["currency"] == "KRW" else (rate or 1400))
        pos.append({**p, "value_krw": k, "cost_krw": cost})
    invested = sum(p["cost_krw"] for p in pos)
    value = sum(p["value_krw"] for p in pos)
    total = state["cash_krw"] + value
    for p in pos:
        p["weight"] = p["value_krw"] / total * 100 if total else 0
    raw = state.get("raw_summary") or {}
    prev = float(raw.get("bfdy_tot_asst_evlu_amt") or 0)
    day = None
    if prev > 0:
        day = {"amount": total - prev, "pct": (total / prev - 1) * 100}
    elif pos:
        dv = sum(p["value_krw"] * (p.get("day_pct") or 0) / (100 + (p.get("day_pct") or 0)) for p in pos)
        day = {"amount": dv, "pct": dv / (total - dv) * 100 if total - dv else 0}
    realized = _realized(state["trades"])
    by_market = {}
    for p in pos:
        by_market[p["market"]] = by_market.get(p["market"], 0) + p["value_krw"]
    alloc = {"holdings": sorted(({"key": p["ticker"], "label": p["name"], "value": p["value_krw"]} for p in pos), key=lambda x: -x["value"])
                         + [{"key": "cash", "label": "현금", "value": state["cash_krw"]}],
             "market": [{"key": k, "label": {"KR": "한국 주식", "US": "미국 주식"}[k], "value": v} for k, v in by_market.items()]
                       + [{"key": "cash", "label": "현금", "value": state["cash_krw"]}]}
    return {"total": total, "cash": state["cash_krw"], "invested": invested, "value": value,
            "unrealized": value - invested, "unrealized_pct": (value / invested - 1) * 100 if invested else None,
            "realized": realized, "day": day, "usdkrw": rate, "positions": sorted(pos, key=lambda p: -p["value_krw"]),
            "allocation": alloc}


def _realized(trades: list[dict]) -> float:
    """이동평균 단가 기준 실현손익 (원화, 해외는 체결일 환율)."""
    fx = fx_usdkrw(date.today() - timedelta(days=400)) if any(t["market"] == "US" for t in trades) else pd.Series(dtype=float)
    book: dict = {}
    pnl = 0.0
    for t in sorted(trades, key=lambda x: (x["date"], x["time"])):
        q, c = book.get(t["ticker"], (0.0, 0.0))
        if t["side"] == "buy":
            book[t["ticker"]] = (q + t["qty"], c + t["amount"])
        elif q > 0:
            avg = c / q
            pnl += _krw((t["price"] - avg) * t["qty"], t["market"], t["date"], fx)
            book[t["ticker"]] = (q - t["qty"], c - avg * t["qty"])
    return pnl


def equity_curve(state: dict, days: int = 90) -> dict:
    trades = state["trades"]
    first = min((date.fromisoformat(t["date"]) for t in trades), default=date.today() - timedelta(days=days))
    start = min(first, date.today() - timedelta(days=days)) - timedelta(days=3)
    bench = kospi(start)
    if bench.empty:
        return {"points": [], "note": "KOSPI 시세를 받지 못했다."}
    idx = bench.index
    fx = fx_usdkrw(start) if any(t["market"] == "US" for t in trades) else pd.Series(dtype=float)
    closes = {(m, t): daily(m, t, start)["Close"] for (m, t) in _group(trades)}
    pts = []
    for day in idx:
        ds = f"{day:%Y-%m-%d}"
        cash = state["cash_krw"]
        val = 0.0
        for tr in trades:
            k = _krw(tr["amount"], tr["market"], tr["date"], fx)
            if tr["date"] > ds:                                # 이후에 일어난 매매를 되돌린다
                cash += k if tr["side"] == "buy" else -k
        for key, g in _group(trades).items():
            q = sum(x["qty"] if x["side"] == "buy" else -x["qty"] for x in g if x["date"] <= ds)
            if q > 0:
                c = closes[key][closes[key].index <= day]
                if len(c):
                    val += _krw(q * float(c.iloc[-1]), key[0], ds, fx)
        pts.append({"d": ds, "equity": cash + val, "bench": float(bench.loc[day])})
    b0 = pts[0]["bench"] if pts else 1
    e0 = pts[0]["equity"] if pts else 1
    for p in pts:
        p["ret"] = (p["equity"] / e0 - 1) * 100 if e0 else 0
        p["bench_ret"] = (p["bench"] / b0 - 1) * 100
    return {"points": pts, "note": "체결 내역과 일별 종가로 되짚은 값이다. 입출금·수수료·세금은 반영되지 않는다."}


# ------------------------------------------------------------------ 종목 상세
def holding_detail(state: dict, market: str, ticker: str) -> dict:
    ticker = _tk(ticker)
    h = daily(market, ticker, date.today() - timedelta(days=370))
    if h.empty:
        return {"error": "시세를 받지 못했다."}
    h = h.copy()
    h["ma20"] = h["Close"].rolling(20).mean()
    h["ma60"] = h["Close"].rolling(60).mean()
    six = h[h.index >= pd.Timestamp(date.today() - timedelta(days=186))]
    bars = [{"d": f"{i:%Y-%m-%d}", "o": r.Open, "h": r.High, "l": r.Low, "c": r.Close, "v": float(r.Volume),
             "ma20": None if pd.isna(r.ma20) else r.ma20, "ma60": None if pd.isna(r.ma60) else r.ma60}
            for i, r in six.iterrows()]
    pos = next((p for p in state["positions"] if _tk(p["ticker"]) == ticker), None)
    if pos:
        sm = summarize(state)
        pos = next((p for p in sm["positions"] if _tk(p["ticker"]) == ticker), pos)   # 비중 포함
    trades = [t for t in state["trades"] if _tk(t["ticker"]) == ticker]
    nd = 0 if market == "KR" else 2
    f = lambda v: f"{v:,.{nd}f}"  # noqa: E731
    events = []
    for t in trades:
        events.append({"date": t["date"], "kind": "매매", "title": f"{'매수' if t['side'] == 'buy' else '매도'} {t['qty']:,.0f}주",
                       "body": f"{f(t['price'])} × {t['qty']:,.0f} = {f(t['amount'])}", "value": None})
    events += pipeline_history(market, ticker)
    # 가격 이벤트: 6개월 안 일간 변동 상위 3일 (거래량 배수 포함), 52주 신고가
    r = h["Close"].pct_change() * 100
    vr = h["Volume"] / h["Volume"].rolling(20).mean()
    recent = r[r.index >= six.index[0]].dropna()
    for d in recent.abs().nlargest(3).index:
        events.append({"date": f"{d:%Y-%m-%d}", "kind": "가격", "title": f"하루 {r[d]:+.1f}%",
                       "body": f"거래량 20일 평균의 {vr[d]:.1f}배" if pd.notna(vr[d]) else "", "value": None})
    hi = h["Close"].rolling(250, min_periods=120).max()
    nh = h[(h["Close"] >= hi) & (h.index >= six.index[0])]
    if len(nh):
        d = nh.index[-1]
        events.append({"date": f"{d:%Y-%m-%d}", "kind": "가격", "title": "52주 신고가",
                       "body": f"종가 {f(h.loc[d, 'Close'])}", "value": None})
    events.sort(key=lambda e: e["date"], reverse=True)

    # 숫자 요약 (LLM 없음)
    last = float(h["Close"].iloc[-1])
    lines = []
    if pos:
        first_buy = min((t["date"] for t in trades if t["side"] == "buy"), default=None)
        held = (date.today() - date.fromisoformat(first_buy)).days if first_buy else None
        lines.append(f"평단 {f(pos['avg_price'])} → 현재 {f(last)}, {pos['pnl_pct']:+.2f}%"
                     + (f" (보유 {held}일)" if held is not None else "") + ".")
        if first_buy:
            k = kospi(date.fromisoformat(first_buy))
            if len(k) > 1:
                lines.append(f"같은 기간 KOSPI {(k.iloc[-1] / k.iloc[0] - 1) * 100:+.2f}%.")
    peak = float(six["Close"].max())
    lines.append(f"6개월 고점 {f(peak)} 대비 {(last / peak - 1) * 100:+.1f}%.")
    ma20 = h["ma20"].iloc[-1]
    if pd.notna(ma20):
        lines.append(f"20일선 {'위' if last >= ma20 else '아래'} ({(last / ma20 - 1) * 100:+.1f}%).")
    vol = r.tail(20).std() * (252 ** .5)
    if pd.notna(vol):
        lines.append(f"최근 20일 변동성(연율) {vol:.0f}%.")
    rec = [e for e in events if e["kind"] == "추천"]
    if rec:
        lines.append(f"파이프라인 등장 {len(rec)}회 — 최근: {rec[0]['title']}.")
    return {"market": market, "ticker": ticker, "name": (pos or {}).get("name") or (trades[0]["name"] if trades else ticker),
            "position": pos, "bars": bars, "trades": trades, "events": events, "summary": lines,
            "currency": "KRW" if market == "KR" else "USD"}
