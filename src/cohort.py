"""후보군 정산 — 최종 1종목뿐 아니라 후보 20종목 전체의 같은 주 수익률을 계산한다.

log.csv는 "LLM이 고른 종목이 얼마 벌었나"만 답한다. 파이프라인의 각 단계가 가치를 더했는지
보려면 같은 주, 같은 매수·매도 규칙으로 나머지도 정산해야 한다.

    screener 기여  = 후보 20종목 평균  - 벤치마크
    analyst  기여  = 최종후보 3종목 평균 - 후보 20종목 평균
    trader   기여  = 최종 1종목       - 최종후보 3종목 평균

가격 데이터만 쓴다 (LLM 호출 없음). 결과는 outputs/wk{N}/{시장}/cohort.csv.

    python src/cohort.py --week 4 --market KR
    python src/cohort.py --week 3 --market KR --buy-date 2026-09-14 --sell-date 2026-09-18
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from settle import ROOT, fetch, load_pick, resolve_dates, window  # noqa: E402

FIELDS = ["week", "market", "ticker", "name", "role", "buy_date", "sell_date",
          "buy_open", "sell_close", "ret_pct"]


def _ticker(v) -> str:
    """KR 티커는 CSV를 거치며 앞자리 0이 사라진다 (010060 -> 10060)."""
    s = str(v).strip()
    return s.zfill(6) if s.isdigit() else s


def settle_ticker(ticker: str, market: str, buy: str, sell: str) -> dict:
    start = (pd.Timestamp(buy) - timedelta(days=10)).date()
    end = (pd.Timestamp(sell) + timedelta(days=5)).date()
    try:
        h = fetch(ticker, market, start, end)
        bd, sd = window(h, buy, sell)
        bo, sc = float(h.loc[bd, "Open"]), float(h.loc[sd, "Close"])
        return {"buy_date": f"{bd:%Y-%m-%d}", "sell_date": f"{sd:%Y-%m-%d}",
                "buy_open": round(bo, 4), "sell_close": round(sc, 4),
                "ret_pct": round((sc / bo - 1) * 100, 2)}
    except Exception as e:
        print(f"    {ticker} 정산 실패: {e}", file=sys.stderr)
        return {"buy_date": "", "sell_date": "", "buy_open": "", "sell_close": "", "ret_pct": ""}


def run(cfg: dict, week: int, market: str, cli_buy: str | None, cli_sell: str | None) -> Path | None:
    out = ROOT / "outputs" / f"wk{week}" / market
    cand_f = out / "candidates.csv"
    if not cand_f.exists():
        print(f"  [{market}] {cand_f.relative_to(ROOT)} 없음 — 건너뛴다")
        return None
    pick = load_pick(out)
    buy, sell = resolve_dates(cfg, week, market, out, cli_buy, cli_sell)

    today = pd.Timestamp.now().normalize()
    if pd.Timestamp(sell) >= today:
        # 매도일 종가가 아직 없으면 window()가 조용히 직전 거래일로 스냅한다. 그러면 틀린 값이 남는다.
        print(f"  [{market}] 매도일 {sell}의 종가가 아직 확정되지 않았다 — 건너뛴다", file=sys.stderr)
        return None

    cand = pd.read_csv(cand_f, dtype=str)
    pick_t = _ticker(pick["ticker"])
    finalists = {pick_t} | {_ticker(r["ticker"]) for r in pick.get("rejected", [])}

    rows = [{"ticker": _ticker(r["ticker"]), "name": r.get("name", "")} for _, r in cand.iterrows()]
    known = {r["ticker"] for r in rows}
    # 최종후보가 후보 CSV에 없는 경우(수기 추가 등)도 빠뜨리지 않는다
    for t in finalists - known:
        rows.append({"ticker": t, "name": pick.get("name", "") if t == pick_t else ""})

    print(f"  [{market}] wk{week} {buy} ~ {sell}  {len(rows)}종목 정산")
    with ThreadPoolExecutor(max_workers=int(cfg["data"].get("max_workers", 8))) as ex:
        res = list(ex.map(lambda r: settle_ticker(r["ticker"], market, buy, sell), rows))

    recs = []
    for r, s in zip(rows, res):
        role = "pick" if r["ticker"] == pick_t else "finalist" if r["ticker"] in finalists else "candidate"
        recs.append({"week": week, "market": market, **r, "role": role, **s})

    df = pd.DataFrame(recs, columns=FIELDS)
    dst = out / "cohort.csv"
    df.to_csv(dst, index=False, encoding="utf-8-sig")

    ok = pd.to_numeric(df["ret_pct"], errors="coerce")
    fin = ok[df["role"].isin(["pick", "finalist"])]
    print(f"    후보 평균 {ok.mean():+.2f}%  최종후보 평균 {fin.mean():+.2f}%  "
          f"pick {ok[df['role'] == 'pick'].mean():+.2f}%  (실패 {ok.isna().sum()})")
    print(f"    저장 {dst.relative_to(ROOT)}")
    return dst


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--week", type=int)
    p.add_argument("--market", choices=["KR", "US"])
    p.add_argument("--buy-date")
    p.add_argument("--sell-date")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    week = a.week or cfg["run"]["week_no"]
    markets = [a.market] if a.market else list(cfg["run"]["markets"])
    done = [m for m in markets if run(cfg, week, m, a.buy_date, a.sell_date)]
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
