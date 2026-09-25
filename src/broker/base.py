"""증권사 공통 인터페이스. 한국투자증권이 막히면 같은 인터페이스로 토스 구현을 붙인다."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


class BrokerError(RuntimeError):
    pass


class BrokerNotConfigured(BrokerError):
    pass


@dataclass
class Position:
    market: str             # KR | US
    ticker: str
    name: str
    qty: float
    avg_price: float        # 매입 평균가 (현지 통화)
    price: float            # 현재가 (현지 통화)
    value: float            # 평가금액 (현지 통화)
    pnl: float              # 평가손익 (현지 통화)
    pnl_pct: float
    currency: str
    day_pct: float | None = None    # 전일 대비 등락률 (국내만 제공)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Balance:
    cash_krw: float                         # 예수금
    total_krw: float                        # 총평가금액 (증권사 산출값)
    pnl_krw: float | None                   # 평가손익 합계
    positions: list[Position] = field(default_factory=list)
    raw_summary: dict = field(default_factory=dict)
    fetched_at: str = ""
    env: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["positions"] = [p.to_dict() for p in self.positions]
        return d


@dataclass
class OrderPlan:
    """주문 초안. 실제 주문 전에 사람이 보고 승인하는 단위다."""
    market: str
    ticker: str
    name: str
    side: str               # buy | sell
    qty: int
    order_type: str         # market | limit
    limit_price: float | None
    ref_price: float        # 수량 계산에 쓴 기준가
    est_amount: float
    currency: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Broker(ABC):
    name: str = ""
    env: str = ""           # paper | real

    @abstractmethod
    def balance(self) -> Balance: ...

    @abstractmethod
    def place_order(self, plan: OrderPlan, *, confirm: str) -> dict:
        """confirm에는 plan 요약 문자열을 그대로 넘겨야 한다 (오조작 방지)."""
