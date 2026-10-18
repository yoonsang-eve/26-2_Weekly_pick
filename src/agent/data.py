"""가격 데이터 계층 — 에이전트 도구가 읽는 표를 만든다. LLM 호출 없음.

  stock_panel   주식 패널. KR은 거래대금 상위 N(스크리너와 같은 수집 범위), US는 S&P500 ∪ NDX.
                필터를 걸지 않고 지표만 붙인다 — 무엇을 거를지는 에이전트가 screen_stocks로 정한다.
  etf_panel     ETF. KR은 상장 ETF 중 거래대금 상위 + 인버스·레버리지·단기채, US는 고정 목록.
  overview      지수·금리·환율·변동성·원자재 시세표
  analogs       '지금과 비슷한 주간' 다음 주 지수 수익률 분포 (15년). 국면 판단의 기준선

스크리너(src/screener.py)의 수집 함수와 캐시를 그대로 쓴다. 같은 기준일이면 다시 받지 않는다.
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent.parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))
import screener  # noqa: E402
import us_universe  # noqa: E402

CACHE = ROOT / "cache" / "agent"
KR_SECTORS = ROOT / "data" / "kr_sectors.csv"
BENCH = {"KR": "^KS11", "US": "^GSPC"}

# 미국 ETF는 상장 종목이 수천 개라 목록을 고정한다. 지수·섹터·채권/현금성·인버스·레버리지·원자재를
# 한 번씩은 고를 수 있게 했다. 추가는 여기에 한 줄.
US_ETFS = [
    ("SPY", "S&P 500", "index"), ("QQQ", "Nasdaq 100", "index"), ("IWM", "Russell 2000", "index"),
    ("DIA", "Dow 30", "index"), ("RSP", "S&P 500 동일가중", "index"),
    ("XLK", "기술", "sector"), ("XLF", "금융", "sector"), ("XLE", "에너지", "sector"),
    ("XLV", "헬스케어", "sector"), ("XLI", "산업재", "sector"), ("XLY", "임의소비재", "sector"),
    ("XLP", "필수소비재", "sector"), ("XLU", "유틸리티", "sector"), ("XLB", "소재", "sector"),
    ("XLRE", "부동산", "sector"), ("XLC", "커뮤니케이션", "sector"),
    ("SMH", "반도체", "sector"), ("SOXX", "반도체", "sector"), ("IGV", "소프트웨어", "sector"),
    ("XBI", "바이오", "sector"), ("KRE", "지역은행", "sector"), ("ITB", "주택건설", "sector"),
    ("XRT", "소매", "sector"), ("ITA", "방산·항공", "sector"), ("TAN", "태양광", "sector"),
    ("URA", "우라늄", "sector"), ("ARKK", "혁신성장", "sector"), ("KWEB", "중국 인터넷", "foreign"),
    ("FXI", "중국 대형주", "foreign"), ("EWY", "한국", "foreign"), ("EWJ", "일본", "foreign"),
    ("EEM", "신흥국", "foreign"), ("INDA", "인도", "foreign"),
    ("GLD", "금", "commodity"), ("SLV", "은", "commodity"), ("USO", "원유", "commodity"),
    ("GDX", "금광", "sector"), ("UUP", "달러 강세", "commodity"),
    ("TLT", "미 장기채", "bond_cash"), ("IEF", "미 중기채", "bond_cash"), ("SHY", "미 단기채", "bond_cash"),
    ("BIL", "미 초단기 국채(현금성)", "bond_cash"), ("SGOV", "미 0-3개월 국채(현금성)", "bond_cash"),
    ("SH", "S&P 500 인버스 1X", "inverse"), ("PSQ", "Nasdaq 100 인버스 1X", "inverse"),
    ("DOG", "Dow 인버스 1X", "inverse"), ("RWM", "Russell 2000 인버스 1X", "inverse"),
    ("SDS", "S&P 500 인버스 2X", "inverse,leveraged"), ("QID", "Nasdaq 100 인버스 2X", "inverse,leveraged"),
    ("SQQQ", "Nasdaq 100 인버스 3X", "inverse,leveraged"), ("SPXU", "S&P 500 인버스 3X", "inverse,leveraged"),
    ("SOXS", "반도체 인버스 3X", "inverse,leveraged"),
    ("SSO", "S&P 500 2X", "leveraged"), ("QLD", "Nasdaq 100 2X", "leveraged"),
    ("TQQQ", "Nasdaq 100 3X", "leveraged"), ("UPRO", "S&P 500 3X", "leveraged"),
    ("SOXL", "반도체 3X", "leveraged"), ("TNA", "Russell 2000 3X", "leveraged"),
]

OVERVIEW = [
    ("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ"), ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"),
    ("^RUT", "Russell 2000"), ("^SOX", "필라델피아 반도체"), ("^N225", "닛케이"), ("000001.SS", "상해종합"),
    ("^VIX", "VIX"), ("^TNX", "미 10년물(%)"), ("DX-Y.NYB", "달러인덱스"), ("KRW=X", "원/달러"),
    ("CL=F", "WTI"), ("GC=F", "금"), ("HG=F", "구리"), ("BTC-USD", "비트코인"),
]


# ------------------------------------------------------------------ 공통

def _quiet_yf(symbols, **kw) -> pd.DataFrame:
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yf.download(symbols, progress=False, auto_adjust=False, threads=True,
                           group_by="ticker", actions=False, **kw)


def yf_hist(symbols: list[str], start=None, end=None, period: str | None = None) -> dict[str, pd.DataFrame]:
    """yfinance 일괄 조회 → 심볼별 OHLCV. 인덱스는 tz 없는 날짜."""
    if not symbols:
        return {}
    kw = {"period": period} if period else {"start": start, "end": end}
    raw = _quiet_yf(list(symbols), **kw)
    out = {}
    for s in symbols:
        try:
            h = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        h = h.dropna(subset=["Close"])
        if h.empty:
            continue
        h = h.copy()
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        out[s] = h
    return out


def _ret(s: pd.Series, n: int) -> float:
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n and s.iloc[-1 - n] > 0 else math.nan


def indicators(h: pd.DataFrame, bench: pd.Series | None = None, ret_col: str = "Close") -> dict | None:
    """일봉 하나에서 지표 묶음. 스크리너 지표(ret_mom·ret5·atr20 등)와 같은 정의를 쓴다.

    모멘텀은 최근 20영업일을 건너뛴 120영업일 구간이다 (CLAUDE.md 4절). 국면에 따라 쓰는 지표가
    달라지므로(상승장: 모멘텀·베타 / 하락장: 저변동·저베타) 하나로 줄이지 않고 다 붙인다.
    """
    if h is None or len(h) < 65:
        return None
    c = h["Close"].astype(float)
    r = (h[ret_col] if ret_col in h.columns else c).astype(float)
    close = float(c.iloc[-1])
    if not close > 0:
        return None
    hi, lo = h["High"].astype(float), h["Low"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    atr20 = float(tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1])
    dr = r.pct_change().dropna()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] > 0 else 100.0
    vol = h["Volume"].astype(float) if "Volume" in h.columns else pd.Series(dtype=float)
    beta = math.nan
    if bench is not None and len(dr) > 40:
        b = bench.pct_change().dropna()
        j = pd.concat([dr, b], axis=1, join="inner").tail(60).dropna()
        if len(j) > 30 and j.iloc[:, 1].var() > 0:
            beta = float(j.cov().iloc[0, 1] / j.iloc[:, 1].var())
    return {
        "close": close,
        "adv20": float((c * vol).tail(20).mean()) if len(vol) else math.nan,
        "ret1": _ret(r, 1), "ret5": _ret(r, 5), "ret20": _ret(r, 20), "ret60": _ret(r, 60),
        "ret_mom": float(r.iloc[-21] / r.iloc[-141] - 1) if len(r) > 141 else math.nan,
        "pct_from_52wh": float((hi.tail(250).max() - close) / hi.tail(250).max()),
        "atr20_pct": atr20 / close,
        "vol20": float(dr.tail(20).std() * math.sqrt(252)),
        "beta60": beta,
        "rsi14": rsi,
        "ma20_gap": float(close / c.tail(20).mean() - 1),
        "ma60_gap": float(close / c.tail(60).mean() - 1),
        "vol_ratio": float(vol.tail(5).mean() / vol.tail(60).mean()) if len(vol) > 60 and vol.tail(60).mean() > 0 else math.nan,
        "last_date": f"{h.index[-1]:%Y-%m-%d}",
    }


def bench_close(market: str, ref: pd.Timestamp) -> pd.Series:
    h = yf_hist([BENCH[market]], start=(ref - timedelta(days=500)).date(), end=(ref + timedelta(days=1)).date())
    return h.get(BENCH[market], pd.DataFrame({"Close": []}))["Close"]


def ref_date(buy_date: str) -> pd.Timestamp:
    """매수일 직전 금요일. 실제 마지막 거래일은 받아온 데이터에서 정한다 (휴장·시차)."""
    return screener.ref_friday(buy_date)


# ------------------------------------------------------------------ KR 업종

def kr_sectors(codes: list[str], markets: dict[str, str], workers: int = 8) -> dict[str, str]:
    """종목코드 → 업종. KRX-DESC가 막혀 있어 yfinance info로 받고 data/kr_sectors.csv에 쌓아 둔다.

    업종은 거의 안 바뀌므로 한 번 받은 것은 다시 받지 않는다. 실패분은 빈칸 (탈락 아님).
    """
    have = {}
    if KR_SECTORS.exists():
        df = pd.read_csv(KR_SECTORS, dtype=str).fillna("")
        have = dict(zip(df["code"], df["sector"]))
    missing = [c for c in codes if c not in have]
    if missing:
        import yfinance as yf

        def one(code):
            sfx = ".KQ" if "KOSDAQ" in markets.get(code, "") else ".KS"
            try:
                i = yf.Ticker(code + sfx).info or {}
                s = " / ".join(x for x in (i.get("sector"), i.get("industry")) if x)
            except Exception:
                s = ""
            return code, s

        print(f"  KR 업종 조회 {len(missing)}종목 (최초 1회, data/kr_sectors.csv에 저장)")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            got = dict(ex.map(one, missing))
        have.update(got)
        KR_SECTORS.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"code": list(have), "sector": list(have.values())}).to_csv(KR_SECTORS, index=False, encoding="utf-8-sig")
    return {c: have.get(c, "") for c in codes}


# ------------------------------------------------------------------ 주식 패널

def _attach(hist: dict, bench: pd.Series, ret_col: str, ref: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for k, h in hist.items():
        ind = indicators(h, bench, ret_col)
        # 기준일보다 4일 넘게 오래된 마지막 봉은 거래정지·상장폐지로 본다
        if ind and pd.Timestamp(ind["last_date"]) >= ref - timedelta(days=4):
            rows.append({"ticker": k, **ind})
    return pd.DataFrame(rows)


def stock_panel(cfg: dict, market: str, buy_date: str, use_cache: bool = True) -> pd.DataFrame:
    ref = ref_date(buy_date)
    f = CACHE / f"panel_{market}_{ref:%Y%m%d}.parquet"
    if use_cache and f.exists():
        return pd.read_parquet(f)
    screener.force_http_timeout(float(cfg["data"].get("request_timeout_sec", 20)))
    bench = bench_close(market, ref)
    if market == "KR":
        uni = screener.build_universe(screener.load_listing(ref, use_cache), cfg)
        codes = uni.nlargest(int(cfg["data"]["prefetch_top_n_kr"]), "Amount")["Code"].tolist()
        print(f"  KR 히스토리 {len(codes)}종목")
        hist = screener.fetch_history(codes, ref, cfg, use_cache)
        ind = _attach(hist, bench, "Close", ref)
        d = uni.rename(columns={"Code": "ticker", "Name": "name", "Market": "exchange", "Marcap": "mktcap"})
        d = d[["ticker", "name", "exchange", "mktcap"]].merge(ind, on="ticker")
        d["sector"] = d["ticker"].map(kr_sectors(d["ticker"].tolist(), dict(zip(d["ticker"], d["exchange"]))))
    else:
        uni = us_universe.load(ROOT / cfg["universe"]["us"]["universe_csv"])
        print(f"  US 히스토리 {len(uni)}종목")
        hist = screener.fetch_us_history(uni["symbol"].tolist(), ref, cfg, use_cache)
        ind = _attach(hist, bench, "Adj Close", ref)
        caps = screener.fetch_us_marketcap(list(hist), ref, cfg, use_cache)
        d = uni.rename(columns={"symbol": "ticker", "index": "exchange"})[["ticker", "name", "exchange", "sector"]]
        d = d.merge(ind, on="ticker")
        d["mktcap"] = d["ticker"].map(caps)
        d = (d.assign(_co=d["name"].str.replace(r"\s*\(Class [A-Z]\)\s*$", "", regex=True).str.strip())
               .sort_values("adv20", ascending=False).drop_duplicates("_co").drop(columns="_co"))
    d.insert(0, "market", market)
    d["kind"] = "stock"
    d = d.reset_index(drop=True)
    f.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(f)
    return d


# ------------------------------------------------------------------ ETF

KR_TAGS = [
    ("inverse", ("인버스",)),
    ("leveraged", ("레버리지", "2X", "3X")),
    ("bond_cash", ("채권", "국채", "국고채", "CD금리", "머니마켓", "KOFR", "통안", "단기자금", "금리액티브", "회사채")),
    ("commodity", ("골드", "금현물", "금선물", "은선물", "원유", "WTI", "구리", "천연가스", "원자재")),
    ("foreign", ("미국", "나스닥", "S&P", "차이나", "중국", "일본", "인도", "베트남", "글로벌", "유로", "필라델피아")),
    ("index", ("200", "코스피", "코스닥", "KRX300", "TOP10")),
]


def kr_etf_tags(name: str) -> str:
    tags = [t for t, keys in KR_TAGS if any(k in name.upper() for k in (k.upper() for k in keys))]
    if "foreign" in tags and "index" in tags:
        tags.remove("index")            # 'S&P500' 같은 이름에 '200'이 걸리지 않게
    return ",".join(tags) or "sector"


def etf_panel(cfg: dict, market: str, buy_date: str, use_cache: bool = True, top_n: int = 160) -> pd.DataFrame:
    ref = ref_date(buy_date)
    f = CACHE / f"etf_{market}_{ref:%Y%m%d}.parquet"
    if use_cache and f.exists():
        return pd.read_parquet(f)
    screener.force_http_timeout(float(cfg["data"].get("request_timeout_sec", 20)))
    bench = bench_close(market, ref)
    if market == "KR":
        import FinanceDataReader as fdr
        lst = fdr.StockListing("ETF/KR")
        lst["tags"] = lst["Name"].map(kr_etf_tags)
        top = lst.nlargest(top_n, "Amount")
        # 인버스·레버리지·현금성은 거래대금이 작아도 국면 대응 수단이라 상위 400 안이면 넣는다
        special = lst.nlargest(400, "Amount")
        special = special[special["tags"].str.contains("inverse|bond_cash|leveraged")]
        lst = pd.concat([top, special]).drop_duplicates("Symbol")
        hist = screener.fetch_history(lst["Symbol"].tolist(), ref, cfg, use_cache)
        ind = _attach(hist, bench, "Close", ref)
        d = lst.rename(columns={"Symbol": "ticker", "Name": "name"})[["ticker", "name", "tags"]]
        d["mktcap"] = lst["MarCap"].values * 1e8          # 억원 → 원
        d = d.merge(ind, on="ticker")
        d["exchange"] = "KRX ETF"
    else:
        syms = [s for s, _, _ in US_ETFS]
        hist = yf_hist(syms, start=(ref - timedelta(days=420)).date(), end=(ref + timedelta(days=1)).date())
        hist = {k: v[v.index <= ref] for k, v in hist.items()}
        ind = _attach(hist, bench, "Adj Close", ref)
        d = pd.DataFrame(US_ETFS, columns=["ticker", "name", "tags"]).merge(ind, on="ticker")
        d["mktcap"] = math.nan
        d["exchange"] = "US ETF"
    d.insert(0, "market", market)
    d["kind"] = "etf"
    d["sector"] = d["tags"]
    d = d.reset_index(drop=True)
    f.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(f)
    return d


# ------------------------------------------------------------------ 시장 개관

def _stats(c: pd.Series) -> dict:
    c = c.dropna()
    hi = c.tail(250).max()
    return {"last": float(c.iloc[-1]), "asof": f"{c.index[-1]:%Y-%m-%d}",
            "d1": _ret(c, 1), "w1": _ret(c, 5), "m1": _ret(c, 21), "m3": _ret(c, 63),
            "vs_ma20": float(c.iloc[-1] / c.tail(20).mean() - 1),
            "vs_ma60": float(c.iloc[-1] / c.tail(60).mean() - 1),
            "vs_ma200": float(c.iloc[-1] / c.tail(200).mean() - 1) if len(c) >= 200 else math.nan,
            "from_52wh": float(c.iloc[-1] / hi - 1),
            "vol20": float(c.pct_change().tail(20).std() * math.sqrt(252))}


def overview(ref: pd.Timestamp) -> pd.DataFrame:
    syms = [s for s, _ in OVERVIEW]
    hist = yf_hist(syms, start=(ref - timedelta(days=420)).date(), end=(ref + timedelta(days=3)).date())
    rows = []
    for s, label in OVERVIEW:
        h = hist.get(s)
        if h is None or len(h) < 30:
            continue
        rows.append({"symbol": s, "label": label, **_stats(h["Close"])})
    return pd.DataFrame(rows)


def breadth(panel: pd.DataFrame) -> dict:
    p = panel[panel["kind"] == "stock"]
    return {"n": int(len(p)),
            "above_ma20": float((p["ma20_gap"] > 0).mean()), "above_ma60": float((p["ma60_gap"] > 0).mean()),
            "up_5d": float((p["ret5"] > 0).mean()), "median_ret5": float(p["ret5"].median()),
            "median_ret20": float(p["ret20"].median()),
            "near_52wh": float((p["pct_from_52wh"] < 0.03).mean())}


def analogs(symbol: str, ref: pd.Timestamp, years: int = 15) -> dict:
    """'지난주와 비슷한 주' 다음 주 지수 수익률 분포. 국면 판단의 기준선(base rate)이다.

    주간 종가(금→금) 기준이라 과제의 월 시가→금 종가와 정확히 같지 않다. 표본이 적은 조건은
    n을 같이 보여 주므로, 해석은 에이전트가 n을 보고 한다.
    """
    h = yf_hist([symbol], start=(ref - timedelta(days=365 * years + 400)).date(), end=(ref + timedelta(days=1)).date())
    if symbol not in h:
        return {}
    w = h[symbol]["Close"]
    w = w[w.index <= ref].resample("W-FRI").last().dropna()
    r1, r4 = w.pct_change(), w.pct_change(4)
    trend = w > w.rolling(40).mean()
    nxt = r1.shift(-1)
    df = pd.DataFrame({"r1": r1, "r4": r4, "up": trend, "nxt": nxt}).dropna(subset=["r1", "r4"])
    cur = df.iloc[-1]
    hist = df.iloc[:-1].dropna(subset=["nxt"])
    qs = hist["r1"].quantile([1 / 3, 2 / 3]).values
    bucket = lambda v: 0 if v <= qs[0] else (2 if v >= qs[1] else 1)  # noqa: E731

    def desc(sel: pd.Series) -> dict:
        x = hist.loc[sel, "nxt"]
        if len(x) == 0:
            return {"n": 0}
        return {"n": int(len(x)), "mean": float(x.mean()), "median": float(x.median()),
                "p_up": float((x > 0).mean()), "p10": float(x.quantile(0.1)), "p90": float(x.quantile(0.9))}

    same = (hist["up"] == cur["up"]) & (hist["r1"].map(bucket) == bucket(cur["r1"]))
    deeper = (hist["r1"] <= cur["r1"]) if cur["r1"] < 0 else (hist["r1"] >= cur["r1"])
    four = (hist["r4"] <= cur["r4"]) if cur["r4"] < 0 else (hist["r4"] >= cur["r4"])
    return {"symbol": symbol, "asof": f"{df.index[-1]:%Y-%m-%d}", "years": years,
            "cur_r1": float(cur["r1"]), "cur_r4": float(cur["r4"]), "cur_above_ma40w": bool(cur["up"]),
            "all": desc(pd.Series(True, index=hist.index)),
            "same_trend_and_r1_tercile": desc(same),
            "r1_at_least_this_extreme": desc(deeper),
            "r4_at_least_this_extreme": desc(four)}


# ------------------------------------------------------------------ 단일 종목

def history_one(market: str, ticker: str, ref: pd.Timestamp, days: int = 420) -> pd.DataFrame:
    """패널에 없는 종목도 조회한다 (에이전트가 웹 검색으로 찾은 종목)."""
    start, end = (ref - timedelta(days=days)).date(), (ref + timedelta(days=1)).date()
    if market == "KR":
        import FinanceDataReader as fdr
        try:
            h = fdr.DataReader(ticker, start, end)
        except Exception:
            h = pd.DataFrame()
    else:
        h = yf_hist([ticker], start=start, end=end).get(ticker, pd.DataFrame())
    if not h.empty:
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        h = h[h.index <= ref]
    return h


def us_events(ticker: str) -> dict:
    """US 종목의 실적 발표일·배당락 (yfinance). 실패하면 빈 dict — 웹 검색으로 확인할 몫이다."""
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar or {}
        out = {}
        for k in ("Earnings Date", "Ex-Dividend Date", "Dividend Date"):
            v = cal.get(k)
            if v:
                out[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else str(v)
        return out
    except Exception:
        return {}


def to_jsonable(v):
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(float(v)) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    return v


def dumps(v) -> str:
    return json.dumps(to_jsonable(v), ensure_ascii=False)
