"""사용자 최종 선택 정산 — 실제로 제출한 1종목의 주간 수익률을 final_log.csv에 남긴다.

에이전트는 시장마다 1종목(KR 1 + US 1)을 추천하지만, 과제는 매주 1종목만 낸다. 그 1종목은
사용자가 고른다: 한국 추천, 미국 추천, 또는 추천 과정을 거치지 않은 직접 지정 종목.
log.csv(에이전트 추천의 정산)와 따로 두어 "에이전트가 고른 것"과 "실제로 낸 것"을 비교할 수 있게 한다.

    python src/final.py --week 4
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settle import ROOT, close_final, fetch, fetch_benchmark, prices, resolve_dates, window  # noqa: E402

FLOG = ROOT / "final_log.csv"
FIELDS = ["week", "choice", "market", "ticker", "name", "buy_date", "sell_date", "buy_open", "sell_close",
          "ret_pct", "benchmark", "benchmark_ret_pct", "excess_pct", "note"]


def load_final(week: int) -> dict:
    f = ROOT / "outputs" / f"wk{week}" / "final.json"
    if not f.exists():
        raise SystemExit(f"wk{week}의 최종 선택(final.json)이 없다. 데스크 작업 패널에서 고른다.")
    return json.loads(f.read_text(encoding="utf-8"))


def settle_final(cfg: dict, week: int, cli_buy: str | None = None, cli_sell: str | None = None) -> dict | None:
    fin = load_final(week)
    m, t = fin["market"], str(fin["ticker"])
    out = ROOT / "outputs" / f"wk{week}" / m          # 시장별 날짜(휴장 반영)는 그 시장 폴더에 있다
    buy, sell = resolve_dates(cfg, week, m, out, cli_buy, cli_sell)
    if not close_final(m, sell):
        print(f"  매도일 {sell}의 종가가 아직 확정되지 않았다 — 건너뛴다", file=sys.stderr)
        return None
    start = (pd.Timestamp(buy) - timedelta(days=10)).date()
    end = (pd.Timestamp(sell) + timedelta(days=5)).date()
    h = fetch(t, m, start, end)
    if h.empty:
        print(f"  {t} 시세를 못 받았다", file=sys.stderr)
        return None
    try:
        bd, sd, bo, sc = prices(h, buy, sell)
    except ValueError as e:
        print(f"  {t} {e}", file=sys.stderr)
        return None
    ret = sc / bo - 1
    bench = fetch_benchmark(m, cfg, start, end)
    try:
        bb, bs = window(bench, buy, sell)
        bret = float(bench.loc[bs, "Close"]) / float(bench.loc[bb, "Open"]) - 1
    except Exception:
        bret = float("nan")
    row = {"week": week, "choice": fin["choice"], "market": m, "ticker": t, "name": fin.get("name", ""),
           "buy_date": f"{bd:%Y-%m-%d}", "sell_date": f"{sd:%Y-%m-%d}", "buy_open": round(bo, 4),
           "sell_close": round(sc, 4), "ret_pct": round(ret * 100, 2),
           "benchmark": {"KR": "KOSPI", "US": "S&P500"}[m],
           "benchmark_ret_pct": round(bret * 100, 2) if bret == bret else "",
           "excess_pct": round((ret - bret) * 100, 2) if bret == bret else "", "note": fin.get("note", "")}
    print(f"  최종 선택 [{fin['choice']}] {t} {row['name']}  {row['buy_date']} 시가 {bo:,.2f} → "
          f"{row['sell_date']} 종가 {sc:,.2f}   {row['ret_pct']:+.2f}%")
    return row


def append(row: dict) -> None:
    old = []
    if FLOG.exists():
        with FLOG.open(encoding="utf-8-sig", newline="") as f:
            old = [r for r in csv.DictReader(f) if str(r["week"]) != str(row["week"])]
    rows = sorted(old + [{k: row.get(k, "") for k in FIELDS}], key=lambda r: int(r["week"]))
    with FLOG.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"저장: {FLOG.name} ({len(rows)}행)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--buy-date")
    p.add_argument("--sell-date")
    a = p.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    week = a.week or cfg["run"]["week_no"]
    print(f"[wk{week} 최종 선택 정산]")
    row = settle_final(cfg, week, a.buy_date, a.sell_date)
    if row is None:
        return 1
    append(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
