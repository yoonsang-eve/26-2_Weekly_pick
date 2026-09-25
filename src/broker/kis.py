"""한국투자증권 Open API (KIS Developers).

스펙 출처: github.com/koreainvestment/open-trading-api (examples_llm, examples_user/kis_auth.py)
  - REST 실전  https://openapi.koreainvestment.com:9443
  - REST 모의  https://openapivts.koreainvestment.com:29443
  - 토큰       POST /oauth2/tokenP  {grant_type: client_credentials, appkey, appsecret}. 유효 1일
  - 모의투자 tr_id는 실전 tr_id의 첫 글자(T/J/C)를 V로 바꾼 것
  - 국내 잔고  GET  /uapi/domestic-stock/v1/trading/inquire-balance   TTTC8434R
  - 국내 주문  POST /uapi/domestic-stock/v1/trading/order-cash        매수 TTTC0012U / 매도 TTTC0011U
  - 해외 잔고  GET  /uapi/overseas-stock/v1/trading/inquire-balance   TTTS3012R
  - 국내 체결  GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld TTTC0081R (3개월 이내, 모의 15건/회)
  - 호출 간격  실전 0.05초, 모의 0.5초

설정은 전부 .env에서 읽는다 (.gitignore 대상). 값을 코드나 config.yaml에 적지 말 것.

    KIS_APP_KEY=
    KIS_APP_SECRET=
    KIS_ACCOUNT_NO=12345678-01      # 종합계좌번호 8자리 - 상품코드 2자리
    KIS_ENV=paper                   # paper(모의투자) | real(실전)
    KIS_ORDER_ENABLED=false         # true가 아니면 주문 함수가 무조건 거부한다

주문은 안전장치를 겹겹이 둔다: KIS_ORDER_ENABLED=true + confirm 문자열 일치 + 국내만 지원.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .base import Balance, Broker, BrokerError, OrderPlan, Position

ROOT = Path(__file__).resolve().parents[2]
TOKEN_DIR = ROOT / "cache"                      # .gitignore 대상
URLS = {"real": "https://openapi.koreainvestment.com:9443",
        "paper": "https://openapivts.koreainvestment.com:29443"}
PACE = {"real": 0.05, "paper": 0.5}


def _load_env() -> None:
    """src/llm.py와 같은 방식의 최소 .env 로더 (python-dotenv 의존 없음)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.split("#", 1)[0].strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class KISSettings:
    app_key: str
    app_secret: str
    account_no: str
    env: str
    order_enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no)

    @property
    def cano(self) -> str:
        return self.account_no.replace("-", "")[:8]

    @property
    def prdt(self) -> str:
        return self.account_no.replace("-", "")[8:10] or "01"

    def public(self) -> dict:
        """화면에 보여 줄 값. 키는 가린다."""
        mask = lambda s: (s[:4] + "…" + s[-2:]) if len(s) > 8 else ("설정됨" if s else "")  # noqa: E731
        return {"configured": self.configured, "env": self.env, "order_enabled": self.order_enabled,
                "app_key": mask(self.app_key), "app_secret": "설정됨" if self.app_secret else "",
                "account_no": (self.cano[:4] + "****-" + self.prdt) if self.account_no else ""}


def kis_settings() -> KISSettings:
    _load_env()
    env = os.environ.get("KIS_ENV", "paper").strip().lower()
    if env not in URLS:
        env = "paper"
    return KISSettings(
        app_key=os.environ.get("KIS_APP_KEY", "").strip(),
        app_secret=os.environ.get("KIS_APP_SECRET", "").strip(),
        account_no=os.environ.get("KIS_ACCOUNT_NO", "").strip(),
        env=env,
        order_enabled=os.environ.get("KIS_ORDER_ENABLED", "false").strip().lower() == "true",
    )


