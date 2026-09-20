"""Step 1 — 기계적 스크리닝 (KR). LLM 호출 없음.

기준일은 buy_date 직전 금요일 종가. 그 시점까지의 데이터만 사용한다.
모든 임계값은 config.yaml에서 읽는다.
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

PREFERRED_NAME = re.compile(r"\d?우[BC]?$")
REIT_NAME = re.compile(r"리츠$")
SPAC_NAME = re.compile(r"스팩|기업인수목적")


def log(step: str, n: int, note: str = "") -> None:
    print(f"  {step:<34} {n:>5} 종목  {note}")


def ref_friday(buy_date: str) -> pd.Timestamp:
    """buy_date 직전 금요일. buy_date 당일은 포함하지 않는다."""
    d = pd.Timestamp(buy_date)
    return d - timedelta(days=(d.weekday() - 4) % 7 or 7)


def load_listing(ref: pd.Timestamp, use_cache: bool) -> pd.DataFrame:
    f = CACHE / f"kr_listing_{ref:%Y%m%d}.parquet"
    if use_cache and f.exists():
        return pd.read_parquet(f)
    snap = fdr.StockListing("KRX")[
        ["Code", "Name", "Market", "Dept", "Close", "Amount", "Marcap"]
    ]
    desc = fdr.StockListing("KRX-DESC")[["Code", "Industry"]]
    df = snap.merge(desc, on="Code", how="left")
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
    d = CACHE / "kr_hist"
    d.mkdir(parents=True, exist_ok=True)

    def one(code: str):
        f = d / f"{code}_{ref:%Y%m%d}.parquet"
        if use_cache and f.exists():
            return code, pd.read_parquet(f)
        try:
            h = fdr.DataReader(code, start, ref.date())
        except Exception:
            return code, None
        if h.empty:
            return code, None
        h.to_parquet(f)
        return code, h

    with ThreadPoolExecutor(max_workers=int(cfg["data"]["max_workers"])) as ex:
        out = dict(ex.map(one, codes))
    return {k: v for k, v in out.items() if v is not None}


def indicators(h: pd.DataFrame, ref: pd.Timestamp, lookback: int,
               mom_lb: int, mom_skip: int, rev_days: int) -> dict | None:
    """기준일 종가 기준 지표. 기준일 데이터가 없으면(거래정지 등) None."""
    need = mom_lb + mom_skip + 5
    if h.index[-1].normalize() != ref.normalize() or len(h) < need:
        return None
    c, hi, lo = h["Close"], h["High"], h["Low"]
    close = float(c.iloc[-1])

    prev = c.shift(1)
    tr = pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    atr20 = tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1]  # Wilder
    wh = float(hi.tail(lookback).max())  # 수집 구간은 여유분을 포함하므로 52주로 자른다

    return {
        "close": close,
        "adv20": float((c * h["Volume"]).tail(20).mean()),  # 거래대금 근사 (종가×거래량)
        # 모멘텀은 최근 mom_skip 영업일을 건너뛴 구간에서 잰다. 건너뛴 구간은 반전이
        # 지배하므로 모멘텀 신호에 섞으면 부호가 상쇄된다 (J&T 1993의 skip-month).
        "ret_mom": float(c.iloc[-(mom_skip + 1)]) / float(c.iloc[-(mom_lb + mom_skip + 1)]) - 1,
        "ret20": close / float(c.iloc[-21]) - 1,
        "ret5": close / float(c.iloc[-(rev_days + 1)]) - 1,
        "pct_from_52wh": (wh - close) / wh,
        "atr20_pct": float(atr20) / close,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--n", type=int)
    p.add_argument("--no-cache", action="store_true")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    use_cache = not a.no_cache
    week = a.week or cfg["run"]["week_no"]
    n_out = a.n or cfg["universe"]["quota"]["KR"]
    f = cfg["filters"]
    ref = ref_friday(cfg["run"]["buy_date"])

    print(f"week {week} | 기준일 {ref:%Y-%m-%d}(금) 종가 | "
          f"{cfg['run']['buy_date']} 시가 매수 → {cfg['run']['sell_date']} 종가 매도")

    df = build_universe(load_listing(ref, use_cache), cfg)

    pre = int(cfg["data"]["prefetch_top_n_kr"])
    codes = df.nlargest(pre, "Amount")["Code"].tolist()
    print(f"\n[*] 히스토리 수집 (당일 거래대금 상위 {pre}종목)")
    hist = fetch_history(codes, ref, cfg, use_cache)
    log("조회 성공", len(hist))

    rows = []
    for code, h in hist.items():
        ind = indicators(h, ref, int(cfg["data"]["lookback_days"]),
                         int(f["mom_lookback_days"]), int(f["mom_skip_days"]),
                         int(f["reversal_days"]))
        if ind:
            rows.append({"Code": code, **ind})
    d = df.merge(pd.DataFrame(rows), on="Code")
    log("기준일 종가 보유 (거래정지 제외)", len(d))

    print("\n[필터 적용]")
    d = d.nlargest(int(f["adv20_rank_max_kr"]), "adv20")
    log(f"1. adv20 상위 {f['adv20_rank_max_kr']}위", len(d))

    d = d[d["Marcap"] >= f["mktcap_min_krw"]]
    log(f"2. 시총 >= {f['mktcap_min_krw'] / 1e8:,.0f}억", len(d))

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

    if d.empty:
        print("\n후보가 0개다. 필터 파라미터를 확인할 것.", file=sys.stderr)
        return 1

    out = pd.DataFrame({
        "market": d["Market"], "ticker": d["Code"], "name": d["Name"],
        "sector": d["Industry"].fillna(""), "close": d["close"],
        "mktcap": d["Marcap"], "adv20": d["adv20"].round(0),
        "ret_mom": d["ret_mom"].round(4), "ret20": d["ret20"].round(4),
        "ret5": d["ret5"].round(4),
        "pct_from_52wh": d["pct_from_52wh"].round(4),
        "atr20_pct": d["atr20_pct"].round(4), "score": d[f["sort_by"]].round(4),
    })
    dst = ROOT / "outputs" / f"wk{week}" / "candidates.csv"
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"\n저장: {dst.relative_to(ROOT)}")
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
