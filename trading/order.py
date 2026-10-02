"""매수 체결 직후 목표가 지정가 매도 주문을 등록한다.

분할매도가 켜져 있으면 Quick Target(기본 40% @ +1.5%)과 Main Target(나머지 @ 전략 목표가,
기본 +2.5%) 두 건을, 꺼져 있으면 전량을 Main Target 한 건으로 건다. 등록된 주문번호는
Position.sell_orders에 담아 반환한다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math

import config
from market import api
from trading.models import Position, SellOrder

logger = logging.getLogger(__name__)

_SELL_ORDER_RETRIES = 3
_SELL_ORDER_RETRY_DELAY_SEC = 1.0


def tick_price(price: float) -> int:
    """가격을 KRX 호가 단위에 맞게 반올림한다 (단위의 절반 이상이면 올림).

    가격대 경계값(2,000/5,000/20,000/...)은 윗 구간 호가 단위의 배수이므로
    아래 구간에서 반올림해 경계로 올라가도 유효한 호가가 된다.
    """
    # entry * 1.015 같은 부동소수 오차(예: 9994.9999999)로 반올림 방향이 뒤집히지 않게 한다.
    price = round(price, 6)
    tick = api.tick_size(int(price))
    return math.floor(price / tick + 0.5) * tick


def plan_target_orders(position: Position) -> list[tuple[int, int]]:
    """[(수량, 지정가)]. 수량은 내림하며, Quick Target에서 내림으로 남는 주식은 Main Target에 합친다."""
    qty = position.quantity
    main_price = tick_price(position.entry_price * (1 + position.strategy.target_pct / 100))
    if not config.SPLIT_TRADE_ENABLED:
        return [(qty, main_price)]

    quick_qty = math.floor(qty * config.QUICK_TARGET_SELL_RATIO)
    if quick_qty <= 0 or quick_qty >= qty:
        return [(qty, main_price)]
    quick_price = tick_price(position.entry_price * (1 + config.QUICK_TARGET_PCT / 100))
    return [(quick_qty, quick_price), (qty - quick_qty, main_price)]


async def place_target_orders(position: Position) -> Position:
    """목표가 지정가 매도 주문을 등록하고, 주문번호를 sell_orders에 담은 새 Position을 반환한다.

    주문이 끝내 실패한 건은 sell_orders에서 빠지며, 해당 물량은 Position Guard가
    손절/시간/트레일링으로 계속 감시한다.
    """
    placed: list[SellOrder] = []
    for qty, price in plan_target_orders(position):
        for attempt in range(1, _SELL_ORDER_RETRIES + 1):
            try:
                order_no = await asyncio.to_thread(api.place_sell_order, position.stock_code, qty, price, "0")
                placed.append(SellOrder(order_no=order_no, quantity=qty, target_price=price))
                logger.info("목표가 매도 주문: %s %d주 @ %s원 (%s)", position.stock_code, qty, f"{price:,}", order_no)
                break
            except Exception as exc:
                # 매수 직후엔 잔고 미반영으로 거부될 수 있어 잠시 뒤 재시도한다.
                logger.warning("목표가 매도 주문 실패 %d/%d %s: %s", attempt, _SELL_ORDER_RETRIES, position.stock_code, exc)
                await asyncio.sleep(_SELL_ORDER_RETRY_DELAY_SEC)
        else:
            logger.error(
                "목표가 매도 주문 등록 실패 — 손절/시간/트레일링은 Guard가 계속 감시: %s %d주 @ %s원",
                position.stock_code, qty, f"{price:,}",
            )
    return dataclasses.replace(position, sell_orders=tuple(placed))
