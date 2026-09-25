"""계좌 화면 API — 한국투자증권 잔고·체결, 포트폴리오 요약, 수익 곡선, 종목 상세, 매수 초안.

증권사 API는 사용자가 '새로고침'을 누르거나, 오늘 조회 기록이 없을 때 화면을 열면 한 번 부른다
(조회 전용). 스냅샷은 private/account/ 에 쌓는다 (.gitignore — 계좌 정보는 저장소에 올리지 않는다).
주문 실행 엔드포인트는 아직 두지 않는다.

?demo=1 이면 지난 추천 종목으로 만든 예시 포트폴리오를 쓴다 (app/portfolio.py).
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import portfolio as pf

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


def _state(demo: bool) -> dict | None:
    if demo:
        return pf.demo_state()
    snaps = _snapshots()
    if not snaps:
        return None
    s = snaps[-1]
    s.setdefault("trades", [])
    s["demo"] = False
    return s


def _brief(sm: dict, state: dict) -> list[str]:
    """숫자만으로 쓰는 브리핑. LLM을 쓰지 않는다."""
    L = []
    if not sm["total"]:
        return L
    L.append(f"총자산 {sm['total']:,.0f}원, 그중 현금 {sm['cash'] / sm['total'] * 100:.1f}%.")
    if sm["day"]:
        L.append(f"오늘 {sm['day']['amount']:+,.0f}원 ({sm['day']['pct']:+.2f}%).")
    if sm["invested"]:
        L.append(f"보유 종목 평가손익 {sm['unrealized']:+,.0f}원 ({sm['unrealized_pct']:+.2f}%), 실현손익 {sm['realized']:+,.0f}원.")
    pos = sm["positions"]
    if pos:
        top = pos[0]
        L.append(f"최대 비중 {top['name']} {top['weight']:.1f}%."
                 + (" 한 종목이 40%를 넘는다 — 주간 추천 종목은 고변동성(필터 4)이라 과하다." if top["weight"] > 40 else ""))
        best, worst = max(pos, key=lambda p: p["pnl_pct"]), min(pos, key=lambda p: p["pnl_pct"])
        if best is not worst:
            L.append(f"수익률 최고 {best['name']} {best['pnl_pct']:+.2f}%, 최저 {worst['name']} {worst['pnl_pct']:+.2f}%.")
    ps = pf.picks()
    if ps:
        last = ps[-1]
        t = pf._tk(last["pick"]["ticker"])
        held = any(pf._tk(p["ticker"]) == t for p in pos)
        L.append(f"가장 최근 추천({last['week']}주차 {last['pick'].get('name')})은 {'보유 중' if held else '보유하지 않음'}.")
    return L


@router.get("/api/account")
def account(demo: bool = False):
    s = kis_settings()
    state = _state(demo)
    base = {"broker": "한국투자증권", "settings": s.public(), "demo": demo,
            "today_fetched": any(x.get("fetched_at", "")[:10] == date.today().isoformat() for x in _snapshots())}
    if not state:
        return {**base, "state": None}
    sm = pf.summarize(state)
    return {**base, "state": {"fetched_at": state["fetched_at"], "env": state.get("env"), "demo": state.get("demo")},
            "summary": sm, "brief": _brief(sm, state), "trades": state["trades"][-50:]}


@router.post("/api/account/refresh")
def refresh():
    try:
        b = load_broker()
        bal = b.balance().to_dict()
        end = date.today()
        try:
            bal["trades"] = b.executions((end - timedelta(days=89)).strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        except BrokerError as e:
            bal["trades"], bal["trades_error"] = [], str(e)
    except BrokerNotConfigured as e:
        raise HTTPException(412, str(e))
    except BrokerError as e:
        raise HTTPException(502, str(e))
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    with SNAP.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bal, ensure_ascii=False) + "\n")
    return account()


@router.get("/api/account/equity")
def equity(demo: bool = False, days: int = 90):
    state = _state(demo)
    if not state:
        return {"points": [], "note": "잔고를 한 번 조회해야 곡선을 그릴 수 있다."}
    try:
        return pf.equity_curve(state, days)
    except Exception as e:                              # 시세 소스 장애
        return {"points": [], "note": f"곡선 계산 실패: {e}"}


@router.get("/api/account/holding/{market}/{ticker}")
def holding(market: str, ticker: str, demo: bool = False):
    if market not in ("KR", "US"):
        raise HTTPException(400)
    state = _state(demo) or {"positions": [], "trades": []}
    try:
        return pf.holding_detail(state, market, ticker)
    except Exception as e:
        raise HTTPException(502, f"시세 조회 실패: {e}")


class PlanIn(BaseModel):
    week: int
    market: str
    budget: float
    order_type: str = "market"


@router.post("/api/account/plan")
def plan(p: PlanIn):
    """추천 종목의 매수 초안. 기준가는 스크리닝 기준일 종가다 (증권사 API 호출 없음)."""
    d = ROOT / "outputs" / f"wk{p.week}" / p.market
    pfile = d / "pick.json"
    if not pfile.exists():
        raise HTTPException(404, "이 주차의 추천이 아직 없다.")
    pick = json.loads(pfile.read_text(encoding="utf-8"))
    t = pf._tk(pick["ticker"])
    ref = None
    cf = d / "candidates.csv"
    if cf.exists():
        with cf.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if pf._tk(r["ticker"]) == t:
                    ref = float(r["close"])
    if not ref:
        raise HTTPException(404, "기준가(후보 CSV 종가)를 찾지 못했다.")
    qty = math.floor(p.budget / ref)
    op = OrderPlan(p.market, t, pick.get("name", ""), "buy", qty, p.order_type, None, ref, qty * ref,
                   "KRW" if p.market == "KR" else "USD",
                   note="월요일 시가에 체결되므로 실제 금액은 기준가와 다르다. 시가 갭이 크면 수량을 다시 계산할 것.")
    return {**op.to_dict(), "confirm_phrase": f"buy {t} {qty}",
            "order_enabled": kis_settings().order_enabled, "executable": False,
            "why_not": "주문 실행은 아직 연결하지 않았다 (모의투자 검증 후 추가)."}
