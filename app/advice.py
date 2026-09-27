"""최종 선택 조언 — 한국 추천과 미국 추천 중 어느 쪽을 낼지 규칙으로 비교한다.

최종 선택은 사용자가 한다. 이 모듈은 판단 근거를 나란히 보여 주고 다수결로 한쪽을 권할 뿐이다.
LLM을 부르지 않는다 (허락 없는 API 사용 금지, 그리고 시장 간 비교를 파이프라인 프롬프트에
넣지 않는다는 CLAUDE.md 2절 설계와도 맞춘다). 이미 있는 파일만 읽는다:
pick.json(Trader 예측 범위·촉매), candidates.csv(ATR), screen_meta.json(보유 영업일).

기준은 과제 구조(주간 상대 순위, 손실 이월 없음 = 토너먼트)에서 나온다 — CLAUDE.md 1절.
  1. 상단 기대수익  Trader 예측 범위의 상단. 순위 경쟁이라 평균보다 윗꼬리가 중요하다
  2. 기간 변동성    ATR20% × √보유일. 휴장으로 보유일이 짧으면 움직일 여지도 줄어든다
  3. 촉매 확정성    평가 구간 안의 날짜가 촉매에 적혀 있고 '없음/미정' 류 표현이 없는가
하단(예상 손실)은 참고로만 보인다. 손실이 다음 주로 이월되지 않아 선택 기준이 아니다.
"""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import date
from pathlib import Path

NEG = re.compile(r"없음|없다|미정|비확정|미확정|불확실|확인 불가|특정되지|특정 불가")
MK = {"KR": "한국", "US": "미국"}


def _json(f: Path) -> dict | None:
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _atr(d: Path, ticker: str) -> float | None:
    f = d / "candidates.csv"
    if not f.exists():
        return None
    with f.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            t = str(r["ticker"]).strip()
            t = t.zfill(6) if t.isdigit() else t
            if t == ticker:
                try:
                    return float(r["atr20_pct"])
                except (TypeError, ValueError):
                    return None
    return None


