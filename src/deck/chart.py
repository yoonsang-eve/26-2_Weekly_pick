"""6개월 일봉 차트 (캔들 + MA20/MA60 + 거래량 + RSI14).

발표 자료 전용이다. 이 패키지를 통째로 지워도 파이프라인은 그대로 돈다.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent

# Windows에서 한글 종목명을 쓰면 폰트를 지정하지 않는 한 네모로 깨진다.
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]


def _korean_font() -> str:
    """설치된 한글 폰트 이름. mplfinance 스타일의 rc에 직접 넣어야 한다.

    make_mpf_style(rc=...)이 rcParams를 덮어쓰기 때문에, matplotlib.rcParams에만
    설정해 두면 조용히 DejaVu Sans로 되돌아가 한글이 네모로 깨진다.
    """
    import matplotlib
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((n for n in KOREAN_FONTS if n in installed), "DejaVu Sans")
    matplotlib.rcParams["font.family"] = chosen
    matplotlib.rcParams["axes.unicode_minus"] = False   # 음수 부호 깨짐 방지
    return chosen


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, pd.NA))


def fetch(ticker: str, market: str, end: str, months: int = 6) -> pd.DataFrame:
    """end까지의 일봉. MA60 워밍업을 위해 넉넉히 받고 마지막에 잘라 낸다."""
    end_ts = pd.Timestamp(end)
    start = (end_ts - timedelta(days=int(months * 30.5) + 140)).date()
    if market == "KR":
        import FinanceDataReader as fdr
        h = fdr.DataReader(ticker, start, end_ts.date())
    else:
        import warnings

        import yfinance as yf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            h = yf.download(ticker, start=start, end=(end_ts + timedelta(days=1)).date(),
                            progress=False, auto_adjust=False)
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
    return h.dropna(subset=["Close"])


def render(ticker: str, name: str, market: str, end: str, dst: Path,
           buy_date: str | None = None, sell_date: str | None = None,
           months: int = 6) -> Path | None:
    """차트를 PNG로 저장하고 경로를 돌려준다. 실패하면 None (발표자료는 계속 만든다)."""
    try:
        import mplfinance as mpf
    except ImportError:
        print("  mplfinance가 없다 — 차트를 건너뛴다. pip install mplfinance")
        return None

    try:
        h = fetch(ticker, market, end, months)
    except Exception as e:
        print(f"  차트 데이터 조회 실패 ({ticker}): {e}")
        return None
    if h.empty:
        print(f"  차트 데이터가 비었다 ({ticker})")
        return None

    font = _korean_font()
    h = h.copy()
    h["MA20"] = h["Close"].rolling(20).mean()
    h["MA60"] = h["Close"].rolling(60).mean()
    h["RSI"] = rsi(h["Close"])

    # 워밍업 구간을 버리고 실제 표시 구간만 남긴다
    cut = pd.Timestamp(end) - timedelta(days=int(months * 30.5))
    v = h[h.index >= cut]
    if len(v) < 20:
        v = h.tail(120)

    # secondary_y를 명시하지 않으면 mplfinance가 스케일을 보고 알아서 오른쪽 축에
    # 그린다. 그러면 RSI 패널의 왼쪽 축에 엉뚱한 눈금이 남는다.
    adds = [
        mpf.make_addplot(v["MA20"], color="#2563eb", width=1.0, secondary_y=False),
        mpf.make_addplot(v["MA60"], color="#f59e0b", width=1.0, secondary_y=False),
        mpf.make_addplot(v["RSI"], panel=2, color="#7c3aed", width=1.2,
                         ylabel="RSI(14)", ylim=(0, 100), secondary_y=False),
        mpf.make_addplot(pd.Series(70.0, index=v.index), panel=2, color="#9ca3af",
                         width=0.6, linestyle="--", secondary_y=False),
        mpf.make_addplot(pd.Series(30.0, index=v.index), panel=2, color="#9ca3af",
                         width=0.6, linestyle="--", secondary_y=False),
    ]
    style = mpf.make_mpf_style(base_mpf_style="yahoo",
                               rc={"font.size": 9, "font.family": font,
                                   "axes.unicode_minus": False})

    dst.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = mpf.plot(
        v, type="candle", style=style, addplot=adds, volume=True,
        panel_ratios=(6, 2, 2), figsize=(11, 6.5), returnfig=True,
        ylabel="", volume_panel=1, tight_layout=True,
        title=f"\n{name} ({ticker})  최근 {months}개월 일봉",
    )

    # 평가 구간을 음영으로 표시한다. 발표에서 "언제 사서 언제 팔았나"가 바로 보인다.
    if buy_date and sell_date:
        idx = v.index.normalize()
        try:
            b = int((idx >= pd.Timestamp(buy_date)).argmax())
            s = int(len(idx) - 1 - (idx <= pd.Timestamp(sell_date))[::-1].argmax())
            for ax in (axes[0], axes[2]):
                ax.axvspan(b - 0.5, s + 0.5, color="#fbbf24", alpha=0.18, zorder=0)
        except Exception:
            pass

    fig.savefig(dst, dpi=150, bbox_inches="tight")
    try:
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception:
        pass
    print(f"  차트 저장: {rel(dst)}")
    return dst


def rel(p: Path) -> str:
    """로그용 상대경로. 저장소 밖 경로가 들어와도 터지지 않게 한다."""
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)
