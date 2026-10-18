"""에이전트 도구 — 스키마(LLM에 보이는 것)와 실행(파이썬).

도구는 '데이터를 보여 주는 것'과 '판단을 확정하는 것'으로 나뉜다.

  조사   market_overview · sector_rotation · screen_stocks · screen_etfs · instrument_detail · track_record
  기록   write_note
  검증   request_critique   — 별도 컨텍스트의 비판자 LLM (웹 검색). 매수 논거만 받고 대화 기록은 못 본다
  확정   submit_recommendation — 검증을 통과해야 루프가 끝난다. 실패하면 이유를 돌려주고 루프가 계속된다

웹 검색은 서버 도구(web_search)라 여기 없다. loop.py가 같이 건다.
고정 파이프라인과 달리 필터를 코드가 정하지 않는다 — 어떤 지표로 거를지는 에이전트가 국면을 보고 정한다.
다만 유동성·시총 하한(위생 필터)은 screen_stocks 기본값으로 남긴다 (CLAUDE.md 8절).
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import data, memory

ROOT = Path(__file__).resolve().parent.parent.parent
NUM_COLS = ["ret1", "ret5", "ret20", "ret60", "ret_mom", "atr20_pct", "vol20", "beta60", "rsi14",
            "ma20_gap", "ma60_gap", "pct_from_52wh", "vol_ratio"]
PCT_COLS = {"ret1", "ret5", "ret20", "ret60", "ret_mom", "atr20_pct", "vol20", "ma20_gap", "ma60_gap", "pct_from_52wh"}
REGIMES = ["risk_on", "neutral", "risk_off", "crash"]
STANCES = ["aggressive", "balanced", "defensive", "hedge"]
MAX_ROWS = 30

FILTER_SCHEMA = {
    "type": "object",
    "description": "지표별 [최소, 최대]. 한쪽만 걸려면 다른 쪽을 null. 수익률·변동성은 소수(0.05 = 5%). "
                   f"쓸 수 있는 키: {', '.join(NUM_COLS)}",
    "additionalProperties": {"type": "array", "items": {"type": ["number", "null"]}, "minItems": 2, "maxItems": 2},
}

SCHEMAS = [
    {"name": "market_overview",
     "description": "지수·금리·환율·VIX·원자재 시세표, 시장별 breadth(이평선 위 종목 비율 등), 지난주와 비슷했던 과거 주간의 "
                    "다음 주 지수 수익률 분포(15년 base rate), 평가 주간 일정(휴장·FOMC·사용자 등록 일정). "
                    "국면 판단의 출발점이다. 인자 없음.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "sector_rotation",
     "description": "한 시장의 섹터·테마별 성과(5일·20일·60일)와 섹터 ETF 수익률. 어느 섹터로 자금이 이동하는지, "
                    "지난주 주도 섹터가 무엇인지 본다.",
     "input_schema": {"type": "object", "properties": {"market": {"type": "string", "enum": ["KR", "US"]}},
                      "required": ["market"], "additionalProperties": False}},
    {"name": "screen_stocks",
     "description": "주식 패널(KR 거래대금 상위 약 500종목 / US S&P500∪Nasdaq100)을 원하는 조건으로 거른다. "
                    "필터·정렬은 전부 네가 정한다. 위생 필터(시총·거래대금 하한)는 기본으로 걸린다. 최대 30행.",
     "input_schema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["KR", "US"]},
         "sectors": {"type": "array", "items": {"type": "string"}, "description": "업종 문자열 부분 일치(OR). 생략 시 전체"},
         "exclude_sectors": {"type": "array", "items": {"type": "string"}},
         "name_contains": {"type": "string"},
         "filters": FILTER_SCHEMA,
         "sort_by": {"type": "string", "enum": NUM_COLS + ["adv20", "mktcap"]},
         "ascending": {"type": "boolean"},
         "n": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}},
         "required": ["market", "sort_by"], "additionalProperties": False}},
    {"name": "screen_etfs",
     "description": "ETF 목록을 거른다. tags: index, sector, foreign, bond_cash(단기채·머니마켓 등 현금성), inverse, "
                    "leveraged, commodity. 하락장 방어·헤지·지수 베팅에 쓴다. 최대 30행.",
     "input_schema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["KR", "US"]},
         "tags": {"type": "array", "items": {"type": "string"}, "description": "모두 포함(AND)"},
         "exclude_tags": {"type": "array", "items": {"type": "string"}},
         "name_contains": {"type": "string"},
         "filters": FILTER_SCHEMA,
         "sort_by": {"type": "string", "enum": NUM_COLS + ["adv20"]},
         "ascending": {"type": "boolean"},
         "n": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}},
         "required": ["market", "sort_by"], "additionalProperties": False}},
    {"name": "instrument_detail",
     "description": "종목·ETF 하나의 지표 전체, 최근 10거래일 일봉, 12주 주간 종가, (US) 실적 발표·배당락 예정일. "
                    "패널에 없는 종목도 티커로 조회된다 (KR 6자리 코드, US 티커).",
     "input_schema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["KR", "US"]}, "ticker": {"type": "string"}},
         "required": ["market", "ticker"], "additionalProperties": False}},
    {"name": "track_record",
     "description": "지난 주차 결과(제출 종목·수익률·벤치마크·당시 국면 판단)와 누적 교훈(지지·반증 주차 수 포함). "
                    "과적합 주의: 지지 3주 미만 교훈은 가설이다.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "write_note",
     "description": "작업 메모를 남긴다 (국면 판단, 탐색 계획, 후보 탈락 사유 등). 보고서와 발표 자료의 원자료가 된다. "
                    "국면 판단을 내리거나 바꿀 때마다 남겨라.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "text": {"type": "string"}},
         "required": ["title", "text"], "additionalProperties": False}},
    {"name": "request_critique",
     "description": "후보 하나에 대한 매수 논거를 별도 컨텍스트의 비판자에게 보내 반박을 받는다. 비판자는 웹 검색으로 "
                    "날짜·수치를 직접 확인하고, 동의하지 않도록 지시받았다. 이 대화는 보지 못한다. "
                    "최종 제출 종목은 반드시 비판을 받아야 한다. 횟수 제한이 있다.",
     "input_schema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["KR", "US"]},
         "ticker": {"type": "string"}, "name": {"type": "string"},
         "stance": {"type": "string", "enum": STANCES},
         "thesis": {"type": "string", "description": "매수 논거 전문. 국면 판단, 상승 시나리오, 트리거(날짜), 미반영 근거"},
         "catalyst": {"type": "string"},
         "expected_return_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
         "required": ["market", "ticker", "name", "stance", "thesis", "catalyst", "expected_return_range"],
         "additionalProperties": False}},
    {"name": "submit_recommendation",
     "description": "이번 주 최종 1종목을 확정한다. 검증에 실패하면 이유가 돌아오고 작업이 계속된다. 통과하면 작업이 끝난다.",
     "input_schema": {"type": "object", "properties": {
         "market": {"type": "string", "enum": ["KR", "US"]},
         "ticker": {"type": "string"}, "name": {"type": "string"},
         "instrument_type": {"type": "string", "enum": ["stock", "etf"]},
         "regime_view": {"type": "string", "enum": REGIMES},
         "regime_rationale": {"type": "string"},
         "stance": {"type": "string", "enum": STANCES},
         "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
         "thesis": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
         "catalyst": {"type": "string", "description": "보유 기간 내 날짜가 있는 이벤트. 없으면 없다고 쓴다"},
         "expected_return_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                                   "description": "[하단 %, 상단 %] — 보유 기간 수익률의 현실적 범위"},
         "primary_risk": {"type": "string"},
         "bull_case": {"type": "object", "properties": {
             "scenario": {"type": "string"}, "trigger": {"type": "string"}, "not_priced_in": {"type": "string"}},
             "required": ["scenario", "trigger", "not_priced_in"], "additionalProperties": False},
         "response_to_critique": {"type": "string", "description": "비판자의 핵심 반박과 그것을 받아들이거나 기각한 이유"},
         "alternatives": {"type": "array", "items": {"type": "object", "properties": {
             "market": {"type": "string"}, "ticker": {"type": "string"}, "name": {"type": "string"},
             "reason": {"type": "string"}}, "required": ["ticker", "reason"], "additionalProperties": False},
             "description": "끝까지 검토하다 탈락시킨 후보와 탈락 사유 (2~5개)"},
         "process_summary": {"type": "string", "description": "이번 탐색 과정 요약: 무엇을 보고, 어디서 되돌아갔고, 왜 이 결론인가"}},
         "required": ["market", "ticker", "name", "instrument_type", "regime_view", "regime_rationale", "stance",
                      "confidence", "thesis", "catalyst", "expected_return_range", "primary_risk", "bull_case",
                      "response_to_critique", "alternatives", "process_summary"],
         "additionalProperties": False}},
]


def tool_schemas() -> list[dict]:
    return [dict(s) for s in SCHEMAS]


# ------------------------------------------------------------------ 표 서식

def _fmt(v, col: str, market: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if col == "pct_from_52wh":
        return f"-{v * 100:.1f}%"
    if col in ("atr20_pct", "vol20"):
        return f"{v * 100:.1f}%"
    if col in PCT_COLS:
        return f"{v * 100:+.1f}%"
    if col in ("beta60", "vol_ratio"):
        return f"{v:.2f}"
    if col == "rsi14":
        return f"{v:.0f}"
    if col == "close":
        return f"{v:,.0f}" if market == "KR" else f"{v:,.2f}"
    if col == "mktcap":
        return f"{v / 1e8:,.0f}억" if market == "KR" else f"${v / 1e9:,.1f}B"
    if col == "adv20":
        return f"{v / 1e8:,.0f}억" if market == "KR" else f"${v / 1e6:,.0f}M"
    return str(v)


LABEL = {"ticker": "티커", "name": "종목명", "sector": "업종", "close": "종가", "mktcap": "시총", "adv20": "거래대금20",
         "ret1": "1일", "ret5": "5일", "ret20": "20일", "ret60": "60일", "ret_mom": "모멘텀(6m-1m)",
         "atr20_pct": "ATR20%", "vol20": "변동성(연)", "beta60": "베타60", "rsi14": "RSI", "ma20_gap": "MA20괴리",
         "ma60_gap": "MA60괴리", "pct_from_52wh": "52주고점대비", "vol_ratio": "거래량비(5/60)", "tags": "태그"}


def table(df: pd.DataFrame, cols: list[str], market: str) -> str:
    if df.empty:
        return "(조건을 만족하는 종목 없음)"
    rows = [[_fmt(r[c], c, market) if c in r else "" for c in cols] for _, r in df.iterrows()]
    head = "| " + " | ".join(LABEL.get(c, c) for c in cols) + " |"
    sep = "|" + "---|" * len(cols)
    body = ["| " + " | ".join(str(x).replace("|", "/")[:28] for x in r) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def _pct(v) -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:+.1f}%"


# ------------------------------------------------------------------ 도구 상자

class Toolbox:
    """한 번의 추천 실행이 쥐는 상태. 패널은 루프 시작 전에 prepare()로 미리 받는다 (LLM 대기 중 다운로드 방지)."""

    def __init__(self, cfg: dict, week: int, plan: dict, out: Path, provider=None, render_critic=None):
        self.cfg, self.week, self.plan, self.out = cfg, week, plan, out
        self.ac = cfg.get("agent") or {}
        self.provider = provider
        self.render_critic = render_critic           # (args) -> prompt text. run.py가 넣는다
        self.stocks: dict[str, pd.DataFrame] = {}
        self.etfs: dict[str, pd.DataFrame] = {}
        self.ov: pd.DataFrame | None = None
        self.critiques: dict[str, str] = {}          # "KR:005930" -> 비판 전문
        self.examined: dict[str, dict] = {}          # instrument_detail로 본 종목 (발표 자료 후보표)
        self.notes: list[tuple[str, str]] = []
        self.decision: dict | None = None
        self.calls: list[dict] = []                  # 도구 호출 기록 (run_meta)
        self.critic_usage: list[dict] = []
        inst = self.ac.get("instruments") or {}
        self.allow = {k: bool(inst.get(k, True)) for k in ("stocks", "etfs", "inverse", "leveraged")}

    def tool_schemas(self) -> list[dict]:
        return tool_schemas()

    # --- 준비
    def prepare(self, use_cache: bool = True) -> None:
        for m, p in self.plan.items():
            if self.allow["stocks"]:
                print(f"[데이터] {m} 주식 패널")
                self.stocks[m] = data.stock_panel(self.cfg, m, p["buy_date"], use_cache)
            if self.allow["etfs"]:
                print(f"[데이터] {m} ETF")
                e = data.etf_panel(self.cfg, m, p["buy_date"], use_cache)
                if not self.allow["inverse"]:
                    e = e[~e["tags"].str.contains("inverse")]
                if not self.allow["leveraged"]:
                    e = e[~e["tags"].str.contains("leveraged")]
                self.etfs[m] = e.reset_index(drop=True)
        ref = max(data.ref_date(p["buy_date"]) for p in self.plan.values())
        print("[데이터] 지수·매크로")
        self.ov = data.overview(ref)
        self.analog = {s: data.analogs(s, ref) for s in ("^KS11", "^GSPC")}

    def ref(self, market: str) -> pd.Timestamp:
        return data.ref_date(self.plan[market]["buy_date"])

    # --- 디스패치
    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(결과 텍스트, 오류 여부)."""
        self.calls.append({"tool": name, "args": {k: v for k, v in args.items() if k not in ("thesis", "text")},
                           "at": datetime.now().isoformat(timespec="seconds")})
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            return f"알 수 없는 도구: {name}", True
        if "market" in args and name != "submit_recommendation" and args["market"] not in self.plan:
            return (f"{args['market']} 시장은 이번 실행에서 제외됐다 (매수 시가가 지났거나 휴장). "
                    f"가능한 시장: {', '.join(self.plan)}"), True
        try:
            return fn(**args), False
        except TypeError as e:
            return f"인자 오류: {e}", True
        except Exception as e:  # 도구 하나가 실패해도 루프는 계속 간다
            return f"{name} 실행 실패: {type(e).__name__}: {e}", True

    # --- 조사
    def t_market_overview(self) -> str:
        ov = self.ov
        lines = ["## 시세표 (기준일 종가)", "| 지표 | 값 | 기준일 | 1일 | 1주 | 1개월 | 3개월 | MA20 대비 | MA60 대비 | MA200 대비 | 52주고점 대비 | 실현변동성(연) |",
                 "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in ov.iterrows():
            lines.append(f"| {r['label']} | {r['last']:,.2f} | {r['asof']} | {_pct(r['d1'])} | {_pct(r['w1'])} | {_pct(r['m1'])} | "
                         f"{_pct(r['m3'])} | {_pct(r['vs_ma20'])} | {_pct(r['vs_ma60'])} | {_pct(r['vs_ma200'])} | "
                         f"{_pct(r['from_52wh'])} | {r['vol20'] * 100:.0f}% |")
        lines += ["", "## 시장 폭 (주식 패널 기준)"]
        for m, p in self.stocks.items():
            b = data.breadth(p)
            lines.append(f"- {m} ({b['n']}종목): MA20 위 {b['above_ma20']:.0%} · MA60 위 {b['above_ma60']:.0%} · "
                         f"5일 상승 {b['up_5d']:.0%} · 5일 수익률 중앙값 {_pct(b['median_ret5'])} · "
                         f"20일 중앙값 {_pct(b['median_ret20'])} · 52주 고점 3% 이내 {b['near_52wh']:.0%}")
        lines += ["", "## 과거 유사 주간의 다음 주 지수 수익률 (금→금 종가, 15년)",
                  "base rate일 뿐이다. n이 작은 조건은 무게를 덜 둔다."]
        for s, a in self.analog.items():
            if not a:
                continue
            lines.append(f"- {s}: 지난주 {_pct(a['cur_r1'])}, 4주 {_pct(a['cur_r4'])}, 40주 이평 "
                         f"{'위' if a['cur_above_ma40w'] else '아래'}")
            for k, lab in (("all", "전체"), ("same_trend_and_r1_tercile", "같은 추세·같은 주간 수익률 3분위"),
                           ("r1_at_least_this_extreme", "지난주 이상으로 극단"), ("r4_at_least_this_extreme", "4주 수익률 이상으로 극단")):
                d = a[k]
                if d.get("n"):
                    lines.append(f"  - {lab}: n={d['n']} · 평균 {_pct(d['mean'])} · 중앙값 {_pct(d['median'])} · "
                                 f"상승확률 {d['p_up']:.0%} · 10%/90% 분위 {_pct(d['p10'])} / {_pct(d['p90'])}")
        lines += ["", "## 평가 주간"]
        for m, p in self.plan.items():
            lines.append(f"- {m}: {p['buy_date']} 시가 매수 → {p['sell_date']} 종가 매도 ({p['holding_days']}영업일)"
                         + (f" · 휴장 {', '.join(p['holidays'])}" if p.get("holidays") else ""))
        from .schedule import macro_dates
        lines += [f"- {x}" for x in macro_dates(self.plan)]
        try:
            from app.calendar_data import auto, auto_events, user_events
            lo = min(p["buy_date"] for p in self.plan.values())
            hi = max(p["sell_date"] for p in self.plan.values())
            for e in user_events():
                if lo <= str(e.get("date", "")) <= hi:
                    lines.append(f"- {e.get('date')} {e.get('title', '')} (사용자 등록)")
            for e in auto_events():
                if lo <= str(e.get("date", "")) <= hi:
                    t = f" (한국시간 {e['time_kst']})" if e.get("time_kst") else ""
                    lines.append(f"- {e['date']} [{e.get('market', '')}] {e['title']}{t}")
            if auto().get("fetched_at"):
                lines.append(f"  (위 대형 일정은 {auto()['fetched_at'][:10]}에 자동 검색한 것이다. 시각·내용은 확인할 것)")
        except Exception:
            pass
        lines.append("- 그 밖의 경제지표·실적 일정은 웹 검색으로 확인할 것")
        return "\n".join(lines)

    def t_sector_rotation(self, market: str) -> str:
        p = self.stocks.get(market)
        out = []
        if p is not None and len(p):
            sec = p.assign(sector=p["sector"].fillna("").replace("", "(미분류)"))
            if market == "KR":
                sec["sector"] = sec["sector"].str.split(" / ").str[-1]      # 업종 세분류
            g = sec.groupby("sector").agg(n=("ticker", "size"), ret5=("ret5", "median"), ret20=("ret20", "median"),
                                          ret60=("ret60", "median"), above20=("ma20_gap", lambda x: (x > 0).mean()))
            g = g[g["n"] >= 3].sort_values("ret20", ascending=False)
            out += [f"## {market} 업종별 중앙값 (종목 3개 이상)", "| 업종 | 종목수 | 5일 | 20일 | 60일 | MA20 위 비율 |", "|---|---|---|---|---|---|"]
            out += [f"| {k[:30]} | {int(r['n'])} | {_pct(r['ret5'])} | {_pct(r['ret20'])} | {_pct(r['ret60'])} | {r['above20']:.0%} |"
                    for k, r in g.iterrows()]
        e = self.etfs.get(market)
        if e is not None and len(e):
            se = e[e["tags"].isin(["sector", "index"]) | e["tags"].str.contains("foreign|commodity")]
            se = se.sort_values("ret20", ascending=False)
            pick = pd.concat([se.head(15), se.tail(10)]).drop_duplicates("ticker")
            out += ["", f"## {market} 섹터·테마 ETF (20일 수익률 상위 15 / 하위 10)",
                    table(pick, ["ticker", "name", "ret5", "ret20", "ret60", "vol_ratio"], market)]
        return "\n".join(out) or "(데이터 없음)"

    def _filter(self, d: pd.DataFrame, filters: dict | None) -> pd.DataFrame:
        for k, (lo, hi) in (filters or {}).items():
            if k not in d.columns:
                raise ValueError(f"알 수 없는 지표 {k}. 쓸 수 있는 키: {', '.join(NUM_COLS)}")
            if lo is not None:
                d = d[d[k] >= lo]
            if hi is not None:
                d = d[d[k] <= hi]
        return d

    def t_screen_stocks(self, market: str, sort_by: str, sectors=None, exclude_sectors=None, name_contains=None,
                        filters=None, ascending: bool = False, n: int = 20) -> str:
        if market not in self.stocks:
            return "이번 실행에서는 주식을 쓰지 않는다 (config agent.instruments.stocks)."
        d = self.stocks[market]
        f = self.cfg["filters"]
        # 위생 필터 — 거래정지·작전주 회피와 발표 방어 (알파 아님)
        if market == "KR":
            d = d[(d["mktcap"] >= f["mktcap_min_krw"]) & (d["adv20"].rank(ascending=False) <= f["adv20_rank_max_kr"])]
        else:
            d = d[(d["adv20"] >= f["adv20_min_usd_us"]) & (d["mktcap"].isna() | (d["mktcap"] >= f["mktcap_min_usd"]))]
        base = len(d)
        s = d["sector"].fillna("")
        if sectors:
            d = d[s.loc[d.index].apply(lambda x: any(k.lower() in x.lower() for k in sectors))]
        if exclude_sectors:
            d = d[~d["sector"].fillna("").apply(lambda x: any(k.lower() in x.lower() for k in exclude_sectors))]
        if name_contains:
            d = d[d["name"].str.contains(name_contains, case=False, regex=False)]
        d = self._filter(d, filters)
        hit = len(d)
        d = d.sort_values(sort_by, ascending=bool(ascending), na_position="last").head(min(int(n), MAX_ROWS))
        cols = ["ticker", "name", "sector", "close", "mktcap", "ret5", "ret20", "ret_mom", "atr20_pct", "beta60", "rsi14",
                "ma20_gap", "pct_from_52wh", "vol_ratio"]
        return f"위생 필터 통과 {base}종목 → 조건 통과 {hit}종목 → {sort_by} {'오름' if ascending else '내림'}차순 상위 {len(d)}\n" + table(d, cols, market)

    def t_screen_etfs(self, market: str, sort_by: str, tags=None, exclude_tags=None, name_contains=None, filters=None,
                      ascending: bool = False, n: int = 20) -> str:
        if market not in self.etfs:
            return "이번 실행에서는 ETF를 쓰지 않는다 (config agent.instruments.etfs)."
        d = self.etfs[market]
        for t in tags or []:
            d = d[d["tags"].str.contains(t, regex=False)]
        for t in exclude_tags or []:
            d = d[~d["tags"].str.contains(t, regex=False)]
        if name_contains:
            d = d[d["name"].str.contains(name_contains, case=False, regex=False)]
        d = self._filter(d, filters)
        hit = len(d)
        d = d.sort_values(sort_by, ascending=bool(ascending), na_position="last").head(min(int(n), MAX_ROWS))
        return f"조건 통과 {hit}개 → 상위 {len(d)}\n" + table(
            d, ["ticker", "name", "tags", "close", "adv20", "ret1", "ret5", "ret20", "ret60", "vol20", "beta60", "rsi14"], market)

    def lookup(self, market: str, ticker: str) -> pd.Series | None:
        t = str(ticker).strip().upper()
        if market == "KR" and t.isdigit():
            t = t.zfill(6)
        for d in (self.stocks.get(market), self.etfs.get(market)):
            if d is not None and len(d):
                hit = d[d["ticker"].astype(str).str.upper() == t]
                if len(hit):
                    return hit.iloc[0]
        return None

    def t_instrument_detail(self, market: str, ticker: str) -> str:
        t = str(ticker).strip().upper()
        if market == "KR" and t.isdigit():
            t = t.zfill(6)
        ref = self.ref(market)
        h = data.history_one(market, t, ref)
        if h is None or h.empty:
            return f"{market} {t}: 시세를 찾지 못했다. 티커를 확인할 것 (KR은 6자리 코드, US는 yfinance 표기 예: BRK-B)."
        bench = data.bench_close(market, ref)
        ind = data.indicators(h, bench, "Adj Close" if "Adj Close" in h.columns else "Close") or {}
        row = self.lookup(market, t)
        name = row["name"] if row is not None else ""
        kind = row["kind"] if row is not None else "unknown"
        rec = {"market": market, "ticker": t, "name": name, "kind": kind,
               "sector": (row["sector"] if row is not None else ""), **ind,
               "mktcap": (row["mktcap"] if row is not None and "mktcap" in row else math.nan)}
        self.examined[f"{market}:{t}"] = rec
        lines = [f"## {market} {t} {name} ({'ETF' if kind == 'etf' else '주식' if kind == 'stock' else '패널 밖 종목'})",
                 f"업종/태그: {rec['sector'] or '-'} · 시총 {_fmt(rec['mktcap'], 'mktcap', market)} · 거래대금20 {_fmt(ind.get('adv20'), 'adv20', market)}",
                 " · ".join(f"{LABEL.get(k, k)} {_fmt(ind.get(k), k, market)}" for k in NUM_COLS),
                 "", "최근 10거래일 | 날짜 | 시가 | 고가 | 저가 | 종가 | 등락 | 거래량 |", "|---|---|---|---|---|---|---|---|"]
        tail = h.tail(11)
        prev = tail["Close"].shift(1)
        for i, (d, r) in enumerate(tail.iterrows()):
            if i == 0:
                continue
            f = (lambda v: f"{v:,.0f}") if market == "KR" else (lambda v: f"{v:,.2f}")
            lines.append(f"| {d:%m-%d} | {f(r['Open'])} | {f(r['High'])} | {f(r['Low'])} | {f(r['Close'])} | "
                         f"{(r['Close'] / prev.loc[d] - 1) * 100:+.1f}% | {r['Volume']:,.0f} |")
        wk = h["Close"].resample("W-FRI").last().dropna().tail(12)
        lines += ["", "주간 종가(12주): " + " → ".join(f"{v:,.2f}" if market == "US" else f"{v:,.0f}" for v in wk)]
        if market == "US" and kind != "etf":
            ev = data.us_events(t)
            lines.append("예정 이벤트(yfinance, 확인 필요): " + (json.dumps(ev, ensure_ascii=False) if ev else "조회 안 됨 — 웹 검색으로 확인"))
        return "\n".join(lines)

    def t_track_record(self) -> str:
        return memory.track_record(self.week)

    def t_write_note(self, title: str, text: str) -> str:
        self.notes.append((title, text))
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "notes.md").open("a", encoding="utf-8") as f:
            f.write(f"\n## {title}  ({datetime.now():%H:%M:%S})\n\n{text}\n")
        return f"메모 저장 ({len(self.notes)}번째)"

    # --- 검증
    def t_request_critique(self, market: str, ticker: str, name: str, stance: str, thesis: str, catalyst: str,
                           expected_return_range: list) -> str:
        used = len(self.critiques)
        cap = int(self.ac.get("max_critiques") or 3)
        t = str(ticker).strip().upper()
        if market == "KR" and t.isdigit():
            t = t.zfill(6)
        key = f"{market}:{t}"
        if key in self.critiques:
            return "이미 이 종목의 비판을 받았다. 같은 내용을 다시 보여 준다:\n\n" + self.critiques[key]
        if used >= cap:
            return f"비판 횟수({cap}회)를 다 썼다. 이미 비판을 받은 종목 중에서 고르거나 그 비판에 답해 제출하라: {', '.join(self.critiques)}"
        if self.provider is None or self.render_critic is None:
            return "비판자 LLM이 연결되지 않았다 (오프라인 시험 실행)."
        if t not in self.examined and self.lookup(market, t) is None:
            # 지표 없이 비판하면 비판자가 가격을 지어낸다. 먼저 조회한다
            self.t_instrument_detail(market, t)
        rec = self.examined.get(key) or {}
        metrics = " · ".join(f"{LABEL.get(k, k)} {_fmt(rec.get(k), k, market)}" for k in ["close"] + NUM_COLS)
        prompt = self.render_critic(market=market, ticker=t, name=name, stance=stance, thesis=thesis,
                                    catalyst=catalyst, rng=expected_return_range, metrics=metrics)
        model = self.ac.get("critic_model") or self.ac.get("model")
        budget = int((self.ac.get("search_budget") or {}).get("critic") or 5)
        print(f"  [critic] {key} {name} — {model}, 검색 {budget}회")
        t0 = datetime.now()
        reply = self.provider.complete(model=model, prompt=prompt, max_tokens=int(self.ac.get("critic_max_tokens") or 16000),
                                       temperature=None, web_search=True, max_searches=budget)
        n = used + 1
        f = self.out / f"critique_{n}_{t}.md"
        f.write_text(reply.text, encoding="utf-8")
        (self.out / f"critique_{n}_{t}.prompt.txt").write_text(prompt, encoding="utf-8")
        self.critiques[key] = reply.text
        self.critic_usage.append({"ticker": t, "market": market, "model": reply.model, "usage": reply.usage,
                                  "web_search_calls": reply.web_searches,
                                  "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1)})
        return f"[비판 {n}/{cap}] — {name}({t})\n\n{reply.text}"

    def t_submit_recommendation(self, **a) -> str:
        errs = []
        m = a.get("market")
        t = str(a.get("ticker", "")).strip().upper()
        if m == "KR" and t.isdigit():
            t = t.zfill(6)
        if m not in self.plan:
            errs.append(f"{m} 시장은 이번 주 제출할 수 없다 (가능: {', '.join(self.plan)})")
        row = self.lookup(m, t) if m in self.plan else None
        if m in self.plan and row is None and f"{m}:{t}" not in self.examined:
            errs.append(f"{m} {t}의 시세를 확인하지 않았다. instrument_detail로 먼저 조회할 것")
        if row is not None:
            tags = str(row.get("tags") or "")
            if "inverse" in tags and not self.allow["inverse"]:
                errs.append("인버스 ETF는 config에서 막혀 있다")
            if "leveraged" in tags and not self.allow["leveraged"]:
                errs.append("레버리지 ETF는 config에서 막혀 있다")
        if self.ac.get("require_critique", True) and f"{m}:{t}" not in self.critiques:
            errs.append("최종 종목은 request_critique로 비판을 받아야 한다" +
                        (f" (비판받은 종목: {', '.join(self.critiques)})" if self.critiques else ""))
        if not self.notes:
            errs.append("write_note로 국면 판단과 자세를 먼저 남겨라")
        for mk in self.plan:
            if not any(c["tool"] in ("screen_stocks", "screen_etfs") and c["args"].get("market") == mk for c in self.calls):
                errs.append(f"{mk} 시장을 한 번도 스크리닝하지 않았다. 제출 가능한 시장은 모두 살펴봐야 한다")
        if row is not None and "bond_cash" in str(row.get("tags") or ""):
            risky = [k for k, r in self.examined.items() if "bond_cash" not in str(r.get("sector") or "")]
            if len(risky) < 4:
                errs.append(f"현금성 ETF는 마지막 수단이다. 현금이 아닌 후보를 instrument_detail로 4개 이상 검토하고 "
                            f"각각의 기대수익이 0보다 낮다고 판단한 근거를 남겨라 (지금 {len(risky)}개)")
        rng = a.get("expected_return_range") or []
        try:
            lo, hi = float(rng[0]), float(rng[1])
            if not lo < hi:
                errs.append("expected_return_range는 [하단, 상단]이고 하단 < 상단이어야 한다")
            if lo < -60 or hi > 100:
                errs.append("expected_return_range가 비현실적이다 (주간 % 단위인지 확인)")
        except Exception:
            errs.append("expected_return_range는 숫자 2개여야 한다")
        # 게이트웨이 경유라 스키마가 강제되지 않는다 (wk8: bull_case가 빈 문자열로 왔다). 형식은 여기서 검사한다
        bc = a.get("bull_case")
        if isinstance(bc, str):
            try:
                bc = json.loads(bc)
            except Exception:
                bc = None
        if not (isinstance(bc, dict) and all(str(bc.get(k) or "").strip() for k in ("scenario", "trigger", "not_priced_in"))):
            errs.append("bull_case는 scenario·trigger·not_priced_in 세 키를 모두 채운 객체여야 한다")
        else:
            a["bull_case"] = bc
        for k, typ in (("thesis", list), ("alternatives", list)):
            if not isinstance(a.get(k), typ):
                errs.append(f"{k}는 배열이어야 한다")
        for k in ("catalyst", "primary_risk", "response_to_critique", "regime_rationale", "process_summary"):
            if not str(a.get(k) or "").strip():
                errs.append(f"{k}가 비어 있다")
        if any(str(x.get("ticker", "")).strip().upper().lstrip("0") == t.lstrip("0") for x in a.get("alternatives") or []):
            errs.append("제출 종목이 alternatives(탈락 후보)에 들어 있다")
        if errs:
            return "제출 거부 — 고쳐서 다시 제출하라:\n- " + "\n- ".join(errs)
        a = dict(a, ticker=t)
        if row is not None:
            a["name"] = a.get("name") or row["name"]
        self.decision = a
        return "제출 완료. 작업을 끝낸다."


def parse_json_block(text: str) -> dict | None:
    fence = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for body in reversed(fence or [text[text.find("{"): text.rfind("}") + 1]] if "{" in text else fence):
        try:
            return json.loads(body)
        except Exception:
            continue
    return None


def today() -> str:
    return date.today().isoformat()