def _dates_in(text: str, year: int) -> list[date]:
    out = []
    for y, m, d in re.findall(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        out.append((int(y), int(m), int(d)))
    for m, d in re.findall(r"(?<![\d-])(\d{1,2})/(\d{1,2})(?![\d/])", text):
        out.append((year, int(m), int(d)))
    for m, d in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", text):
        out.append((year, int(m), int(d)))
    res = []
    for y, m, d in out:
        try:
            res.append(date(y, m, d))
        except ValueError:
            pass
    return res


def _catalyst(text: str, buy: str, sell: str) -> tuple[bool, str]:
    b, s = date.fromisoformat(buy), date.fromisoformat(sell)
    inside = [x for x in _dates_in(text or "", b.year) if b <= x <= s]
    if NEG.search(text or ""):
        return False, "촉매에 '없음/미정' 류 표현이 있다"
    if not inside:
        return False, "평가 구간 안의 날짜가 적혀 있지 않다"
    return True, f"{inside[0].month}/{inside[0].day} 일정이 평가 구간 안에 있다"


def side(out: Path, market: str) -> dict | None:
    d = out / market
    p = _json(d / "pick.json")
    if not p:
        return None
    meta = _json(d / "screen_meta.json") or {}
    if not meta.get("buy_date"):
        # 초기 주차(wk3·wk4)의 screen_meta에는 매매일이 없다. 정산 기록에서 가져온다
        log = out.parent.parent / "log.csv"
        if log.exists():
            with log.open(encoding="utf-8-sig") as fh:
                row = next((r for r in csv.DictReader(fh)
                            if r["week"] == out.name[2:] and r["market"] == market), None)
            if row:
                meta = {**meta, "buy_date": row["buy_date"], "sell_date": row["sell_date"],
                        "holding_days": row["holding_days"]}
    t = str(p["ticker"])
    lo, hi = (p.get("expected_return_range") or [None, None])[:2]
    atr = _atr(d, t)
    days = int(meta.get("holding_days") or 5)
    ok, why = _catalyst(p.get("catalyst", ""), meta.get("buy_date", "1900-01-01"), meta.get("sell_date", "2999-12-31")) \
        if meta.get("buy_date") else (False, "매매일 기록 없음")
    return {"market": market, "ticker": t, "name": p.get("name", ""), "lo": lo, "hi": hi,
            "atr": atr, "days": days, "vol": atr * math.sqrt(days) if atr is not None else None,
            "catalyst_ok": ok, "catalyst_why": why, "catalyst": p.get("catalyst", "")}


def advise(out: Path) -> dict | None:
    """out = outputs/wk{N}. 두 시장 추천이 다 있어야 비교한다."""
    kr, us = side(out, "KR"), side(out, "US")
    if not kr and not us:
        return None
    if not (kr and us):
        have = kr or us
        return {"ready": False, "sides": [x for x in (kr, us) if x],
                "summary": f"{MK[have['market']]} 추천만 나왔다. {MK['US' if kr else 'KR']} 추천이 저장되면 비교한다."}

    def cmp(a, b, higher=True):
        if a is None or b is None or a == b:
            return None
        return "KR" if (a > b) == higher else "US"

    crit = [
        {"key": "hi", "label": "상단 기대수익", "KR": _fmt_pct(kr["hi"]), "US": _fmt_pct(us["hi"]),
         "win": cmp(_f(kr["hi"]), _f(us["hi"])),
         "why": "Trader 예측 범위의 위쪽 끝. 순위 경쟁이라 윗꼬리가 크게 열린 쪽이 유리하다"},
        {"key": "vol", "label": "예상 변동폭", "KR": _fmt_band(kr["vol"]), "US": _fmt_band(us["vol"]),
         "win": cmp(kr["vol"], us["vol"]),
         "why": f"하루 평균 변동(ATR20) × √보유일. 한국 {kr['days']}일 · 미국 {us['days']}일 동안 움직일 수 있는 폭"},
        {"key": "cat", "label": "촉매 확정성", "KR": "확정" if kr["catalyst_ok"] else "불확실",
         "US": "확정" if us["catalyst_ok"] else "불확실",
         "win": None if kr["catalyst_ok"] == us["catalyst_ok"] else ("KR" if kr["catalyst_ok"] else "US"),
         "why": f"한국: {kr['catalyst_why']} / 미국: {us['catalyst_why']}"},
    ]
    # 판정에 쓰지 않는 참고 행
    info = [
        {"label": "예측 범위", "KR": _fmt_range(kr), "US": _fmt_range(us)},
        {"label": "하루 변동성 (ATR20)", "KR": _fmt_pct(_r(kr["atr"]), sign=False), "US": _fmt_pct(_r(us["atr"]), sign=False)},
        {"label": "보유 영업일", "KR": f"{kr['days']}일", "US": f"{us['days']}일"},
    ]
    score = {m: sum(c["win"] == m for c in crit) for m in ("KR", "US")}
    if score["KR"] == score["US"]:
        pick, summary = None, f"기준 3개에서 {score['KR']}:{score['US']} 동률이다. 촉매 내용을 직접 읽고 고른다."
    else:
        pick = "KR" if score["KR"] > score["US"] else "US"
        s = kr if pick == "KR" else us
        summary = f"{MK[pick]} {s['name']}({s['ticker']}) — 기준 3개 중 {score[pick]}개에서 앞선다"
    return {"ready": True, "recommend": pick, "score": score, "criteria": crit, "info": info,
            "sides": [kr, us], "summary": summary, "explain": _explain(kr, us, crit, pick),
            "note": "하단(예상 손실)은 판정에 넣지 않는다. 손실이 다음 주로 이월되지 않아 순위 경쟁에서는 윗꼬리가 더 중요하다."}


def _explain(kr: dict, us: dict, crit: list[dict], pick: str | None) -> str:
    """판정을 문장으로. 숫자는 위 표와 같은 값만 쓴다."""
    by = {c["key"]: c for c in crit}
    n = {"KR": f"{kr['name']}", "US": f"{us['name']}"}
    out = []
    c = by["cat"]
    if c["win"] is None:
        out.append("두 종목 모두 촉매가 " + ("평가 구간 안의 날짜로 특정돼 있어" if kr["catalyst_ok"] else "날짜로 특정되지 않아") + " 이 기준은 동률이다.")
    else:
        out.append(f"촉매는 {n[c['win']]} 쪽만 평가 구간 안의 날짜로 특정돼 있다.")
    h = by["hi"]
    if h["win"]:
        out.append(f"예측 상단은 {n['KR']} {h['KR']}, {n['US']} {h['US']}로 {n[h['win']]} 쪽이 높다.")
    v = by["vol"]
    if v["win"] and kr["vol"] and us["vol"]:
        big, small = (kr, us) if v["win"] == "KR" else (us, kr)
        ratio = big["vol"] / small["vol"]
        out.append(f"보유 기간 예상 변동폭은 {n[v['win']]} {_fmt_band(big['vol'])}로 상대의 약 {ratio:.1f}배라 "
                   f"상위권(크게 오르는 쪽)에 들 여지가 더 크다.")
    if pick is None:
        out.append("기준이 갈려 규칙으로는 정하지 않는다.")
    return " ".join(out)


def _fmt_pct(v, sign=True):
    v = _f(v)
    if v is None:
        return None
    return f"{v:+g}%" if sign else f"{v:g}%"


def _fmt_band(v):
    return None if v is None else f"±{v * 100:.1f}%"


def _fmt_range(sd: dict):
    lo, hi = _f(sd["lo"]), _f(sd["hi"])
    return None if lo is None or hi is None else f"{lo:+g}% ~ {hi:+g}%"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r(v):
    return None if v is None else round(v * 100, 1)
