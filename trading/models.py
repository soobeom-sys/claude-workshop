"""trading/ 모듈이 공유하는 불변 데이터 모델."""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from config import StrategyParams


class ExitReason(StrEnum):
    """청산 사유. str 하위 타입이라 DB에는 한글 값이 그대로 저장된다."""

    STOP_LOSS = "손절"
    TAKE_PROFIT = "익절"
    TRAILING_STOP = "트레일링"
    TIME_LIMIT = "시간초과"
    # 15:20 장 마감 강제 청산
    MARKET_CLOSE = "장마감"
    # 미리 걸어 둔 지정가 매도 주문이 전량 체결된 경우 (계좌 동기화로 감지)
    TARGET = "목표가"


@dataclass(frozen=True)
class ExitDecision:
    """Position Guard가 산출하는 청산 판단 결과."""

    exit_price: int
    exit_reason: ExitReason
    pnl_amount: int
    pnl_pct: float


@dataclass(frozen=True)
class SellOrder:
    """포지션에 걸어 둔 지정가 매도(목표가) 주문. 분할매도 시 포지션당 여러 개다."""

    order_no: str
    quantity: int
    target_price: int


@dataclass(frozen=True)
class Position:
    """진입 에이전트가 매수에 성공한 뒤 Position Guard가 추적하는 보유 포지션.

    high_water_mark는 트레일링 스탑 계산용 보유 중 최고가이며 진입 시점에는
    entry_price와 같다. 갱신은 dataclasses.replace()로 새 인스턴스를 만든다.

    quantity는 현재 남은 보유수량이다. 지정가 매도가 일부 체결되면 줄어들고,
    그만큼의 실현손익/수량은 realized_pnl/sold_quantity에 누적된다.
    """

    stock_code: str
    stock_name: str
    entry_price: int
    quantity: int
    entry_time: dt.datetime
    condition_name: str
    strategy: StrategyParams
    exchange: str
    order_no: str
    high_water_mark: int
    trade_id: str | None = None
    signal_id: str | None = None
    sell_orders: tuple[SellOrder, ...] = ()
    # 1차 트랜치만 매수된 상태로, 2차 트랜치 매수 조건을 기다리는 중인지
    pending_tranche2: bool = False
    # 2차 트랜치 매수 마감 시각(ISO 8601). 이 시각을 넘기면 1차만 유지한다.
    tranche2_deadline: str | None = None
    # 체결된 매수 트랜치 수 (1차만 = 1, 2차까지 = 2)
    tranche_count: int = 1
    realized_pnl: int = 0
    sold_quantity: int = 0
    # 마지막 매수 주문 시각. 직후에는 잔고 반영이 늦을 수 있어 계좌 동기화를 건너뛴다.
    last_buy_time: dt.datetime | None = None

    def with_updated_high(self, price: int) -> Position:
        """price가 기존 최고가보다 높으면 최고가를 갱신한 새 인스턴스를, 아니면 self를 반환한다."""
        if price <= self.high_water_mark:
            return self
        return dataclasses.replace(self, high_water_mark=price)
