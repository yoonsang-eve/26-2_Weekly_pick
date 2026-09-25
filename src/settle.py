"""금요일 정산 — 실제 매수가·매도가를 조회해 log.csv에 누적한다.

매수는 buy_date 시가, 매도는 sell_date 종가다. 휴장이면 그 주의 첫/마지막 영업일로
자동 이동한다 (config에 sell_date를 적어 두더라도 실제 데이터에 없으면 밀어 준다).

log.csv가 쌓이면 학기 말에 "LLM 에이전트 기반 종목 선정의 hit rate" 분석이 나온다.
빠짐없이 기록할 것 (CLAUDE.md 6절).

    python src/settle.py                 # config.run.week_no, config.run.markets
    python src/settle.py --week 3        # 특정 주차
    python src/settle.py --week 3 --market KR
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "log.csv"

FIELDS = [
    "week", "market", "ticker", "name", "buy_date", "sell_date", "holding_days",
    "buy_open", "sell_close", "ret_pct", "benchmark", "benchmark_ret_pct",
    "excess_pct", "predicted_low", "predicted_high", "hit_range",
    "catalyst", "primary_risk", "rank", "notes",
]

JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def load_pick(out: Path) -> dict:
    """pick.json이 있으면 그걸 쓰고, 없으면 trader 응답에서 JSON을 꺼낸다.

    manual 모드로 돌린 주차에는 pick.json이 없다 (wk3이 그렇다).
    """
    f = out / "pick.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))

    t = out / "responses" / "trader.md"
    if not t.exists():
        raise FileNotFoundError(f"{out.relative_to(ROOT)}에 pick.json도 trader 응답도 없다.")
    text = t.read_text(encoding="utf-8")
    fence = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        body = fence[-1]
    else:
        m = JSON_BLOCK.search(text)
        if not m:
            raise ValueError(f"{t.relative_to(ROOT)}에서 JSON을 찾지 못했다.")
        body = m.group(0)
    pick = json.loads(body)
    # 다음부터는 다시 파싱하지 않도록 남겨 둔다
    f.write_text(json.dumps(pick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  trader 응답에서 pick을 복원해 {f.relative_to(ROOT)}에 저장했다")
    return pick


def fetch(ticker: str, market: str, start, end) -> pd.DataFrame:
    if market == "KR":
        import FinanceDataReader as fdr
        return fdr.DataReader(ticker, start, end)
    import warnings

    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        h = yf.download(ticker, start=start, end=end + timedelta(days=1),
                        progress=False, auto_adjust=False)
    if isinstance(h.columns, pd.MultiIndex):     # 단일 티커도 MultiIndex로 온다
        h.columns = h.columns.get_level_values(0)
    return h


def fetch_benchmark(market: str, cfg: dict, start, end) -> pd.DataFrame:
    """벤치마크. KOSPI는 FDR 지수 데이터가 며칠 늦어 yfinance를 먼저 쓴다."""
    import warnings

    import yfinance as yf
    symbol = {"KR": "^KS11", "US": "^GSPC"}[market]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        h = yf.download(symbol, start=start, end=end + timedelta(days=1),
                        progress=False, auto_adjust=False)
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)
    return h.dropna(subset=["Close"])


def window(h: pd.DataFrame, buy: str, sell: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """실제 거래일로 스냅한다. 매수일이 휴장이면 다음 영업일, 매도일이면 직전 영업일."""
    idx = h.index.normalize()
    b = idx[idx >= pd.Timestamp(buy)]
    s = idx[idx <= pd.Timestamp(sell)]
    if len(b) == 0 or len(s) == 0:
        raise ValueError(f"{buy}~{sell} 구간에 거래일이 없다 (데이터 범위 {idx.min()}~{idx.max()})")
    return b[0], s[-1]


def resolve_dates(cfg: dict, week: int, market: str, out: Path,
                  cli_buy: str | None, cli_sell: str | None) -> tuple[str, str]:
    """그 주차의 매수·매도일. config는 '이번 주차'의 값이라 과거 주차엔 쓸 수 없다.

    우선순위: CLI 인자 > screen_meta.json(스크리닝 시점에 기록) > config(주차가 같을 때).
    """
    if cli_buy and cli_sell:
        return cli_buy, cli_sell

    meta_f = out / "screen_meta.json"
    if meta_f.exists():
        m = json.loads(meta_f.read_text(encoding="utf-8"))
        if m.get("buy_date") and m.get("sell_date"):
            return str(m["buy_date"]), str(m["sell_date"])

    if int(cfg["run"]["week_no"]) == int(week):
        mk = cfg["markets"][market]
        return (str(mk.get("buy_date") or cfg["run"]["buy_date"]),
                str(mk.get("sell_date") or cfg["run"]["sell_date"]))

    raise ValueError(
        f"wk{week} {market}의 매수·매도일을 알 수 없다. config는 wk{cfg['run']['week_no']} "
        f"기준이고 {(meta_f).relative_to(ROOT)}에도 날짜가 없다.\n"
        f"  --buy-date 2026-09-14 --sell-date 2026-09-18 처럼 직접 넘길 것.")


def settle_one(cfg: dict, week: int, market: str,
               cli_buy: str | None = None, cli_sell: str | None = None) -> dict | None:
    out = ROOT / "outputs" / f"wk{week}" / market
    if not out.exists():
        print(f"  [{market}] outputs/wk{week}/{market} 이 없다 — 건너뛴다")
        return None
    pick = load_pick(out)
    ticker, name = str(pick["ticker"]), pick.get("name", "")

    buy, sell = resolve_dates(cfg, week, market, out, cli_buy, cli_sell)
    if pd.Timestamp(sell) >= pd.Timestamp.now().normalize():
        # 매도일 종가가 아직 없으면 window()가 직전 거래일로 스냅해 틀린 값이 기록된다
        print(f"  [{market}] 매도일 {sell}의 종가가 아직 확정되지 않았다 — 건너뛴다",
              file=sys.stderr)
        return None

    start =(pd.Timestamp(buy) - timedelta(days=10)).date()
    end = (pd.Timestamp(sell) + timedelta(days=5)).date()

    h = fetch(ticker, market, start, end)
    if h.empty:
        print(f"  [{market}] {ticker} 시세를 못 받았다", file=sys.stderr)
        return None
    bd, sd = window(h, buy, sell)
    buy_open = float(h.loc[bd, "Open"])
    sell_close = float(h.loc[sd, "Close"])
    ret = sell_close / buy_open - 1

    bench = fetch_benchmark(market, cfg, start, end)
    try:
        bbd, bsd = window(bench, buy, sell)
        bret = float(bench.loc[bsd, "Close"]) / float(bench.loc[bbd, "Open"]) - 1
    except Exception as e:
        print(f"  [{market}] 벤치마크 조회 실패: {e}", file=sys.stderr)
        bret = float("nan")

    lo, hi = (pick.get("expected_return_range") or [None, None])[:2]
    hit = ""
    if lo is not None and hi is not None:
        hit = "Y" if float(lo) <= ret * 100 <= float(hi) else "N"

    row = {
        "week": week, "market": market, "ticker": ticker, "name": name,
        "buy_date": f"{bd:%Y-%m-%d}", "sell_date": f"{sd:%Y-%m-%d}",
        "holding_days": int(((h.index.normalize() >= bd) &
                             (h.index.normalize() <= sd)).sum()),
        "buy_open": round(buy_open, 4), "sell_close": round(sell_close, 4),
        "ret_pct": round(ret * 100, 2),
        "benchmark": {"KR": "KOSPI", "US": "S&P500"}[market],
        "benchmark_ret_pct": round(bret * 100, 2) if bret == bret else "",
        "excess_pct": round((ret - bret) * 100, 2) if bret == bret else "",
        "predicted_low": lo, "predicted_high": hi, "hit_range": hit,
        "catalyst": pick.get("catalyst", ""),
        "primary_risk": pick.get("primary_risk", ""),
        "rank": "", "notes": "",          # rank는 순위표 확인 후 수동 입력
    }
    print(f"  [{market}] {ticker} {name}  {row['buy_date']} 시가 {buy_open:,.2f} → "
          f"{row['sell_date']} 종가 {sell_close:,.2f}   {row['ret_pct']:+.2f}% "
          f"(벤치마크 {row['benchmark_ret_pct']}%, 초과 {row['excess_pct']}%p)")
    return row


def append_log(rows: list[dict]) -> None:
    """같은 (week, market)이 이미 있으면 덮어쓴다. rank·notes 수동 입력분은 보존한다."""
    old = []
    if LOG.exists():
        with LOG.open(encoding="utf-8-sig", newline="") as f:
            old = list(csv.DictReader(f))
    keyed = {(r["week"], r["market"]): r for r in old}
    for r in rows:
        k = (str(r["week"]), r["market"])
        if k in keyed:
            for col in ("rank", "notes"):
                if keyed[k].get(col):
                    r[col] = keyed[k][col]
        keyed[k] = {c: r.get(c, "") for c in FIELDS}
    merged = sorted(keyed.values(), key=lambda r: (int(r["week"]), r["market"]))
    with LOG.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(merged)
    print(f"\n저장: {LOG.relative_to(ROOT)} ({len(merged)}행)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--market", choices=["KR", "US"])
    p.add_argument("--buy-date", help="과거 주차를 정산할 때 (config는 이번 주차 기준이다)")
    p.add_argument("--sell-date")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    week = a.week or cfg["run"]["week_no"]
    markets = [a.market] if a.market else list(cfg["run"]["markets"])

    print(f"[wk{week} 정산]")
    rows = [r for m in markets
            if (r := settle_one(cfg, week, m, a.buy_date, a.sell_date)) is not None]
    if not rows:
        print("정산할 것이 없다.", file=sys.stderr)
        return 1
    append_log(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
