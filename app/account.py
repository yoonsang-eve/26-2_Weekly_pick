"""계좌 화면 — 한국투자증권 잔고 조회, 스냅샷, 브리핑, 주문 초안.

잔고 조회는 사용자가 '새로고침'을 누를 때만 증권사 API를 부른다 (LLM 아님).
스냅샷은 private/account/ 에 쌓는다 (.gitignore — 계좌 정보는 저장소에 올리지 않는다).
주문 실행 엔드포인트는 아직 두지 않는다. 초안(plan)만 만든다.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "private" / "account" / "snapshots.jsonl"
sys.path.insert(0, str(ROOT / "src"))
from broker import BrokerError, BrokerNotConfigured, OrderPlan, kis_settings, load_broker  # noqa: E402

router = APIRouter()


def _snapshots() -> list[dict]:
    if not SNAP.exists():
        return []
    out = []
    for line in SNAP.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _latest_pick(market: str = "KR") -> dict | None:
    wks = sorted((int(p.name[2:]), p) for p in (ROOT / "outputs").glob("wk*") if p.name[2:].isdigit())
    for _, p in reversed(wks):
        f = p / market / "pick.json"
        if f.exists():
            return {"week": int(p.name[2:]), **json.loads(f.read_text(encoding="utf-8"))}
    return None


def briefing(bal: dict, prev: dict | None) -> dict:
    """숫자만으로 만드는 브리핑. LLM을 쓰지 않는다."""
    pos = bal["positions"]
    kr = [p for p in pos if p["currency"] == "KRW"]
    total = bal["total_krw"] or (sum(p["value"] for p in kr) + bal["cash_krw"])
    weights = sorted(({"ticker": p["ticker"], "name": p["name"], "weight": p["value"] / total * 100,
                       "pnl_pct": p["pnl_pct"]} for p in kr if total), key=lambda x: -x["weight"])
    lines = []
    if total:
        lines.append(f"국내 계좌 평가금액 {total:,.0f}원, 현금 비중 {bal['cash_krw'] / total * 100:.1f}%.")
    if prev and prev.get("total_krw"):
        ch = total - prev["total_krw"]
        lines.append(f"직전 조회({prev['fetched_at'][:16].replace('T', ' ')}) 대비 {ch:+,.0f}원 "
                     f"({ch / prev['total_krw'] * 100:+.2f}%).")
    if weights:
        top = weights[0]
        lines.append(f"최대 비중 {top['name']} {top['weight']:.1f}%.")
        if top["weight"] > 40:
            lines.append("한 종목 비중이 40%를 넘는다 — 주간 추천 전략의 변동성(필터 4)을 감안하면 과하다.")
    best = max(pos, key=lambda p: p["pnl_pct"], default=None)
    worst = min(pos, key=lambda p: p["pnl_pct"], default=None)
    if best and worst and best is not worst:
        lines.append(f"수익률 최고 {best['name']} {best['pnl_pct']:+.2f}%, 최저 {worst['name']} {worst['pnl_pct']:+.2f}%.")
    us = [p for p in pos if p["currency"] == "USD"]
    if us:
        lines.append(f"해외 보유 {len(us)}종목 평가 ${sum(p['value'] for p in us):,.2f} (원화 비중에는 미포함).")
    pick = _latest_pick("KR")
    if pick:
        held = any(p["ticker"] == str(pick["ticker"]) for p in pos)
        lines.append(f"wk{pick['week']} 추천 {pick['name']}({pick['ticker']})는 "
                     f"{'보유 중' if held else '보유하지 않음'}.")
    return {"total_krw": total, "weights": weights, "lines": lines}


@router.get("/api/account")
def account():
    s = kis_settings()
    snaps = _snapshots()
    last = snaps[-1] if snaps else None
    return {"broker": "한국투자증권", "settings": s.public(), "last": last,
            "briefing": briefing(last, snaps[-2] if len(snaps) > 1 else None) if last else None,
            "history": [{"t": x["fetched_at"], "total": x["total_krw"]} for x in snaps[-120:]]}


@router.post("/api/account/refresh")
def refresh():
    try:
        b = load_broker()
        bal = b.balance().to_dict()
    except BrokerNotConfigured as e:
        raise HTTPException(412, str(e))
    except BrokerError as e:
        raise HTTPException(502, str(e))
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    with SNAP.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bal, ensure_ascii=False) + "\n")
    return account()


class PlanIn(BaseModel):
    week: int
    market: str
    budget: float
    order_type: str = "market"


@router.post("/api/account/plan")
def plan(p: PlanIn):
    """추천 종목의 매수 초안. 기준가는 스크리닝 기준일 종가다 (증권사 API 호출 없음)."""
    d = ROOT / "outputs" / f"wk{p.week}" / p.market
    pf = d / "pick.json"
    if not pf.exists():
        raise HTTPException(404, "이 주차의 추천이 아직 없다.")
    pick = json.loads(pf.read_text(encoding="utf-8"))
    t = str(pick["ticker"]).zfill(6) if str(pick["ticker"]).isdigit() else str(pick["ticker"])
    ref = None
    cf = d / "candidates.csv"
    if cf.exists():
        with cf.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rt = str(r["ticker"]).zfill(6) if str(r["ticker"]).isdigit() else str(r["ticker"])
                if rt == t:
                    ref = float(r["close"])
    if not ref:
        raise HTTPException(404, "기준가(후보 CSV 종가)를 찾지 못했다.")
    qty = math.floor(p.budget / ref)
    op = OrderPlan(p.market, t, pick.get("name", ""), "buy", qty, p.order_type, None, ref, qty * ref,
                   "KRW" if p.market == "KR" else "USD",
                   note="월요일 시가에 체결되므로 실제 금액은 기준가와 다르다. 시가 갭이 크면 수량을 다시 계산할 것.")
    return {**op.to_dict(), "confirm_phrase": f"buy {t} {qty}",
            "order_enabled": kis_settings().order_enabled,
            "executable": False,
            "why_not": "주문 실행은 아직 연결하지 않았다 (모의투자 검증 후 추가)."}
