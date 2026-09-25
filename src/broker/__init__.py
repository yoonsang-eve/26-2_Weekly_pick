"""증권사 계좌 연동 계층.

파이프라인(screener·agents·settle)은 이 패키지를 import하지 않는다. 과제 채점은 가격
기준이라 계좌와 무관하고, 계좌 연동은 로컬 대시보드(app/)에서만 쓴다.

    from broker import load_broker
    b = load_broker()          # .env의 KIS_* 값으로 만든다. 없으면 BrokerNotConfigured
    b.balance()
"""
from .base import (Balance, Broker, BrokerError, BrokerNotConfigured, OrderPlan,  # noqa: F401
                   Position)
from .kis import KISBroker, kis_settings  # noqa: F401


def load_broker() -> Broker:
    s = kis_settings()
    if not s.configured:
        raise BrokerNotConfigured(
            "한국투자증권 API 설정이 비어 있다. .env에 KIS_APP_KEY, KIS_APP_SECRET, "
            "KIS_ACCOUNT_NO를 채울 것.")
    return KISBroker(s)
