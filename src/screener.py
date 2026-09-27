"""Step 1 — 기계적 스크리닝 (KR / US). LLM 호출 없음.

기준일은 buy_date 직전 금요일 종가. 그 시점까지의 데이터만 사용한다.
US는 시차 때문에 한국 금요일에 미국 금요일장이 아직 안 끝났을 수 있어, 실제로 확정된
마지막 거래일을 데이터에서 직접 찾아 기준일로 쓴다 (resolve_us_ref 참조).

모든 임계값은 config.yaml에서 읽는다.

    python src/screener.py                # config.run.markets 전부 (KR, US)
    python src/screener.py --market US    # 한 시장만
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402
import us_universe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

PREFERRED_NAME = re.compile(r"\d?우[BC]?$")
REIT_NAME = re.compile(r"리츠$")
SPAC_NAME = re.compile(r"스팩|기업인수목적")


def force_http_timeout(seconds: float) -> None:
    """requests에 기본 타임아웃을 강제한다.

    FinanceDataReader는 requests를 timeout 인자 없이 호출한다. requests는 timeout이
    없으면 urllib3에 명시적으로 '무한 대기'를 넘기기 때문에 socket.setdefaulttimeout도
    듣지 않는다. 실제로 전종목 히스토리를 수백 건 연속 호출하면 소스가 응답을 끊고,
    그 스레드가 영원히 멈춰 스크리너가 끝나지 않는다. 라이브러리가 타임아웃을 노출하지
    않으므로 Session.request의 기본값을 여기서 채운다.
    """
    import requests

    if getattr(requests.Session, "_weekly_pick_timeout", None):
        return
    original = requests.Session.request

    def with_timeout(self, *args, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return original(self, *args, **kwargs)

    requests.Session.request = with_timeout
    requests.Session._weekly_pick_timeout = True
    socket.setdefaulttimeout(seconds)


def log(step: str, n: int, note: str = "") -> None:
    print(f"  {step:<34} {n:>5} 종목  {note}")
    events.emit("screener", "step", step, n=n)


def buy_date_of(cfg: dict, market: str) -> str:
    """시장별 매수일. 휴장 등으로 다를 수 있어 markets 블록이 run을 덮어쓴다."""
    return str(cfg["markets"][market].get("buy_date") or cfg["run"]["buy_date"])


def ref_friday(buy_date: str) -> pd.Timestamp:
    """buy_date 직전 금요일. buy_date 당일은 포함하지 않는다."""
    d = pd.Timestamp(buy_date)
    return d - timedelta(days=(d.weekday() - 4) % 7 or 7)


# ---------------------------------------------------------------- 공통 지표

def indicators(h: pd.DataFrame, ref: pd.Timestamp, lookback: int,
               mom_lb: int, mom_skip: int, rev_days: int,
               ret_col: str = "Close") -> dict | None:
    """기준일 종가 기준 지표. 기준일 데이터가 없으면(거래정지 등) None.

    ret_col은 수익률 계산에 쓸 컬럼이다. US는 분할·배당 조정된 'Adj Close'를 쓰고,
    표시가격(close)과 거래대금(adv20)은 미조정 'Close'로 계산한다.
    """
    need = mom_lb + mom_skip + 5
    if h.index[-1].normalize() != ref.normalize() or len(h) < need:
        return None
    c, hi, lo = h["Close"], h["High"], h["Low"]
    r = h[ret_col]
    close = float(c.iloc[-1])
    if not (close > 0):
        return None

    prev = c.shift(1)
    tr = pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    atr20 = tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1]  # Wilder
    wh = float(hi.tail(lookback).max())  # 수집 구간은 여유분을 포함하므로 52주로 자른다

    return {
        "close": close,
        "adv20": float((c * h["Volume"]).tail(20).mean()),  # 거래대금 근사 (종가×거래량)
        # 모멘텀은 최근 mom_skip 영업일을 건너뛴 구간에서 잰다. 건너뛴 구간은 반전이
        # 지배하므로 모멘텀 신호에 섞으면 부호가 상쇄된다 (J&T 1993의 skip-month).
        "ret_mom": float(r.iloc[-(mom_skip + 1)]) / float(r.iloc[-(mom_lb + mom_skip + 1)]) - 1,
        "ret20": float(r.iloc[-1]) / float(r.iloc[-21]) - 1,
        "ret5": float(r.iloc[-1]) / float(r.iloc[-(rev_days + 1)]) - 1,
        "pct_from_52wh": (wh - close) / wh,
        "atr20_pct": float(atr20) / close,
    }


def apply_common_filters(d: pd.DataFrame, f: dict, market: str,
                         n_out: int) -> pd.DataFrame:
    """필터 1~4. 시장별로 다른 것은 임계값뿐이고 순서와 성격은 동일하다."""
    print("\n[필터 적용]")
    if market == "KR":
        d = d.nlargest(int(f["adv20_rank_max_kr"]), "adv20")
        log(f"1. adv20 상위 {f['adv20_rank_max_kr']}위", len(d))
        d = d[d["mktcap"] >= f["mktcap_min_krw"]]
        log(f"2. 시총 >= {f['mktcap_min_krw'] / 1e8:,.0f}억", len(d))
    else:
        d = d[d["adv20"] >= f["adv20_min_usd_us"]]
        log(f"1. adv20 >= ${f['adv20_min_usd_us'] / 1e6:,.0f}M", len(d))
        # 시총을 못 받아온 종목은 여기서 떨어뜨리지 않는다. S&P500·NDX 구성종목이라
        # 하한 $2B은 사실상 비구속적이고, 조회 실패를 탈락으로 처리하면 그 자체가
        # 의도치 않은 필터가 된다. 대신 미확인 건수를 로그에 남긴다.
        unknown = int(d["mktcap"].isna().sum())
        d = d[d["mktcap"].isna() | (d["mktcap"] >= f["mktcap_min_usd"])]
        log(f"2. 시총 >= ${f['mktcap_min_usd'] / 1e9:,.0f}B", len(d),
            f"(시총 미확인 {unknown}종목은 통과 처리)" if unknown else "")

    d = d[(d["ret_mom"] >= d["ret_mom"].quantile(f["mom_pct_rank_min"]))
          & (d["ret5"] <= f["reversal_max"])]
    log(f"3. {f['mom_lookback_days']}일 모멘텀(최근 {f['mom_skip_days']}일 제외) 상위 "
        f"{(1 - f['mom_pct_rank_min']) * 100:.0f}% & {f['reversal_days']}일 수익률 <= "
        f"{f['reversal_max']:+.0%}", len(d))

    # 4. 변동성 정렬 — 컷이 아니다. low-vol anomaly / BAB(Frazzini & Pedersen 2014)상
    # 고변동성은 위험조정수익률이 오히려 낮다. 기대수익률을 깎는 대신 상위 3위 진입
    # 확률을 올리는 tournament 베팅이며, 주간 독립 시행 구조에서만 정당화된다.
    d = d.sort_values(f["sort_by"], ascending=not f["sort_desc"]).head(n_out)
    log(f"4. {f['sort_by']} 정렬 후 상위", len(d))
    return d


def emit(d: pd.DataFrame, market: str, week: int, ref: pd.Timestamp,
         cfg: dict) -> Path:
    out = pd.DataFrame({
        "market": market, "exchange": d["exchange"], "ticker": d["ticker"],
        "name": d["name"], "sector": d["sector"].fillna(""), "close": d["close"],
        "mktcap": d["mktcap"], "adv20": d["adv20"].round(0),
        "ret_mom": d["ret_mom"].round(4), "ret20": d["ret20"].round(4),
        "ret5": d["ret5"].round(4), "pct_from_52wh": d["pct_from_52wh"].round(4),
        "atr20_pct": d["atr20_pct"].round(4),
        "score": d[cfg["filters"]["sort_by"]].round(4),
    })
    dst = ROOT / "outputs" / f"wk{week}" / market / "candidates.csv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False, encoding="utf-8-sig")

    # 기준일은 US에서 시차 때문에 달력 금요일과 달라질 수 있다. agents.py가 프롬프트에
    # 그대로 넣어야 하므로 파일로 남긴다.
    # 매수·매도일도 같이 남긴다. config는 '이번 주차' 값이라 몇 주 뒤에 과거 주차를
    # 정산·발표자료화할 때 그 주의 날짜를 복원할 방법이 없어진다.
    mk = cfg["markets"][market]
    (dst.parent / "screen_meta.json").write_text(json.dumps({
        "market": market, "week": week, "ref_date": f"{ref:%Y-%m-%d}",
        "buy_date": str(mk.get("buy_date") or cfg["run"]["buy_date"]),
        "sell_date": str(mk.get("sell_date") or cfg["run"]["sell_date"]),
        "holding_days": mk.get("holding_days"),
        "n_candidates": len(out), "config_version": cfg["meta"]["config_version"],
        "screened_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {dst.relative_to(ROOT)}")
    print(out.to_string(index=False))
    events.emit("screener", "done", f"후보 {len(out)}종목 확정", n=len(out),
                tickers=[f"{t} {n}" for t, n in zip(out["ticker"], out["name"])])
    return dst


# ------------------------------------------------------------------- KR

def load_listing(ref: pd.Timestamp, use_cache: bool) -> pd.DataFrame:
    f = CACHE / f"kr_listing_{ref:%Y%m%d}.parquet"
    if use_cache and f.exists():
        return pd.read_parquet(f)
    snap = fdr.StockListing("KRX")[
        ["Code", "Name", "Market", "Dept", "Close", "Amount", "Marcap"]
    ]
    try:
        desc = fdr.StockListing("KRX-DESC")[["Code", "Industry"]]
        df = snap.merge(desc, on="Code", how="left")
    except Exception as e:
        # FDR 0.9.202 기준 KRX-DESC 엔드포인트가 404다. 업종은 후보 표의 참고 정보일
        # 뿐이고 필터에는 쓰이지 않으므로, 못 받으면 비워 두고 계속 간다.
        print(f"  경고: KRX-DESC 업종 정보를 못 받았다 ({e}). 업종 칸은 비운다.",
              file=sys.stderr)
        df = snap.assign(Industry="")
    f.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(f)
    return df


def build_universe(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    kr = cfg["universe"]["kr"]
    print("\n[0] 유니버스 구성")
    log("KRX 전체 상장", len(df))

    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    log("KONEX 제외", len(df))

    dept = df["Dept"].fillna("")
    if kr["exclude_preferred"]:
        # 보통주는 종목코드 끝자리가 0. 종목명 접미사(우/우B/2우B)와 함께 판정한다.
        df = df[~df["Name"].str.contains(PREFERRED_NAME) & df["Code"].str.endswith("0")]
        dept = df["Dept"].fillna("")
        log("우선주 제외", len(df))
    if kr["exclude_spac"]:
        df = df[~df["Name"].str.contains(SPAC_NAME) & ~dept.str.contains("SPAC")]
        dept = df["Dept"].fillna("")
        log("스팩 제외", len(df))
    if kr["exclude_reit"]:
        df = df[~df["Name"].str.contains(REIT_NAME)]
        dept = df["Dept"].fillna("")
        log("리츠 제외", len(df))
    if kr["exclude_admin_issue"]:
        # KOSPI는 Dept가 비어 있어 여기서 걸러지지 않는다. 거래정지분은 아래 히스토리
        # 단계에서 기준일 종가 부재로 탈락한다.
        df = df[~dept.str.contains("관리종목|투자주의환기")]
        log("관리종목·투자주의환기 제외", len(df), "(KOSDAQ 한정)")
    return df.reset_index(drop=True)


def fetch_history(codes: list[str], ref: pd.Timestamp, cfg: dict, use_cache: bool) -> dict:
    start = (ref - timedelta(days=int(cfg["data"]["lookback_days"] * 1.7))).date()
    retries = int(cfg["data"].get("kr_retries", 2))
    d = CACHE / "kr_hist"
    d.mkdir(parents=True, exist_ok=True)
    done = [0]

    def one(code: str):
        f = d / f"{code}_{ref:%Y%m%d}.parquet"
        if use_cache and f.exists():
            h = pd.read_parquet(f)
        else:
            # 연속 호출을 계속하면 소스가 조용히 응답을 끊는다. 소켓 타임아웃(main에서
            # 설정)에 걸리면 예외로 돌아오므로 몇 번 다시 시도한다. 타임아웃이 없으면
            # 스레드가 영원히 멈춰서 스크리너 전체가 끝나지 않는다.
            h = None
            for attempt in range(retries + 1):
                try:
                    h = fdr.DataReader(code, start, ref.date())
                    break
                except Exception:
                    if attempt < retries:
                        time.sleep(1.5 * (attempt + 1))
            if h is None or h.empty:
                return code, None
            h.to_parquet(f)
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"    {done[0]:>4} / {len(codes)}")
        return code, h

    with ThreadPoolExecutor(max_workers=int(cfg["data"]["max_workers"])) as ex:
        out = dict(ex.map(one, codes))
    return {k: v for k, v in out.items() if v is not None}


def screen_kr(cfg: dict, week: int, use_cache: bool) -> int:
    f = cfg["filters"]
    ref = ref_friday(buy_date_of(cfg, "KR"))
    n_out = int(cfg["universe"]["quota"]["KR"])
    print(f"\n{'=' * 78}\n[KR] 기준일 {ref:%Y-%m-%d}(금) 종가\n{'=' * 78}")
    events.bind(week, "KR")
    events.emit("screener", "start", f"기준일 {ref:%Y-%m-%d} 종가")

    df = build_universe(load_listing(ref, use_cache), cfg)

    pre = int(cfg["data"]["prefetch_top_n_kr"])
    codes = df.nlargest(pre, "Amount")["Code"].tolist()
    print(f"\n[*] 히스토리 수집 (당일 거래대금 상위 {pre}종목)")
    hist = fetch_history(codes, ref, cfg, use_cache)
    log("조회 성공", len(hist))

    # 금요일이 휴장이면(wk5: 추석 9/24~25) 달력상 금요일 종가는 없다. 미국과 같은 방식으로
    # 실제 마지막 거래일을 기준일로 삼는다. 안 그러면 전 종목이 '기준일 종가 없음'으로 탈락한다.
    actual = resolve_last_session(hist, ref)
    if actual != ref.normalize():
        print(f"  기준일 조정: {ref:%Y-%m-%d}은 휴장 -> 실제 마지막 거래일 {actual:%Y-%m-%d} 종가")
        events.emit("screener", "step", f"기준일 조정 {actual:%Y-%m-%d} (금요일 휴장)")
        ref = actual

    rows = []
    for code, h in hist.items():
        ind = indicators(h, ref, int(cfg["data"]["lookback_days"]),
                         int(f["mom_lookback_days"]), int(f["mom_skip_days"]),
                         int(f["reversal_days"]))
        if ind:
            rows.append({"Code": code, **ind})
    if not rows:
        print(f"\nKR: 기준일 {ref:%Y-%m-%d} 종가를 가진 종목이 하나도 없다 (휴장일 또는 데이터 지연).",
              file=sys.stderr)
        events.emit("screener", "error", f"기준일 {ref:%Y-%m-%d} 종가 없음")
        return 1
    d = df.merge(pd.DataFrame(rows), on="Code")
    log("기준일 종가 보유 (거래정지 제외)", len(d))

    d = d.rename(columns={"Code": "ticker", "Name": "name", "Market": "exchange",
                          "Industry": "sector", "Marcap": "mktcap"})
    d = apply_common_filters(d, f, "KR", n_out)
    if d.empty:
        print("\nKR 후보가 0개다. 필터 파라미터를 확인할 것.", file=sys.stderr)
        events.emit("screener", "error", "후보 0개")
        return 1
    emit(d, "KR", week, ref, cfg)
    return 0


# ------------------------------------------------------------------- US

def fetch_us_history(symbols: list[str], ref: pd.Timestamp, cfg: dict,
                     use_cache: bool) -> dict:
    """yfinance 벌크 다운로드. 심볼 -> OHLCV+Adj Close DataFrame."""
    import yfinance as yf

    start = (ref - timedelta(days=int(cfg["data"]["lookback_days"] * 1.7))).date()
    end = (ref + timedelta(days=1)).date()          # yfinance의 end는 배타적이다
    cache_f = CACHE / f"us_hist_{ref:%Y%m%d}.parquet"
    if use_cache and cache_f.exists():
        raw = pd.read_parquet(cache_f)
    else:
        size = int(cfg["data"]["us_batch_size"])
        frames = []
        for i in range(0, len(symbols), size):
            batch = symbols[i:i + size]
            print(f"    {i + 1:>4}~{i + len(batch):<4} / {len(symbols)}")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                part = yf.download(batch, start=start, end=end, auto_adjust=False,
                                   group_by="ticker", threads=True, progress=False,
                                   actions=False)
            if part is None or part.empty:
                print(f"    경고: {batch[0]}~{batch[-1]} 배치가 비어서 돌아왔다",
                      file=sys.stderr)
                continue
            frames.append(part)
        if not frames:
            return {}
        raw = pd.concat(frames, axis=1)
        cache_f.parent.mkdir(parents=True, exist_ok=True)
        raw.to_parquet(cache_f)

    out = {}
    for sym in symbols:
        if sym not in raw.columns.get_level_values(0):
            continue
        h = raw[sym].dropna(how="all")
        if h.empty or "Adj Close" not in h.columns:
            continue
        out[sym] = h.dropna(subset=["Close", "Adj Close"])
    return out


def resolve_us_ref(hist: dict, ref: pd.Timestamp) -> pd.Timestamp:
    """실제로 확정된 마지막 미국 거래일.

    한국시간 금요일에 돌리면 미국 현지 금요일장은 아직 열리지도 않았다. 기준일을
    달력으로 고정하면 전 종목이 '기준일 종가 없음'으로 탈락한다. 그래서 다수결로
    마지막 거래일을 찾아 기준일로 삼고, 그 날짜를 로그에 찍는다.
    """
    if not hist:
        return ref
    last = Counter(h.index[-1].normalize() for h in hist.values())
    resolved = max(d for d, _ in last.most_common(3))
    return min(resolved, ref.normalize())


resolve_last_session = resolve_us_ref   # KR도 같은 규칙 (금요일 휴장 대비)


def fetch_us_marketcap(symbols: list[str], ref: pd.Timestamp, cfg: dict,
                       use_cache: bool) -> pd.Series:
    """yfinance fast_info 기반 시총. 실패분은 NaN으로 남기고 필터에서 통과시킨다."""
    import yfinance as yf

    f = CACHE / f"us_mktcap_{ref:%Y%m%d}.parquet"
    if use_cache and f.exists():
        return pd.read_parquet(f)["mktcap"]

    def one(sym: str):
        # FastInfo는 __getitem__만 snake_case를 camelCase로 바꿔 준다. .get()을 쓰면
        # 키를 못 찾고 조용히 None이 나온다.
        try:
            v = yf.Ticker(sym).fast_info["market_cap"]
            return sym, float(v) if v else float("nan")
        except Exception:
            return sym, float("nan")

    with ThreadPoolExecutor(max_workers=int(cfg["data"]["max_workers"])) as ex:
        pairs = list(ex.map(one, symbols))
    s = pd.Series(dict(pairs), name="mktcap")
    f.parent.mkdir(parents=True, exist_ok=True)
    s.to_frame().to_parquet(f)
    return s


def screen_us(cfg: dict, week: int, use_cache: bool) -> int:
    f = cfg["filters"]
    cal_ref = ref_friday(buy_date_of(cfg, "US"))
    n_out = int(cfg["universe"]["quota"]["US"])
    print(f"\n{'=' * 78}\n[US] 기준일 후보 {cal_ref:%Y-%m-%d}(금) — 실제 기준일은 "
          f"데이터에서 확정한다\n{'=' * 78}")
    events.bind(week, "US")
    events.emit("screener", "start", f"기준일 후보 {cal_ref:%Y-%m-%d}")

    uni = us_universe.load(ROOT / cfg["universe"]["us"]["universe_csv"])
    print("\n[0] 유니버스 구성")
    log("S&P500 + NDX 합집합", len(uni))

    print("\n[*] 히스토리 수집 (yfinance)")
    hist = fetch_us_history(uni["symbol"].tolist(), cal_ref, cfg, use_cache)
    log("조회 성공", len(hist))
    if not hist:
        print("\nyfinance 응답이 비었다. --no-cache로 재시도할 것.", file=sys.stderr)
        return 1

    ref = resolve_us_ref(hist, cal_ref)
    print(f"  확정 기준일  {ref:%Y-%m-%d}({'월화수목금토일'[ref.weekday()]}) 종가"
          + ("" if ref == cal_ref.normalize() else "  ← 시차로 금요일장 미확정"))

    caps = fetch_us_marketcap(list(hist), cal_ref, cfg, use_cache)
    log("시총 조회 성공", int(caps.notna().sum()), f"/ {len(caps)}")

    rows = []
    for sym, h in hist.items():
        ind = indicators(h, ref, int(cfg["data"]["lookback_days"]),
                         int(f["mom_lookback_days"]), int(f["mom_skip_days"]),
                         int(f["reversal_days"]), ret_col="Adj Close")
        if ind:
            rows.append({"symbol": sym, **ind})
    d = uni.merge(pd.DataFrame(rows), on="symbol")
    log("기준일 종가 보유 (거래정지·신규상장 제외)", len(d))

    d = d.rename(columns={"symbol": "ticker"})
    d["exchange"] = d["index"]
    d["mktcap"] = d["ticker"].map(caps)

    if cfg["universe"]["us"].get("drop_secondary_class"):
        # GOOG/GOOGL, FOX/FOXA 처럼 같은 회사의 복수 클래스는 거래대금이 큰 쪽만 남긴다.
        # 유니버스 CSV의 종목명이 "Alphabet Inc. (Class C)" 형태라 괄호를 떼면 같아진다.
        before = len(d)
        d = (d.assign(_co=d["name"].str.replace(r"\s*\(Class [A-Z]\)\s*$", "",
                                                regex=True).str.strip())
               .sort_values("adv20", ascending=False)
               .drop_duplicates(subset="_co", keep="first")
               .drop(columns="_co"))
        log("복수 클래스 주식 정리", len(d), f"({before - len(d)}종목 제외)")

    d = apply_common_filters(d, f, "US", n_out)
    if d.empty:
        print("\nUS 후보가 0개다. 필터 파라미터를 확인할 것.", file=sys.stderr)
        events.emit("screener", "error", "후보 0개")
        return 1
    emit(d, "US", week, ref, cfg)
    return 0


# ------------------------------------------------------------------ main

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--market", choices=["KR", "US"], help="생략하면 config.run.markets 전부")
    p.add_argument("--week", type=int)
    p.add_argument("--no-cache", action="store_true")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    force_http_timeout(float(cfg["data"].get("request_timeout_sec", 20)))
    use_cache = not a.no_cache
    week = a.week or cfg["run"]["week_no"]
    markets = [a.market] if a.market else list(cfg["run"]["markets"])

    print(f"week {week} | 대상 시장 {', '.join(markets)} | "
          f"{cfg['run']['buy_date']} 시가 매수 → {cfg['run']['sell_date']} 종가 매도")

    rc = 0
    for m in markets:
        rc |= (screen_kr if m == "KR" else screen_us)(cfg, week, use_cache)
    return rc


if __name__ == "__main__":
    sys.exit(main())