def _f(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


class KISBroker(Broker):
    name = "한국투자증권"

    def __init__(self, s: KISSettings):
        self.s = s
        self.env = s.env
        self.base = URLS[s.env]
        self._last = 0.0

    # ------------------------------------------------------------ 인증
    def _token(self) -> str:
        """토큰은 하루 유효하고 발급 횟수 제한이 있어 파일에 캐시한다."""
        f = TOKEN_DIR / f"kis_token_{self.env}.json"
        if f.exists():
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if (t.get("app_key") == self.s.app_key
                        and datetime.fromisoformat(t["expires"]) > datetime.now() + timedelta(minutes=10)):
                    return t["token"]
            except (ValueError, KeyError):
                pass
        r = requests.post(f"{self.base}/oauth2/tokenP", timeout=15, json={
            "grant_type": "client_credentials", "appkey": self.s.app_key, "appsecret": self.s.app_secret})
        if r.status_code != 200:
            raise BrokerError(f"토큰 발급 실패 ({r.status_code}): {r.text[:200]}")
        d = r.json()
        tok = d.get("access_token")
        if not tok:
            raise BrokerError(f"토큰 응답에 access_token이 없다: {str(d)[:200]}")
        exp = datetime.now() + timedelta(seconds=int(d.get("expires_in") or 86400))
        TOKEN_DIR.mkdir(exist_ok=True)
        f.write_text(json.dumps({"token": tok, "expires": exp.isoformat(), "app_key": self.s.app_key}),
                     encoding="utf-8")
        return tok

    def _tr(self, tr_id: str) -> str:
        return ("V" + tr_id[1:]) if self.env == "paper" and tr_id[0] in "TJC" else tr_id

    def _call(self, method: str, path: str, tr_id: str, *, params=None, body=None,
              tr_cont: str = "") -> tuple[dict, str]:
        wait = PACE[self.env] - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        headers = {"content-type": "application/json; charset=utf-8",
                   "authorization": f"Bearer {self._token()}",
                   "appkey": self.s.app_key, "appsecret": self.s.app_secret,
                   "tr_id": self._tr(tr_id), "custtype": "P", "tr_cont": tr_cont}
        r = requests.request(method, self.base + path, headers=headers, params=params,
                             json=body, timeout=15)
        self._last = time.time()
        try:
            d = r.json()
        except ValueError:
            raise BrokerError(f"{path} 응답 파싱 실패 ({r.status_code}): {r.text[:200]}")
        if r.status_code != 200 or str(d.get("rt_cd", "0")) != "0":
            raise BrokerError(f"{path} 실패: [{d.get('msg_cd', r.status_code)}] {d.get('msg1', r.text[:200])}")
        return d, r.headers.get("tr_cont", "")

    # ------------------------------------------------------------ 조회
    def _domestic(self) -> tuple[list[Position], dict]:
        params = {"CANO": self.s.cano, "ACNT_PRDT_CD": self.s.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                  "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                  "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                  "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        rows, summary, cont = [], {}, ""
        for _ in range(10):                             # 연속조회 (20종목 단위)
            d, nxt = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                                "TTTC8434R", params=params, tr_cont=cont)
            rows += d.get("output1") or []
            summary = (d.get("output2") or [{}])[0]
            if nxt not in ("F", "M"):
                break
            cont = "N"
            params["CTX_AREA_FK100"] = d.get("ctx_area_fk100", "")
            params["CTX_AREA_NK100"] = d.get("ctx_area_nk100", "")
        pos = [Position("KR", r.get("pdno", ""), r.get("prdt_name", ""), _f(r.get("hldg_qty")),
                        _f(r.get("pchs_avg_pric")), _f(r.get("prpr")), _f(r.get("evlu_amt")),
                        _f(r.get("evlu_pfls_amt")), _f(r.get("evlu_pfls_rt")), "KRW",
                        _f(r.get("fltt_rt")) if r.get("fltt_rt") not in (None, "") else None)
               for r in rows if _f(r.get("hldg_qty")) > 0]
        return pos, summary

    def _overseas(self) -> list[Position]:
        out: dict[str, Position] = {}
        for ex in ("NASD", "NYSE", "AMEX"):
            try:
                d, _ = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R",
                                  params={"CANO": self.s.cano, "ACNT_PRDT_CD": self.s.prdt,
                                          "OVRS_EXCG_CD": ex, "TR_CRCY_CD": "USD",
                                          "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
            except BrokerError:
                continue                                 # 해외 계좌 미개설 등
            for r in d.get("output1") or []:
                if _f(r.get("ovrs_cblc_qty")) <= 0:
                    continue
                t = r.get("ovrs_pdno", "")
                out[t] = Position("US", t, r.get("ovrs_item_name", ""), _f(r.get("ovrs_cblc_qty")),
                                  _f(r.get("pchs_avg_pric")), _f(r.get("now_pric2")),
                                  _f(r.get("ovrs_stck_evlu_amt")), _f(r.get("frcr_evlu_pfls_amt")),
                                  _f(r.get("evlu_pfls_rt")), "USD")
        return list(out.values())

    def balance(self) -> Balance:
        pos, s = self._domestic()
        pos += self._overseas()
        return Balance(
            cash_krw=_f(s.get("dnca_tot_amt")),
            total_krw=_f(s.get("tot_evlu_amt")),
            pnl_krw=_f(s.get("evlu_pfls_smtl_amt")) if s.get("evlu_pfls_smtl_amt") is not None else None,
            positions=pos, raw_summary=s,
            fetched_at=datetime.now().isoformat(timespec="seconds"), env=self.env)

    def executions(self, start: str, end: str) -> list[dict]:
        """국내 체결 내역 (3개월 이내). start/end는 YYYYMMDD."""
        params = {"CANO": self.s.cano, "ACNT_PRDT_CD": self.s.prdt, "INQR_STRT_DT": start, "INQR_END_DT": end,
                  "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "01", "PDNO": "", "CCLD_DVSN": "01",
                  "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
                  "EXCG_ID_DVSN_CD": "KRX", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        rows, cont = [], ""
        for _ in range(30):                              # 모의투자는 15건 단위
            d, nxt = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                                "TTTC0081R", params=params, tr_cont=cont)
            rows += d.get("output1") or []
            if nxt not in ("F", "M"):
                break
            cont = "N"
            params["CTX_AREA_FK100"] = d.get("ctx_area_fk100", "")
            params["CTX_AREA_NK100"] = d.get("ctx_area_nk100", "")
        out = []
        for r in rows:
            q = _f(r.get("tot_ccld_qty"))
            if q <= 0:
                continue
            dt = r.get("ord_dt", "")
            out.append({"date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}", "time": r.get("ord_tmd", ""),
                        "side": "sell" if r.get("sll_buy_dvsn_cd") == "01" else "buy",
                        "market": "KR", "ticker": r.get("pdno", ""), "name": r.get("prdt_name", ""),
                        "qty": q, "price": _f(r.get("avg_prvs")), "amount": _f(r.get("tot_ccld_amt"))})
        return sorted(out, key=lambda t: (t["date"], t["time"]))

    # ------------------------------------------------------------ 주문
    def place_order(self, plan: OrderPlan, *, confirm: str) -> dict:
        if not self.s.order_enabled:
            raise BrokerError("주문이 비활성화되어 있다 (.env KIS_ORDER_ENABLED=true가 아님).")
        expect = f"{plan.side} {plan.ticker} {plan.qty}"
        if confirm != expect:
            raise BrokerError(f"확인 문자열 불일치. '{expect}'를 그대로 입력해야 한다.")
        if plan.market != "KR":
            raise BrokerError("해외 주문은 아직 구현하지 않았다.")
        if plan.qty <= 0:
            raise BrokerError("수량이 0이다.")
        body = {"CANO": self.s.cano, "ACNT_PRDT_CD": self.s.prdt, "PDNO": plan.ticker,
                "ORD_DVSN": "01" if plan.order_type == "market" else "00",      # 01 시장가, 00 지정가
                "ORD_QTY": str(int(plan.qty)),
                "ORD_UNPR": "0" if plan.order_type == "market" else str(int(plan.limit_price or 0)),
                "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if plan.side == "sell" else "", "CNDT_PRIC": ""}
        d, _ = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash",
                          "TTTC0012U" if plan.side == "buy" else "TTTC0011U", body=body)
        return {"ok": True, "msg": d.get("msg1", ""), "output": d.get("output", {})}
