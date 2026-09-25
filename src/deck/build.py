"""발표 자료(8슬라이드 pptx) 생성.

**이 패키지는 파이프라인에서 완전히 분리되어 있다.** outputs/ 아래의 산출물과
log.csv, config.yaml만 읽고, screener·agents·llm을 import하지 않는다.
`src/deck/`를 통째로 지워도 파이프라인은 그대로 돈다.

원칙은 '간단하게'다. 발표 주차가 아니면 손대지 않고 그대로 제출할 수 있어야 하므로,
모든 칸을 파일에서 기계적으로 채우고 없는 것은 비워 둔다 (거짓으로 채우지 않는다).

    python src/deck/build.py --week 3 --market KR
    python src/deck/build.py --week 3 --market KR --full   # 발표하는 주차
    python src/deck/build.py --week 4 --final              # 사용자 최종 선택 종목
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chart as chart_mod  # noqa: E402

W, H = 13.333, 7.5          # 16:9 인치
ACCENT = (0x1F, 0x3A, 0x5F)
MUTED = (0x6B, 0x72, 0x80)
POS = (0xC0, 0x39, 0x2B)    # 한국식: 상승 빨강
NEG = (0x1D, 0x4E, 0xD8)


# ------------------------------------------------------------------ 입력

def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def log_row(week: int, market: str) -> dict:
    f = ROOT / "log.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("week")) == str(week) and r.get("market") == market:
                return r
    return {}


def section(md: str, *keys: str) -> str:
    """'## 종목명 (티커)' 형태의 섹션 본문을 다음 헤딩 직전까지 뽑는다.

    문서 맨 위의 '# 상승 논거 — A · B · C' 같은 제목에도 종목명이 들어 있다.
    그걸 잡으면 첫 종목 섹션 전체를 엉뚱하게 가져오므로, 후보 중 가장 깊은(구체적인)
    헤딩을 고른다.
    """
    if not md:
        return ""
    lines = md.splitlines()
    hits = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m and any(k and k in m.group(2) for k in keys):
            hits.append((len(m.group(1)), i))
    if not hits:
        return ""
    level, idx = max(hits)          # 가장 깊은 헤딩 = 그 종목만 다루는 섹션
    start = idx + 1
    body = []
    for ln in lines[start:]:
        m = re.match(r"^(#{1,4})\s", ln)
        if m and len(m.group(1)) <= level:
            break
        body.append(ln)
    return "\n".join(body).strip()


def labelled(sec: str, label: str) -> str:
    """'**4. 절대 사면 안 되는 이유**' 같은 소제목 아래 본문 한 덩어리."""
    if not sec:
        return ""
    m = re.search(rf"\*\*[^*]*{re.escape(label)}[^*]*\*\*\s*\n+(.+?)(?=\n\*\*|\Z)",
                  sec, re.S)
    return clean(m.group(1)) if m else ""


def clean(s: str) -> str:
    s = re.sub(r"^[\s>\-*•]+", "", s.strip(), flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ------------------------------------------------------------------ 슬라이드

class Deck:
    def __init__(self, subtitle: str):
        from pptx import Presentation
        from pptx.util import Inches
        self.p = Presentation()
        self.p.slide_width, self.p.slide_height = Inches(W), Inches(H)
        self.subtitle = subtitle
        self.n = 0

    def _blank(self):
        return self.p.slides.add_slide(self.p.slide_layouts[6])

    def _box(self, s, x, y, w, h, text, size=16, bold=False, color=(0, 0, 0),
             align=None, wrap=True):
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = wrap
        lines = str(text).split("\n")
        for i, ln in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = ln
            if align is not None:
                para.alignment = align
            for r in para.runs:
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = RGBColor(*color)
                r.font.name = "맑은 고딕"
        return tb

    def _chrome(self, s, title: str):
        """슬라이드 상단 제목 + 하단 푸터."""
        from pptx.util import Pt
        self.n += 1
        self._box(s, 0.6, 0.35, W - 1.2, 0.7, title, size=26, bold=True, color=ACCENT)
        self._box(s, 0.6, H - 0.6, W - 1.2, 0.35,
                  f"{self.subtitle}          {self.n} / 8", size=10, color=MUTED)
        return Pt

    def title_slide(self, title, lines):
        s = self._blank()
        self.n += 1
        self._box(s, 0.9, 2.3, W - 1.8, 1.2, title, size=40, bold=True, color=ACCENT)
        self._box(s, 0.9, 3.7, W - 1.8, 2.2, "\n".join(lines), size=18)
        self._box(s, 0.9, H - 0.8, W - 1.8, 0.4, self.subtitle, size=11, color=MUTED)

    def bullets(self, title, items, note=""):
        s = self._blank()
        self._chrome(s, title)
        body = "\n".join(f"• {clip(i, 150)}" if not str(i).startswith(" ") else str(i)
                         for i in items if str(i).strip())
        self._box(s, 0.8, 1.35, W - 1.6, H - 2.3, body or "(자료 없음)", size=15)
        if note:
            self._box(s, 0.8, H - 1.15, W - 1.6, 0.5, note, size=11, color=MUTED)

    def two_col(self, title, left_title, left, right_title, right):
        s = self._blank()
        self._chrome(s, title)
        cw = (W - 2.0) / 2
        for i, (ht, items) in enumerate(((left_title, left), (right_title, right))):
            x = 0.8 + i * (cw + 0.4)
            self._box(s, x, 1.3, cw, 0.4, ht, size=17, bold=True,
                      color=POS if i == 0 else NEG)
            body = "\n".join(f"• {clip(t, 120)}" for t in items if str(t).strip())
            self._box(s, x, 1.85, cw, H - 2.9, body or "(자료 없음)", size=13)

    def image(self, title, png, caption=""):
        from pptx.util import Inches
        s = self._blank()
        self._chrome(s, title)
        if png and Path(png).exists():
            iw = W - 2.6
            s.shapes.add_picture(str(png), Inches(1.3), Inches(1.25), width=Inches(iw))
        else:
            self._box(s, 1.3, 3.0, W - 2.6, 1.0,
                      "[ 차트 이미지 — 직접 넣을 것 ]", size=18, color=MUTED)
        if caption:
            self._box(s, 0.8, H - 1.25, W - 1.6, 0.6, caption, size=12, color=MUTED)

    def table(self, title, header, rows, note=""):
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt
        s = self._blank()
        self._chrome(s, title)
        nr, nc = len(rows) + 1, len(header)
        shape = s.shapes.add_table(nr, nc, Inches(0.7), Inches(1.3),
                                   Inches(W - 1.4), Inches(min(0.32 * nr, H - 2.4)))
        t = shape.table
        for c, h in enumerate(header):
            cell = t.cell(0, c)
            cell.text = str(h)
            for para in cell.text_frame.paragraphs:
                for r in para.runs:
                    r.font.size, r.font.bold = Pt(11), True
                    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                    r.font.name = "맑은 고딕"
        for i, row in enumerate(rows, start=1):
            for c, v in enumerate(row):
                cell = t.cell(i, c)
                cell.text = str(v)
                for para in cell.text_frame.paragraphs:
                    for r in para.runs:
                        r.font.size = Pt(10)
                        r.font.name = "맑은 고딕"
        if note:
            self._box(s, 0.7, H - 1.1, W - 1.4, 0.5, note, size=11, color=MUTED)

    def save(self, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.p.save(str(dst))
        return dst


# ------------------------------------------------------------------ 본체

PIPELINE = [
    "Step 1 — 스크리닝 (LLM 호출 없음)",
    "   가격·거래대금 데이터로 전종목을 걸러 후보 20종목 확정",
    "   유동성 → 시가총액 → 중기 모멘텀+단기 반전 → 변동성 정렬",
    "",
    "Step 2 — Analyst (LLM + 웹 검색) : 20종목 → 3종목",
    "   뉴스·공시로 검증해 탈락시키는 단계. 선택은 하지 않는다",
    "",
    "Step 3 — Bull → Bear → Trader (LLM) : 3종목 → 1종목",
    "   매수 논거 구성 → 반박(동의 금지 강제) → 비교 후 최종 선택",
    "",
    "각 단계는 이전 단계의 '응답 텍스트'만 받는다 (대화 히스토리 누적 금지).",
    "Bear가 Bull과 같은 맥락에 있으면 반박하지 않고 동조하기 때문이다.",
]


def build(week: int, market: str, full: bool = False,
          no_chart: bool = False) -> Path:
    out = ROOT / "outputs" / f"wk{week}" / market
    if not out.exists():
        raise SystemExit(f"{out} 가 없다.")

    cfg = {}
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    except Exception:
        pass
    mk = (cfg.get("markets") or {}).get(market, {})
    label = mk.get("label", market)

    pick = read_json(out / "pick.json")
    smeta = read_json(out / "screen_meta.json")
    rmeta = read_json(out / "run_meta.json")
    row = log_row(week, market)

    ticker = str(pick.get("ticker", "")) or str(row.get("ticker", ""))
    name = pick.get("name") or row.get("name") or ""
    buy = row.get("buy_date") or smeta.get("buy_date", "")
    sell = row.get("sell_date") or smeta.get("sell_date", "")
    ret = row.get("ret_pct", "")
    bret = row.get("benchmark_ret_pct", "")
    excess = row.get("excess_pct", "")

    analyst = read_text(out / "responses" / "analyst.md")
    bull = read_text(out / "responses" / "bull.md")
    bear = read_text(out / "responses" / "bear.md")

    sec_bull = section(bull, ticker, name)
    sec_bear = section(bear, ticker, name)

    deck = Deck(f"wk{week} · {label} · {name}({ticker})")

    # 1. 표지 ---------------------------------------------------------------
    perf = f"{ret}%" if ret != "" else "(정산 전)"
    deck.title_slide(
        f"{name} ({ticker})",
        [f"{week}주차 · {label}",
         f"{buy} 시가 매수 → {sell} 종가 매도",
         "",
         f"주간 수익률   {perf}"
         + (f"      벤치마크({row.get('benchmark','')}) {bret}%" if bret != "" else ""),
         f"초과수익      {excess}%p" if excess != "" else ""])

    # 2. 파이프라인 ---------------------------------------------------------
    deck.bullets("파이프라인 구조", PIPELINE,
                 "LLM에게 '오를 종목을 추천해줘'라고 묻지 않는다. "
                 "후보는 기계적으로 먼저 확정하고, LLM은 그 안에서만 탈락·선택한다.")

    # 3. 사용 모델 및 프롬프트 ----------------------------------------------
    stages = rmeta.get("stages") or {}
    srows = []
    for st in ("analyst", "bull", "bear", "trader"):
        v = stages.get(st) or {}
        u = v.get("usage") or {}
        # manual 모드에는 web_search가 기록되지 않는다. 그 경우 파이프라인 규약
        # (analyst·bear만 검색)을 그대로 쓴다 — 없는 값을 '미사용'으로 적으면 거짓이 된다.
        ws = v.get("web_search")
        if ws is None:
            ws = st in ("analyst", "bear")
        srows.append([
            st, v.get("model") or "(수동 실행)",
            "사용" if ws else "미사용",
            f"{u.get('input_tokens', ''):,}" if u.get("input_tokens") else "",
            f"{u.get('output_tokens', ''):,}" if u.get("output_tokens") else "",
        ])
    deck.table("사용 모델 및 프롬프트",
               ["단계", "모델", "웹 검색", "입력 토큰", "출력 토큰"], srows,
               f"프롬프트 버전 {rmeta.get('prompt_version', '-')} · "
               f"스크리닝 설정 {rmeta.get('config_version', smeta.get('config_version', '-'))} · "
               f"실행 모드 {rmeta.get('mode', '-')}   |   프롬프트 전문은 제출 문서 참조")

    # 4. Step 1 스크리닝 결과 ------------------------------------------------
    crows, n_total = [], 0
    try:
        import pandas as pd
        df = pd.read_csv(out / "candidates.csv", dtype={"ticker": str})
        n_total = len(df)
        shown = df.head(10)
        # 선택된 종목이 상위 10위 밖이면 표에서 사라진다. 그 한 줄은 반드시 붙인다.
        if ticker and ticker not in set(shown["ticker"]):
            extra = df[df.ticker == ticker]
            shown = pd.concat([df.head(9), extra]) if len(extra) else shown
        for _, r in shown.iterrows():
            crows.append([r["ticker"], clip(r["name"], 14),
                          f"{r['ret_mom'] * 100:+.0f}%", f"{r['ret5'] * 100:+.1f}%",
                          f"{r['atr20_pct'] * 100:.1f}%",
                          "★ 선택" if str(r["ticker"]) == ticker else ""])
    except Exception:
        pass
    ref = smeta.get("ref_date") or "(기록 없음)"
    deck.table(f"Step 1 — 기계적 스크리닝 결과 (상위 {len(crows)} / {n_total or '?'}종목)",
               ["티커", "종목명", "6개월 모멘텀", "5일", "ATR20", "선택"], crows,
               f"기준일 {ref} 종가 · 후보 {smeta.get('n_candidates', n_total)}종목 "
               f"→ Analyst가 3종목으로 압축 → Trader가 1종목 선택")

    # 5. 종목 설명 -----------------------------------------------------------
    thesis = pick.get("thesis") or []
    deck.bullets(f"{name} — 선정 근거",
                 list(thesis) + ["", f"Catalyst : {clip(pick.get('catalyst', ''), 160)}"],
                 "출처: Trader 단계 구조화 출력(pick.json)")

    # 6. Bull vs Bear --------------------------------------------------------
    bull_items = [labelled(sec_bull, "시나리오"), labelled(sec_bull, "트리거"),
                  labelled(sec_bull, "미반영")]
    bear_items = [labelled(sec_bear, "시나리오"), labelled(sec_bear, "검증"),
                  labelled(sec_bear, "절대 사면 안 되는 이유")]
    bull_items = [b for b in bull_items if b] or ["(추출 실패 — responses/bull.md 참조)"]
    bear_items = [b for b in bear_items if b] or ["(추출 실패 — responses/bear.md 참조)"]
    deck.two_col("Bull vs Bear", "매수 측", bull_items, "매도 측", bear_items)

    # 7. 차트 ---------------------------------------------------------------
    png = out / f"chart_{ticker}.png"
    if not no_chart and ticker and not png.exists():
        chart_mod.render(ticker, name, market, sell or smeta.get("ref_date", ""),
                         png, buy, sell)
    tech = ""
    try:
        import pandas as pd
        df = pd.read_csv(out / "candidates.csv", dtype={"ticker": str})
        r = df[df.ticker == ticker]
        if len(r):
            r = r.iloc[0]
            tech = (f"6개월 모멘텀(최근 1개월 제외) {r['ret_mom'] * 100:+.1f}%  ·  "
                    f"20일 {r['ret20'] * 100:+.1f}%  ·  5일 {r['ret5'] * 100:+.1f}%  ·  "
                    f"52주 고점 대비 -{r['pct_from_52wh'] * 100:.1f}%  ·  "
                    f"ATR20/종가 {r['atr20_pct'] * 100:.1f}%")
    except Exception:
        pass
    deck.image("6개월 일봉 · 기술적 지표", png if png.exists() else None,
               tech + ("\n음영 = 평가 구간" if tech else ""))

    # 8. 사후 검증 -----------------------------------------------------------
    lo, hi = pick.get("expected_return_range") or ["", ""]
    verdict = []
    if ret != "":
        verdict.append(f"실제 {ret}%  vs  예측 {lo}~{hi}%   "
                       f"→ 예측 범위 {'적중' if row.get('hit_range') == 'Y' else '이탈'}")
        verdict.append(f"벤치마크 {row.get('benchmark', '')} {bret}%  →  초과수익 {excess}%p")
    else:
        verdict.append("(아직 정산 전 — python src/settle.py 실행)")
    verdict += ["", f"예상했던 최대 리스크 : {clip(pick.get('primary_risk', ''), 200)}", ""]
    verdict += ["무엇이 맞고 무엇이 틀렸는지 한 줄로 :", "  (직접 작성)"]
    deck.bullets("사후 검증 — 예측 대비 실제", verdict,
                 "주차별 기록은 log.csv에 누적된다. 학기말 hit rate 분석의 원자료다.")

    dst = out / f"deck_wk{week}_{market}_{ticker}.pptx"
    deck.save(dst)
    print(f"\n저장: {chart_mod.rel(dst)}  ({deck.n}슬라이드)")
    return dst


def final_row(week: int) -> dict:
    f = ROOT / "final_log.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return next((r for r in csv.DictReader(fh) if str(r.get("week")) == str(week)), {})


def build_final(week: int, full: bool = False, no_chart: bool = False) -> Path:
    """사용자 최종 선택의 발표 자료. 에이전트 추천을 골랐으면 그 시장 자료를 그대로 만든다.

    직접 지정한 종목은 에이전트 과정(Analyst/Bull/Bear)이 없으므로 없는 슬라이드를 지어내지 않고,
    '에이전트 추천 vs 내 선택' 비교와 사용자가 적은 선택 사유로 대신한다.
    """
    wdir = ROOT / "outputs" / f"wk{week}"
    fin = read_json(wdir / "final.json")
    if not fin:
        raise SystemExit(f"wk{week}의 최종 선택(final.json)이 없다.")
    if fin.get("choice") in ("KR", "US"):
        return build(week, fin["choice"], full, no_chart)

    out = wdir / "final"
    out.mkdir(parents=True, exist_ok=True)
    m, ticker, name = fin["market"], str(fin["ticker"]), fin.get("name", "")
    row = final_row(week)
    smeta = read_json(wdir / m / "screen_meta.json")
    buy = row.get("buy_date") or smeta.get("buy_date", "")
    sell = row.get("sell_date") or smeta.get("sell_date", "")
    ret, bret, excess = row.get("ret_pct", ""), row.get("benchmark_ret_pct", ""), row.get("excess_pct", "")
    label = {"KR": "한국", "US": "미국"}[m]
    deck = Deck(f"wk{week} · 최종 선택 · {name}({ticker})")

    deck.title_slide(f"{name} ({ticker})", [
        f"{week}주차 · {label} · 사용자 직접 선택", f"{buy} 시가 매수 → {sell} 종가 매도", "",
        f"주간 수익률   {ret + '%' if ret != '' else '(정산 전)'}"
        + (f"      벤치마크({row.get('benchmark', '')}) {bret}%" if bret != "" else ""),
        f"초과수익      {excess}%p" if excess != "" else ""])
    deck.bullets("파이프라인 구조", PIPELINE,
                 "이번 주 최종 제출 종목은 에이전트 추천이 아니라 사용자가 직접 골랐다. 다음 장에서 비교한다.")

    rows = []
    for mk in ("KR", "US"):
        pk = read_json(wdir / mk / "pick.json")
        if pk:
            lr = log_row(week, mk)
            rows.append([f"에이전트 추천 ({'한국' if mk == 'KR' else '미국'})", f"{pk.get('name', '')} {pk.get('ticker', '')}",
                         "{}~{}%".format(*(pk.get("expected_return_range") or ["", ""])),
                         f"{lr['ret_pct']}%" if lr.get("ret_pct") else "(정산 전)"])
    rows.append(["최종 선택 (사용자)", f"{name} {ticker}", "-", f"{ret}%" if ret != "" else "(정산 전)"])
    deck.table("에이전트 추천 vs 최종 선택", ["구분", "종목", "예상 범위", "주간 수익률"], rows,
               "에이전트 추천은 log.csv, 최종 선택은 final_log.csv에서 정산한다.")

    note = fin.get("note") or "(선택 사유 미기재 — 데스크 작업 패널의 최종 선택에서 적는다)"
    deck.bullets(f"{name} — 선택 사유", [note], "사용자가 최종 선택 단계에서 직접 적은 내용")

    png = out / f"chart_{ticker}.png"
    if not no_chart and not png.exists():
        chart_mod.render(ticker, name, m, sell or smeta.get("ref_date", ""), png, buy, sell)
    deck.image("6개월 일봉 · 기술적 지표", png if png.exists() else None, "음영 = 평가 구간")

    verdict = ([f"실제 {ret}%   벤치마크 {row.get('benchmark', '')} {bret}%  →  초과수익 {excess}%p"]
               if ret != "" else ["(아직 정산 전 — 작업 패널의 정산 실행)"])
    verdict += ["", "에이전트 추천을 따르지 않은 판단이 맞았는지 한 줄로 :", "  (직접 작성)"]
    deck.bullets("사후 검증", verdict, "")

    dst = out / f"deck_wk{week}_final_{ticker}.pptx"
    deck.save(dst)
    print(f"\n저장: {chart_mod.rel(dst)}  ({deck.n}슬라이드)")
    return dst


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--market", default="KR", choices=["KR", "US"])
    p.add_argument("--final", action="store_true", help="사용자 최종 선택 종목으로 만든다 (final.json)")
    p.add_argument("--full", action="store_true", help="발표하는 주차 — 내용을 더 담는다")
    p.add_argument("--no-chart", action="store_true", help="차트 생성을 건너뛴다")
    a = p.parse_args()
    if a.final:
        build_final(a.week, a.full, a.no_chart)
    else:
        build(a.week, a.market, a.full, a.no_chart)
    return 0


if __name__ == "__main__":
    sys.exit(main())
