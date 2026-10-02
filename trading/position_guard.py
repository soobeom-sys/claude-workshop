"""Position Guard: 보유 포지션의 청산(exit)을 코드 기반으로 판단·실행한다.

- check_exit(): 손절/시간 제한/트레일링 스탑 여부를 판단하는 순수 함수 (API·DB·시계 의존 없음)
- PositionGuard: GUARD_INTERVAL_SEC(기본 5초)마다 보유 포지션을 순회하며
  계좌 동기화 → 청산 조건 체크 → 안전 청산(지정가 취소 → 잔고 확인 → 시장가 매도)을 수행하고,
  15:20에는 남은 포지션을 전량 시장가로 강제 청산한다.

전략 파라미터의 비율은 config.StrategyParams와 같이 퍼센트 단위(-1.5 == -1.5%)로 다룬다.
AI(Claude Code CLI)는 청산 판단에 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import config
from market import api
from trading import position_store
from trading.models import ExitDecision, ExitReason, Position, SellOrder

if TYPE_CHECKING:
    from db import TradingDB
    from notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

MARKET_OPEN = dt.time(9, 0)
FORCE_CLOSE_TIME = dt.time(15, 20)
MARKET_CLOSE = dt.time(15, 30)

_HEARTBEAT_EVERY_CYCLES = 12  # 5초 × 12 = 60초
# 매수 직후에는 잔고 반영이 늦을 수 있어 이 시간 동안은 계좌 동기화 대상에서 뺀다.
# (반영 전 잔고 0을 '목표가 전량 체결'로 오인하지 않기 위함)
_SYNC_GRACE_SEC = 30
# 가드 체크가 연속으로 이 횟수만큼 실패하면 Slack으로 알린다 (5초 주기라 매번 알리면 폭주).
_GUARD_NOTIFY_AFTER_FAILURES = 3


def _now() -> dt.datetime:
    return dt.datetime.now(_KST)


def _orders_value(orders: Iterable[SellOrder]) -> tuple[int, int]:
    """(총수량, 총금액)."""
    orders = list(orders)
    return sum(o.quantity for o in orders), sum(o.quantity * o.target_price for o in orders)


def check_exit(
    position: Position,
    current_price: int,
    now: dt.datetime | None = None,
) -> ExitDecision | None:
    """보유 포지션의 청산 여부를 판단하는 순수 함수.

    손절 → 시간 제한 → 트레일링 스탑 순으로 검사하여 처음 해당하는 규칙의
    ExitDecision을 반환하고, 모두 해당하지 않으면 None을 반환한다.
    트레일링 스탑은 보유 중 최고가(high_water_mark)의 수익률이 활성화 기준에 한 번이라도
    닿으면 켜지고, 이후 최고가 대비 하락폭이 기준 이상이면 청산한다.
    now를 생략하면 entry_time과 같은 타임존의 현재 시각을 사용한다.
    """
    strategy = position.strategy
    if now is None:
        now = dt.datetime.now(position.entry_time.tzinfo)

    pnl_pct = (current_price - position.entry_price) / position.entry_price * 100

    reason: ExitReason | None = None
    if pnl_pct <= strategy.stop_pct:
        reason = ExitReason.STOP_LOSS
    elif now - position.entry_time >= dt.timedelta(minutes=strategy.max_hold_min):
        reason = ExitReason.TIME_LIMIT
    elif strategy.trailing_activate_pct is not None and strategy.trailing_drop_pct is not None:
        hwm = position.high_water_mark
        hwm_pct = (hwm - position.entry_price) / position.entry_price * 100
        drop_pct = (hwm - current_price) / hwm * 100
        if hwm_pct >= strategy.trailing_activate_pct and drop_pct >= strategy.trailing_drop_pct:
            reason = ExitReason.TRAILING_STOP

    if reason is None:
        return None
    return ExitDecision(
        exit_price=current_price,
        exit_reason=reason,
        pnl_amount=(current_price - position.entry_price) * position.quantity,
        pnl_pct=round(pnl_pct, 2),
    )


class PositionGuard:
    """보유 포지션(.positions.json)을 소유하고 청산을 전담한다.

    진입 쪽(QuantTradingSystem)은 save_position()으로 새 포지션을 넘기고, positions로 보유 현황을
    읽는다. on_hold는 청산 조건에 걸리지 않은 포지션마다 현재가와 함께 호출되는 훅으로,
    2차 트랜치 매수처럼 진입 쪽 로직을 끼워 넣는 데 쓴다. 반환한 Position이 저장된다.
    """

    def __init__(
        self,
        db: TradingDB,
        slack: SlackNotifier,
        db_submit: Callable[..., None],
        on_hold: Callable[[Position, int], Awaitable[Position]] | None = None,
    ) -> None:
        self._positions: dict[str, Position] = {p.stock_code: p for p in position_store.load()}
        self._db = db
        self._slack = slack
        self._db_submit = db_submit
        self._on_hold = on_hold
        self.exit_count = 0
        # 취소 실패로 청산을 보류했다고 이미 알린 종목 (5초마다 같은 알림이 쌓이지 않게)
        self._cancel_alerted: set[str] = set()

    # ------------------------------------------------------------------
    # 포지션 저장소
    # ------------------------------------------------------------------
    @property
    def positions(self) -> Mapping[str, Position]:
        return self._positions

    def save_position(self, position: Position) -> None:
        if self._positions.get(position.stock_code) == position:
            return
        self._positions[position.stock_code] = position
        position_store.save(list(self._positions.values()))

    def _drop_position(self, stock_code: str) -> None:
        self._cancel_alerted.discard(stock_code)
        if self._positions.pop(stock_code, None) is not None:
            position_store.save(list(self._positions.values()))

    # ------------------------------------------------------------------
    # 조회 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    async def _get_price(stock_code: str) -> int | None:
        try:
            return await asyncio.to_thread(api.get_current_price, stock_code)
        except Exception as exc:
            logger.warning("현재가 조회 실패 %s: %s", stock_code, exc)
            return None

    @staticmethod
    async def _get_actual_qty(stock_code: str) -> int | None:
        """계좌의 실제 보유수량. 조회 실패 시 None (0과 구분해야 한다)."""
        try:
            holdings = await asyncio.to_thread(api.get_holdings)
        except Exception as exc:
            logger.error("잔고 조회 실패 %s: %s", stock_code, exc)
            return None
        holding = holdings.get(stock_code)
        return holding.quantity if holding else 0

    # ------------------------------------------------------------------
    # 매도 주문 정리
    # ------------------------------------------------------------------
    @staticmethod
    async def cancel_sell_orders(position: Position) -> tuple[SellOrder, ...]:
        """걸려 있는 매도 주문을 모두 취소하고, 취소에 실패한 주문을 반환한다.

        실패 원인이 '이미 체결'인지 일시적 오류인지 응답만으로는 구분할 수 없으므로, 호출하는 쪽은
        실패한 주문을 버리지 말고 계속 추적해야 한다 (체결됐다면 잔고 확인에서 정리된다).
        """
        failed: list[SellOrder] = []
        for order in position.sell_orders:
            try:
                await asyncio.to_thread(api.cancel_order, position.stock_code, order.order_no, 0)
                logger.info("매도 주문 취소: %s %s", position.stock_code, order.order_no)
            except Exception as exc:
                failed.append(order)
                logger.warning("매도 주문 취소 실패(이미 체결됐을 수 있음) %s %s: %s", position.stock_code, order.order_no, exc)
        return tuple(failed)

    @staticmethod
    def _remove_filled_sell_orders(orders: Iterable[SellOrder], sold_qty: int) -> tuple[SellOrder, ...]:
        """체결된 sold_qty만큼 매도 주문을 제거/축소한 나머지를 반환한다.

        지정가가 낮은 주문(Quick Target)이 먼저 체결된다고 보고 가격 오름차순으로 차감한다.
        """
        remaining: list[SellOrder] = []
        left = sold_qty
        for order in sorted(orders, key=lambda o: o.target_price):
            if left >= order.quantity:
                left -= order.quantity
            elif left > 0:
                remaining.append(dataclasses.replace(order, quantity=order.quantity - left))
                left = 0
            else:
                remaining.append(order)
        return tuple(remaining)

    # ------------------------------------------------------------------
    # 청산 기록
    # ------------------------------------------------------------------
    @staticmethod
    def _final_decision(position: Position, exit_price: int, reason: ExitReason, qty: int) -> ExitDecision:
        """앞서 부분 체결된 실현손익까지 합친 포지션 전체 기준의 청산 결과."""
        pnl = position.realized_pnl + (exit_price - position.entry_price) * qty
        invested = position.entry_price * (position.sold_quantity + qty)
        return ExitDecision(
            exit_price=exit_price,
            exit_reason=reason,
            pnl_amount=pnl,
            pnl_pct=round(pnl / invested * 100, 2) if invested else 0.0,
        )

    def _record_exit(self, position: Position, decision: ExitDecision, sold_qty: int) -> None:
        self._drop_position(position.stock_code)
        self.exit_count += 1
        logger.info(
            "청산 완료(%s): %s %s %d주 @ %s원 → %+d원 (%+.2f%%)",
            decision.exit_reason, position.stock_code, position.stock_name, sold_qty,
            f"{decision.exit_price:,}", decision.pnl_amount, decision.pnl_pct,
        )
        if position.trade_id:
            if decision.exit_reason == ExitReason.TARGET:
                self._db_submit(
                    self._db.close_trade_target, position.trade_id, position.stock_code,
                    decision.exit_price, decision.pnl_pct, decision.pnl_amount,
                )
            else:
                self._db_submit(self._db.close_trade, position.trade_id, decision)

    # ------------------------------------------------------------------
    # 계좌 동기화 (지정가 매도 체결 감지)
    # ------------------------------------------------------------------
    async def _sync_with_account(self) -> None:
        if not self._positions:
            return
        try:
            holdings = await asyncio.to_thread(api.get_holdings)
        except Exception as exc:
            logger.warning("계좌 동기화 건너뜀(잔고 조회 실패): %s", exc)
            return
        if not holdings:
            # 포지션이 있는데 잔고가 비었다면 토큰 재발급 직후 등 일시적 오류일 가능성이 크다.
            logger.warning("계좌 동기화 건너뜀: 잔고 응답이 비어 있음 (보유 포지션 %d개)", len(self._positions))
            return

        now = _now()
        for position in list(self._positions.values()):
            if position.last_buy_time and (now - position.last_buy_time).total_seconds() < _SYNC_GRACE_SEC:
                continue

            holding = holdings.get(position.stock_code)
            actual_qty = holding.quantity if holding else 0

            if actual_qty == 0:
                qty, value = _orders_value(position.sell_orders)
                if qty:
                    exit_price = round(value / qty)
                else:
                    exit_price = await self._get_price(position.stock_code) or position.entry_price
                    logger.warning("매도 주문 정보 없이 잔고가 0 — 현재가로 청산 기록: %s", position.stock_code)
                decision = self._final_decision(position, exit_price, ExitReason.TARGET, position.quantity)
                logger.info("목표가 매도 전량 체결 감지: %s %s", position.stock_code, position.stock_name)
                self._record_exit(position, decision, position.quantity)
                await self._slack.notify_exit(
                    decision, ",".join(o.order_no for o in position.sell_orders),
                    position=position, sold_qty=position.quantity,
                )

            elif actual_qty < position.quantity:
                sold = position.quantity - actual_qty
                remaining = self._remove_filled_sell_orders(position.sell_orders, sold)
                before_qty, before_value = _orders_value(position.sell_orders)
                after_qty, after_value = _orders_value(remaining)
                filled_qty = before_qty - after_qty
                realized = (before_value - after_value) - position.entry_price * filled_qty
                updated = dataclasses.replace(
                    position,
                    quantity=actual_qty,
                    sell_orders=remaining,
                    realized_pnl=position.realized_pnl + realized,
                    sold_quantity=position.sold_quantity + sold,
                )
                self.save_position(updated)
                logger.info(
                    "목표가 매도 부분 체결 감지: %s %d주 체결 → 잔여 %d주 (실현 %+d원)",
                    position.stock_code, sold, actual_qty, realized,
                )
                await self._slack.notify_partial_fill(updated, sold)

            elif actual_qty > position.quantity:
                logger.debug(
                    "계좌 보유수량(%d)이 포지션(%d)보다 많음 — 수동 매수분으로 보고 무시: %s",
                    actual_qty, position.quantity, position.stock_code,
                )

    # ------------------------------------------------------------------
    # 안전 청산
    # ------------------------------------------------------------------
    async def _execute_exit(self, position: Position, decision: ExitDecision) -> None:
        code = position.stock_code
        logger.info("청산 시작(%s): %s %s @ ~%s원", decision.exit_reason, code, position.stock_name, f"{decision.exit_price:,}")

        # 1. 걸려 있는 목표가 매도 주문을 모두 취소한다 (남겨 두면 시장가 매도와 합쳐 초과 매도).
        #    취소에 실패한 주문은 목록에 남겨 다음 사이클에 다시 취소를 시도한다.
        prior_orders = position.sell_orders
        uncancelled = await self.cancel_sell_orders(position)
        position = dataclasses.replace(position, sell_orders=uncancelled)
        self.save_position(position)

        # 2. 실제 잔고를 확인한다. 취소 직전에 체결된 물량이 있을 수 있어 공매도를 막는다.
        actual_qty = await self._get_actual_qty(code)
        if actual_qty is None:
            logger.error("잔고 조회 실패 → 매도 보류(다음 가드 사이클에 재시도): %s", code)
            return

        unresolved = uncancelled
        if actual_qty < position.quantity:
            # 취소 전에 체결된 목표가 주문분을 실현손익에 반영한다.
            sold = position.quantity - actual_qty
            remaining = self._remove_filled_sell_orders(prior_orders, sold)
            (bq, bv), (aq, av) = _orders_value(prior_orders), _orders_value(remaining)
            # 취소 실패 주문 중 체결로 설명되지 않는 잔량만 아직 살아 있을 수 있는 주문으로 본다.
            uncancelled_nos = {o.order_no for o in uncancelled}
            unresolved = tuple(o for o in remaining if o.order_no in uncancelled_nos)
            position = dataclasses.replace(
                position,
                quantity=actual_qty,
                sell_orders=unresolved,
                realized_pnl=position.realized_pnl + (bv - av) - position.entry_price * (bq - aq),
                sold_quantity=position.sold_quantity + sold,
            )
            self.save_position(position)

        if actual_qty > 0 and unresolved:
            # 살아 있는 지정가 주문이 물량을 잡고 있으면 시장가 매도가 거부되거나 초과 매도가 된다.
            logger.error(
                "매도 주문 취소 실패 %d건(%s) → 시장가 매도 보류(다음 가드 사이클에 취소 재시도): %s",
                len(unresolved), ",".join(o.order_no for o in unresolved), code,
            )
            if code not in self._cancel_alerted:
                self._cancel_alerted.add(code)
                await self._slack.notify_error(
                    f"{code} {position.stock_name} {decision.exit_reason} 청산 보류: 지정가 매도 주문 "
                    f"{','.join(o.order_no for o in unresolved)} 취소 실패. 5초마다 재시도하며, "
                    "계속되면 HTS에서 주문 상태를 확인해 주세요."
                )
            return

        if actual_qty == 0:
            logger.warning("실제 잔고 0 → 매도 생략, 포지션만 제거: %s", code)
            final = self._final_decision(position, decision.exit_price, decision.exit_reason, 0)
            self._record_exit(position, final, 0)
            await self._slack.notify_exit(final, "", position=position, sold_qty=0)
            return

        # 3. 실제 보유수량으로 시장가 매도. 수동 매수분까지 팔지 않도록 포지션 수량을 넘지 않는다.
        sell_qty = min(actual_qty, position.quantity)
        try:
            order_no = await asyncio.to_thread(api.place_sell_order, code, sell_qty, None, "3")
        except Exception as exc:
            logger.error("시장가 매도 실패(다음 가드 사이클에 재시도): %s %d주 — %s", code, sell_qty, exc)
            await self._slack.notify_error(f"{code} {position.stock_name} 시장가 매도 실패: {exc}")
            return

        logger.info("시장가 매도 주문: %s %d주 (%s)", code, sell_qty, order_no)
        final = self._final_decision(position, decision.exit_price, decision.exit_reason, sell_qty)
        self._record_exit(position, final, sell_qty)
        await self._slack.notify_exit(final, order_no, position=position, sold_qty=sell_qty)

    async def _force_close_all(self) -> None:
        if not self._positions:
            return
        logger.info("장 마감 강제청산: 보유 %d개", len(self._positions))
        await self._sync_with_account()
        for position in list(self._positions.values()):
            price = await self._get_price(position.stock_code) or position.entry_price
            decision = ExitDecision(
                exit_price=price,
                exit_reason=ExitReason.MARKET_CLOSE,
                pnl_amount=(price - position.entry_price) * position.quantity,
                pnl_pct=round((price - position.entry_price) / position.entry_price * 100, 2),
            )
            await self._execute_exit(position, decision)

    # ------------------------------------------------------------------
    # 5초 주기 루프
    # ------------------------------------------------------------------
    async def check(self) -> None:
        """가드 1사이클. 장중(평일 09:00~15:30)에만 동작한다."""
        now = _now()
        if now.weekday() >= 5 or not (MARKET_OPEN <= now.time() < MARKET_CLOSE):
            return
        if now.time() >= FORCE_CLOSE_TIME:
            # 매도에 실패한 포지션이 남아 있으면 다음 사이클에 다시 시도한다.
            await self._force_close_all()
            return
        if not self._positions:
            return

        await self._sync_with_account()

        for position in list(self._positions.values()):
            if position.stock_code not in self._positions:
                continue  # 동기화 중 청산됨
            price = await self._get_price(position.stock_code)
            if price is None:
                continue

            position = position.with_updated_high(price)
            # 청산 조건을 먼저 본다. 손절선 근처에서 눌림 2차 매수 후 바로 손절하는 일을 막는다.
            decision = check_exit(position, price, now)
            if decision is not None:
                await self._execute_exit(position, decision)
                continue

            if self._on_hold is not None:
                position = await self._on_hold(position, price)
            self.save_position(position)

    async def run(self) -> None:
        """GUARD_INTERVAL_SEC마다 check()를 돈다. 한 사이클이 실패해도 루프는 계속된다."""
        cycle = 0
        failures = 0
        while True:
            try:
                await self.check()
                failures = 0
            except Exception as exc:
                failures += 1
                logger.exception("가드 체크 실패 (다음 사이클 계속, 연속 %d회)", failures)
                if failures == _GUARD_NOTIFY_AFTER_FAILURES:
                    self._slack.fire(self._slack.notify_error(f"Position Guard 연속 {failures}회 실패: {exc}"))
            cycle += 1
            if cycle % _HEARTBEAT_EVERY_CYCLES == 0:
                logger.info(
                    "Guard heartbeat: 보유 %d개 [%s]",
                    len(self._positions), ", ".join(self._positions) or "-",
                )
            await asyncio.sleep(config.GUARD_INTERVAL_SEC)
