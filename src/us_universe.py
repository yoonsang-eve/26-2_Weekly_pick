"""US 유니버스 (S&P 500 + Nasdaq 100) 관리.

CLAUDE.md 4절: 미국 유니버스는 정적 CSV로 관리하고 분기 1회 갱신한다.
매주 위키피디아를 긁으면 구성종목 변경이 조용히 스크리닝 결과를 바꿔서 재현이 깨진다.

갱신:
    python src/us_universe.py --refresh
확인:
    python src/us_universe.py
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "us_universe.csv"

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
UA = {"User-Agent": "Mozilla/5.0 (weekly_pick universe refresh)"}


def to_yf(symbol: str) -> str:
    """티커를 yfinance 표기로. 클래스 구분자가 점이 아니라 하이픈이다 (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def _read_tables(url: str) -> list[pd.DataFrame]:
    import requests

    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def _pick(tables: list[pd.DataFrame], sym_names: tuple[str, ...]) -> pd.DataFrame:
    """심볼 컬럼을 가진 첫 테이블을 고른다. 위키 문서의 표 순서는 자주 바뀐다."""
    for t in tables:
        cols = {str(c).strip(): c for c in t.columns}
        for s in sym_names:
            if s in cols:
                out = pd.DataFrame({"symbol": t[cols[s]].astype(str)})
                for name, key in (("name", ("Security", "Company")),
                                  ("sector", ("GICS Sector", "ICB Industry[1]",
                                              "ICB Industry"))):
                    for k in key:
                        if k in cols:
                            out[name] = t[cols[k]].astype(str)
                            break
                    else:
                        out[name] = ""
                return out
    raise RuntimeError(f"심볼 컬럼({sym_names})을 가진 표를 찾지 못했다")


def refresh(csv: Path) -> pd.DataFrame:
    sp = _pick(_read_tables(SP500_URL), ("Symbol",))
    sp["index"] = "SP500"
    print(f"  S&P 500      {len(sp):>4} 종목")

    ndx = _pick(_read_tables(NDX_URL), ("Ticker", "Symbol"))
    ndx["index"] = "NDX"
    print(f"  Nasdaq 100   {len(ndx):>4} 종목")

    df = pd.concat([sp, ndx], ignore_index=True)
    df["symbol"] = df["symbol"].map(to_yf)
    # 합집합. 양쪽에 있는 종목은 index를 "SP500+NDX"로 합쳐 둔다 (참고용)
    df = (df.groupby("symbol", as_index=False)
            .agg(name=("name", "first"), sector=("sector", "first"),
                 index=("index", lambda s: "+".join(sorted(set(s))))))
    df["refreshed_on"] = date.today().isoformat()
    df = df.sort_values("symbol").reset_index(drop=True)

    csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv, index=False, encoding="utf-8")
    print(f"  합집합       {len(df):>4} 종목  ->  {csv.relative_to(ROOT)}")
    return df


def load(csv: Path) -> pd.DataFrame:
    if not csv.exists():
        raise FileNotFoundError(
            f"{csv}가 없다. 먼저 `python src/us_universe.py --refresh`를 실행할 것.")
    df = pd.read_csv(csv)
    df["symbol"] = df["symbol"].map(to_yf)
    return df


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=str(DEFAULT_CSV))
    p.add_argument("--refresh", action="store_true", help="위키피디아에서 다시 받는다")
    a = p.parse_args()
    csv = Path(a.csv)

    if a.refresh:
        print("[US 유니버스 갱신]")
        refresh(csv)
        return 0

    df = load(csv)
    print(f"{csv.relative_to(ROOT)} — {len(df)}종목 "
          f"(갱신일 {df['refreshed_on'].iloc[0] if 'refreshed_on' in df else '?'})")
    print(df.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
