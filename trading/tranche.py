"""분할매수(Split Buy): 1차 트랜치(기본 60%) 매수 후 3분 안에 2차 트랜치(나머지 40%)를 추가 매수한다.

- 1차: 진입 신호에서 TRADE_AMOUNT * TRANCHE1_BUY_RATIO 만큼 매수 → Quick/Main Target 지정가 매도 등록
       → apply_split_buy()로 2차 대기 상태(pending_tranche2, tranche2_deadline)를 켠다.
- 2차: Position Guard 사이클(5초)마다 try_tranche2_buy()가 풀백(-0.5% 이하) 또는
       모멘텀(+0.3% 이상)을 확인해 최유리지정가로 추가 매수하고, 평균단가를 재계산한 뒤
       2차 매수분에 대한 Quick/Main Target 매도 주문을 추가로 건다.
- TRANCHE2_TIMEOUT_SEC(기본 3분) 안에 조건이 충족되지 않으면 pending_tranche2=False로 1차만 유지한다.

1차 매수 주문 자체는 진입 판단과 함께 quant-trader 에이전트가 실행하며, 그 수량 기준이
tranche1_amount()/tranche1_quantity()다. 퍼센트 값은 -0.5 == -0.5% 단위로 다룬다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import config
from market import api
from trading.models import Position, SellOrder
from trading.order import place_target_orders

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 키움 주문 매매구분: 최유리지정가
_TRDE_TP_BEST_LIMIT = "6"


def _now() -> dt.datetime:
    return dt.datetime.now(_KST)


@dataclass(frozen=True)
class Tranche2Fill:
    """2차 트랜치 매수 결과. 알림/DB 기록용."""

    quantity: int
    price: int
    trigger: str
    order_no: str
    sell_orders: tuple[SellOrder, ...]


# ----------------------------------------------------------------------
# 수량 계산
# ----------------------------------------------------------------------
def tranche1_amount() -> int:
    """1차 매수 금액. 분할매수가 꺼져 있으면 TRADE_AMOUNT 전액을 한 번에 산다."""
    if config.SPLIT_TRADE_ENABLED:
        return int(config.TRADE_AMOUNT * config.TRANCHE1_BUY_RATIO)
    return config.TRADE_AMOUNT


def tranche1_quantity(current_price: int) -> int:
    """1차 매수 수량 = TRADE_AMOUNT * TRANCHE1_BUY_RATIO / 현재가 (내림)."""
    return tranche1_amount() // current_price if current_price > 0 else 0


def tranche2_quantity(current_price: int) -> int:
    """2차 매수 수량 = TRADE_AMOUNT * (1 - TRANCHE1_BUY_RATIO) / 현재가 (내림)."""
    if current_price <= 0:
        return 0
    return int(config.TRADE_AMOUNT * (1 - config.TRANCHE1_BUY_RATIO)) // current_price


def pnl_pct(position: Position, current_price: int) -> float:
    """평균단가 대비 현재가 수익률(%)."""
    return (current_price - position.entry_price) / position.entry_price * 100


# ----------------------------------------------------------------------
# 1차 매수 후 분할 설정
# ----------------------------------------------------------------------
def apply_split_buy(position: Position, now: dt.datetime | None = None) -> Position:
    """1차 매수 직후 호출해 2차 트랜치 대기 상태를 설정한 새 Position을 반환한다.

    SPLIT_TRADE_ENABLED가 꺼져 있으면 2차 대기 없이 그대로(tranche_count=1) 둔다.
    1차 수량은 이미 TRANCHE1_BUY_RATIO만큼만 매수된 상태다.
    """
    if not config.SPLIT_TRADE_ENABLED:
        return dataclasses.replace(position, pending_tranche2=False, tranche2_deadline=None, tranche_count=1)
    deadline = (now or _now()) + dt.timedelta(seconds=config.TRANCHE2_TIMEOUT_SEC)
    return dataclasses.replace(
        position,
        pending_tranche2=True,
        tranche2_deadline=deadline.isoformat(),
        tranche_count=1,
    )


def _deadline(position: Position) -> dt.datetime:
    if position.tranche2_deadline:
        return dt.datetime.fromisoformat(position.tranche2_deadline)
    # tranche2_deadline 필드가 생기기 전에 저장된 포지션은 진입 시각 기준으로 계산한다.
    return position.entry_time + dt.timedelta(seconds=config.TRANCHE2_TIMEOUT_SEC)


def _give_up(position: Position) -> Position:
    return dataclasses.replace(position, pending_tranche2=False)


# ----------------------------------------------------------------------
# 2차 매수
# ----------------------------------------------------------------------
async def try_tranche2_buy(
    position: Position,
    current_price: int,
    now: dt.datetime | None = None,
    persist: Callable[[Position], None] | None = None,
) -> tuple[Position, Tranche2Fill | None]:
    """2차 트랜치 매수를 시도한다. (갱신된 Position, 매수했으면 Tranche2Fill)을 반환한다.

    persist를 넘기면 매수 체결 직후(목표가 매도 주문을 걸기 전)의 Position을 먼저 저장한다.
    도중에 프로세스가 죽어도 재시작 시 늘어난 수량·평균단가를 이어받게 하기 위함이다.
    """
    if not position.pending_tranche2:
        return position, None

    now = now or _now()
    if now >= _deadline(position):
        logger.info("2차 트랜치 대기 시간 초과(%d초) → 1차만 유지: %s", config.TRANCHE2_TIMEOUT_SEC, position.stock_code)
        return _give_up(position), None

    change_pct = pnl_pct(position, current_price)
    if change_pct <= config.TRANCHE2_PULLBACK_PCT:
        trigger = "풀백"
    elif change_pct >= config.TRANCHE2_MOMENTUM_PCT:
        trigger = "모멘텀"
    else:
        return position, None

    # a. 2차 매수 수량
    qty2 = tranche2_quantity(current_price)
    if qty2 <= 0:
        logger.info("2차 트랜치 수량 0(현재가 %s원) → 1차만 유지: %s", f"{current_price:,}", position.stock_code)
        return _give_up(position), None

    # b. 최유리지정가 매수
    try:
        order_no = await asyncio.to_thread(api.place_buy_order, position.stock_code, qty2, _TRDE_TP_BEST_LIMIT)
    except Exception as exc:
        # 재시도하면 중복 매수 위험이 있으므로 한 번 실패하면 포기한다.
        logger.error("2차 트랜치 매수 실패 → 1차만 유지: %s %s", position.stock_code, exc)
        return _give_up(position), None

    # c. 평균단가 재계산
    new_qty = position.quantity + qty2
    new_entry = round((position.entry_price * position.quantity + current_price * qty2) / new_qty)
    logger.info(
        "2차 트랜치 매수(%s, %+.2f%%): %s %d주 @ ~%s원 (%s) → 총 %d주 평균 %s원",
        trigger, change_pct, position.stock_code, qty2, f"{current_price:,}", order_no, new_qty, f"{new_entry:,}",
    )

    # d. Position 업데이트
    updated = dataclasses.replace(
        position,
        quantity=new_qty,
        entry_price=new_entry,
        tranche_count=2,
        pending_tranche2=False,
        last_buy_time=_now(),
        high_water_mark=max(position.high_water_mark, current_price),
    )
    if persist is not None:
        persist(updated)

    # e. 2차 매수분에 대한 Quick Target + Main Target 매도 주문 (1차분 주문은 그대로 둔다).
    #    목표가는 재계산된 평균단가 기준이다.
    tranche2_orders = (await place_target_orders(dataclasses.replace(updated, quantity=qty2))).sell_orders
    updated = dataclasses.replace(updated, sell_orders=position.sell_orders + tranche2_orders)

    fill = Tranche2Fill(
        quantity=qty2, price=current_price, trigger=trigger, order_no=order_no, sell_orders=tranche2_orders,
    )
    return updated, fill
