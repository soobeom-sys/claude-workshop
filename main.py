"""엔트리포인트: QuantTradingSystem 오케스트레이터를 구동한다.

조건검색 편입 신호 수신 → AI 진입 판단(Claude Code CLI) → 목표가 매도 주문 등록 →
Position Guard(코드 기반)로 손절/시간제한/트레일링 청산 → 계좌 동기화로 목표가 체결 반영
까지의 전체 흐름을 관리한다.

    uv run python main.py

Ctrl+C(SIGINT)/SIGTERM을 받으면 새 신호 처리를 멈추고 안전하게 종료한다. 보유 포지션은
청산하지 않고 .positions.json에 남겨 두며, 다음 실행 시 복구해 계속 감시한다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import logging
import re
import signal as signal_module
import sys
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from zoneinfo import ZoneInfo

import config
from agents.entry import call_entry_agent
from condition.client import KiwoomConditionClient
from condition.models import Signal
from db import TradingDB
from market import api
from notifier.slack import get_notifier
from trading.models import Position, SellOrder
from trading.order import place_target_orders
from trading.position_guard import FORCE_CLOSE_TIME, PositionGuard
from trading.tranche import apply_split_buy, try_tranche2_buy

logger = logging.getLogger("main")

_KST = ZoneInfo("Asia/Seoul")

_HEALTH_INTERVAL_SEC = 600

_WS_BACKOFF_SEC = [0, 5, 10, 20, 40, 60]
_WS_STABLE_SESSION_SEC = 300
_WS_NOTIFY_AFTER_FAILURES = 3

# 파싱 실패로 Position을 만들지 못했지만 AI가 매수로 결정한 경우를 감지한다.
_BUY_DECISION_RE = re.compile(r"\[결정\]\s*매수")


def _now() -> dt.datetime:
    return dt.datetime.now(_KST)


class QuantTradingSystem:
    # 동시에 최대 2개 매수 에이전트만 실행한다 (Claude CLI 프로세스 수 제한).
    _agent_sem = asyncio.Semaphore(2)

    def __init__(self) -> None:
        self._processing: set[str] = set()
        self._traded_today: set[str] = set()
        self._traded_date: dt.date = _now().date()
        # DB 기록용. TradingDB 메서드는 이미 non-blocking이지만, 클라이언트 호출 자체가
        # 이벤트 루프를 막지 않도록 한 번 더 격리한다. 워커 1개라 제출 순서가 보존된다.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-writer")
        self._db = TradingDB()
        # 프로세스 공용 싱글톤. 조건검색 클라이언트(notify_signal)와 진입 에이전트
        # (notify_skip/notify_error)에도 같은 인스턴스를 주입한다.
        self._slack = get_notifier()
        # 보유 포지션과 청산은 Position Guard가 전담한다. 2차 트랜치(추가 매수)만 훅으로 끼워 넣는다.
        self._guard = PositionGuard(self._db, self._slack, self._db_submit, on_hold=self._on_position_hold)
        self._positions = self._guard.positions

        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_client: KiwoomConditionClient | None = None
        self._signal_tasks: set[asyncio.Task[None]] = set()
        # 세마포어 안에서 실행 중인 진입 에이전트 수. 동시에 여러 에이전트가 매수해
        # MAX_POSITIONS를 넘지 않도록 보유 수에 더해 계산한다.
        self._agents_running = 0

        self._signal_count = 0
        self._entry_count = 0

    # ------------------------------------------------------------------
    # 공통 헬퍼
    # ------------------------------------------------------------------
    def _db_submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """DB 기록을 fire-and-forget으로 넘긴다. 실패는 로그만 남긴다."""

        def _log_failure(future: Future[Any]) -> None:
            exc = future.exception()
            if exc is not None:
                logger.error("DB 기록 실패: %s", getattr(fn, "__name__", fn), exc_info=exc)

        try:
            self._executor.submit(fn, *args, **kwargs).add_done_callback(_log_failure)
        except RuntimeError:
            # 종료 중 executor가 이미 닫힌 경우
            logger.warning("DB executor가 종료되어 기록을 건너뜁니다: %s", getattr(fn, "__name__", fn))

    # ------------------------------------------------------------------
    # 신호 → 진입
    # ------------------------------------------------------------------
    def _on_entry_event(self, event: dict[str, Any]) -> None:
        """KiwoomConditionClient의 on_entry 콜백.

        클라이언트는 콜백을 await하는 동안 수신(PING 응답 포함)을 멈추므로, AI 판단(최대 수 분)은
        별도 태스크로 넘기고 즉시 반환한다.
        """
        self._signal_count += 1
        signal = Signal.from_event(event)
        task = asyncio.create_task(self._on_signal(signal, event.get("signal_id")), name=f"signal-{signal.stock_code}")
        self._signal_tasks.add(task)
        task.add_done_callback(self._signal_tasks.discard)

    async def _on_signal(self, signal: Signal, signal_id: str | None = None) -> None:
        today = _now().date()
        if today != self._traded_date:
            logger.info("날짜 변경(%s → %s): 당일 거래 종목 목록 초기화", self._traded_date, today)
            self._traded_date = today
            self._traded_today.clear()

        code = signal.stock_code
        label = f"{code} {signal.stock_name} [{signal.condition_name}]"
        if not code or self._stop.is_set():
            return
        if _now().time() >= FORCE_CLOSE_TIME:
            logger.info("스킵(장 마감 청산 시간대): %s", label)
            return
        if code in self._processing:
            logger.info("스킵(처리 중): %s", label)
            return
        if code in self._traded_today:
            logger.info("스킵(당일 거래 완료): %s", label)
            return
        if code in self._positions:
            logger.info("스킵(보유 중): %s", label)
            return
        if len(self._positions) >= config.MAX_POSITIONS:
            logger.info("스킵(최대 보유 %d개 도달): %s", config.MAX_POSITIONS, label)
            return

        # 여기까지 await 없이 진행되므로 같은 종목의 동시 신호가 중복 통과하지 않는다.
        self._processing.add(code)
        try:
            if not signal.signal_price or not signal.stock_name:
                try:
                    info = await asyncio.to_thread(api.get_stock_basic_info, code)
                    signal = dataclasses.replace(
                        signal,
                        stock_name=signal.stock_name or info.stock_name,
                        signal_price=signal.signal_price or info.current_price,
                        signal_change_pct=(
                            signal.signal_change_pct if signal.signal_change_pct is not None else info.change_pct
                        ),
                    )
                except Exception as exc:
                    logger.warning("종목 기본정보 보충 실패 %s: %s", code, exc)

            async with self._agent_sem:
                # 세마포어 대기 중에 상황이 바뀌었을 수 있으므로 다시 확인한다.
                if self._stop.is_set():
                    return
                if len(self._positions) + self._agents_running >= config.MAX_POSITIONS:
                    logger.info("스킵(보유+판단 중 %d개가 최대치): %s", config.MAX_POSITIONS, label)
                    return
                strategy = config.get_condition_strategy(signal.condition_name)
                started = time.monotonic()
                self._agents_running += 1
                try:
                    bought, position, ai_summary = await call_entry_agent(signal, strategy, self._slack)
                finally:
                    # 여기서부터 _open_position()이 포지션을 저장하기까지 await가 없으므로,
                    # 보유 수와 판단 중 수에 이중으로 잡히거나 빠지는 순간이 없다.
                    self._agents_running -= 1
                latency_ms = int((time.monotonic() - started) * 1000)

                if signal_id:
                    self._db_submit(
                        self._db.update_ai_decision, signal_id,
                        "매수" if bought else "패스", ai_summary, config.ENTRY_MODEL, latency_ms,
                    )

                if bought and position is not None:
                    await self._open_position(position, signal_id, ai_summary)
                elif _BUY_DECISION_RE.search(ai_summary or ""):
                    # 주문이 나갔을 수 있는데 추적하지 못하는 상태. 재매수를 막는다.
                    # (계좌 확인 요청 알림은 call_entry_agent가 보냈다)
                    self._traded_today.add(code)
        except Exception as exc:
            logger.exception("신호 처리 실패: %s", label)
            self._slack.fire(self._slack.notify_error(f"{label} 신호 처리 실패: {exc}"))
        finally:
            self._processing.discard(code)

    async def _open_position(self, position: Position, signal_id: str | None, ai_summary: str) -> None:
        now = _now()
        position = dataclasses.replace(
            position,
            trade_id=str(uuid.uuid4()),
            signal_id=signal_id,
            last_buy_time=now,
        )
        position = apply_split_buy(position, now)
        # 매도 주문보다 먼저 저장해, 도중에 죽어도 재시작 시 Guard가 포지션을 이어받게 한다.
        self._guard.save_position(position)
        self._traded_today.add(position.stock_code)
        self._entry_count += 1

        position = await place_target_orders(position)
        orders = position.sell_orders
        self._guard.save_position(position)

        self._db_submit(
            self._db.save_trade,
            {
                "id": position.trade_id,
                "signal_id": signal_id,
                "stock_code": position.stock_code,
                "stock_name": position.stock_name or None,
                "entry_price": position.entry_price,
                "quantity": position.quantity,
                "entry_time": position.entry_time.isoformat(),
                "condition_name": position.condition_name,
                "strategy_json": dataclasses.asdict(position.strategy),
                "exchange": position.exchange,
            },
        )
        self._save_sell_orders_db(position, orders)
        await self._slack.notify_entry(position, ai_summary)

    def _save_sell_orders_db(self, position: Position, orders: tuple[SellOrder, ...]) -> None:
        if position.trade_id and orders:
            self._db_submit(
                self._db.save_sell_orders,
                position.trade_id,
                [{"order_no": o.order_no, "quantity": o.quantity, "target_price": o.target_price} for o in orders],
            )

    # ------------------------------------------------------------------
    # 2차 트랜치
    # ------------------------------------------------------------------
    async def _on_position_hold(self, position: Position, price: int) -> Position:
        """Position Guard 훅: 청산 조건에 걸리지 않은 포지션마다 5초 주기로 호출된다."""
        if position.pending_tranche2:
            return await self._try_tranche2_buy(position, price)
        return position

    async def _try_tranche2_buy(self, position: Position, price: int) -> Position:
        updated, fill = await try_tranche2_buy(position, price, persist=self._guard.save_position)
        if fill is None:
            return updated
        if updated.trade_id:
            self._db_submit(
                self._db.update_trade, updated.trade_id,
                {"entry_price": updated.entry_price, "quantity": updated.quantity},
            )
        self._save_sell_orders_db(updated, fill.sell_orders)
        await self._slack.notify_tranche2(updated, fill.quantity, fill.price, fill.trigger)
        return updated

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL_SEC)
            logger.info(
                "Health: 신호 %d건 / 매수 %d건 / 청산 %d건 / 보유 %d개 / 판단 중 %d건",
                self._signal_count, self._entry_count, self._guard.exit_count, len(self._positions), len(self._processing),
            )
            await self._slack.notify_health(self._entry_count, self._guard.exit_count, len(self._positions))

    # ------------------------------------------------------------------
    # 조건검색 WebSocket
    # ------------------------------------------------------------------
    async def _condition_ws_loop(self) -> None:
        """한 번 연결해 구독·수신한다. 연결이 끊기면 예외를 올려 _async_run이 재접속하게 한다."""
        client = KiwoomConditionClient(on_entry=self._on_entry_event, db=self._db, notifier=self._slack)
        self._ws_client = client
        try:
            await client.run()
        finally:
            await client.close()
            self._ws_client = None

    async def _run_session(self) -> None:
        await self._condition_ws_loop()

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """seconds만큼 기다린다. 도중에 종료 요청이 오면 True."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _async_run(self) -> None:
        backoff = _WS_BACKOFF_SEC
        attempt = 0
        while not self._stop.is_set():
            delay = backoff[min(attempt, len(backoff) - 1)]
            if delay:
                logger.warning("조건검색 재접속 대기 %d초 (연속 실패 %d회)", delay, attempt)
            if await self._sleep_or_stop(delay):
                break

            session_start = time.monotonic()
            try:
                await self._run_session()
                logger.warning("조건검색 세션이 종료되었습니다")
                attempt += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                logger.warning("조건검색 세션 오류 (연속 %d회): %s", attempt, exc, exc_info=True)
                if attempt == _WS_NOTIFY_AFTER_FAILURES:
                    await self._slack.notify_error(f"조건검색 WebSocket 연속 {attempt}회 실패: {exc}")

            if time.monotonic() - session_start >= _WS_STABLE_SESSION_SEC:
                attempt = 0  # 안정적으로 유지되던 연결이 끊긴 것 → 즉시 재접속

    # ------------------------------------------------------------------
    # 수명 주기
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            if self._stop.is_set():
                logger.warning("이미 종료 중입니다 (%s)", signal_module.Signals(signum).name)
                return
            logger.warning("종료 신호 수신(%s) → 안전 종료를 시작합니다", signal_module.Signals(signum).name)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._stop.set)

        signal_module.signal(signal_module.SIGINT, handler)
        signal_module.signal(signal_module.SIGTERM, handler)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._install_signal_handlers()

        env = "모의투자" if config.USE_MOCK else "실계좌"
        logger.info(
            "QuantTradingSystem 시작 (%s) — 복구 포지션 %d개 %s, 최대 보유 %d개, 1회 매수금액 %s원",
            env, len(self._positions), list(self._positions) or "", config.MAX_POSITIONS, f"{config.TRADE_AMOUNT:,}",
        )
        await self._slack.notify_startup(len(self._positions))

        tasks = [
            asyncio.create_task(self._async_run(), name="condition-ws"),
            asyncio.create_task(self._guard.run(), name="position-guard"),
            asyncio.create_task(self._health_loop(), name="health"),
        ]
        stop_waiter = asyncio.create_task(self._stop.wait(), name="stop-waiter")
        try:
            done, _ = await asyncio.wait([*tasks, stop_waiter], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stop_waiter and not task.cancelled() and task.exception() is not None:
                    logger.error("%s 태스크가 비정상 종료되어 전체를 종료합니다", task.get_name(), exc_info=task.exception())
                    await self._slack.notify_error(f"{task.get_name()} 태스크 비정상 종료: {task.exception()}")
        finally:
            self._stop.set()
            for task in [*tasks, stop_waiter]:
                task.cancel()
            await asyncio.gather(*tasks, stop_waiter, return_exceptions=True)
            await self._shutdown()

    async def _shutdown(self) -> None:
        logger.info("종료 처리 중...")

        # 1. WebSocket 해제 (정상 경로에서는 태스크 취소 시 이미 닫힌다)
        if self._ws_client is not None:
            await self._ws_client.close()

        # 2. 진행 중인 진입 판단은 끝까지 기다린다. 도중에 끊으면 에이전트가 낸 매수 주문을
        #    추적하지 못하게 된다. 결과 포지션은 .positions.json에 남아 재시작 시 복구된다.
        if self._signal_tasks:
            timeout = config.ENTRY_AGENT_TIMEOUT_SEC + 30
            logger.info("진행 중인 진입 판단 %d건 완료 대기 (최대 %.0f초)", len(self._signal_tasks), timeout)
            _, pending = await asyncio.wait(set(self._signal_tasks), timeout=timeout)
            for task in pending:
                task.cancel()

        # 3. 일일 요약 / 조건식 성과 갱신 후 DB 풀 정리 (대기 중인 쓰기를 모두 flush)
        self._db_submit(self._db.build_daily_summary, _now().date())
        self._db_submit(self._db.refresh_condition_performance)
        self._executor.shutdown(wait=True)
        await asyncio.to_thread(self._db.close)

        # 4. Slack 종료 알림. 백그라운드로 띄운 알림(패스/탐지 등)을 먼저 흘려보낸다.
        await self._slack.flush()
        await self._slack.notify_shutdown(
            self._signal_count, self._entry_count, self._guard.exit_count, len(self._positions)
        )
        logger.info(
            "종료 완료 — 신호 %d건 / 매수 %d건 / 청산 %d건 / 미청산 보유 %d개",
            self._signal_count, self._entry_count, self._guard.exit_count, len(self._positions),
        )


def _setup_logging() -> None:
    # Windows 콘솔에서도 종목명이 깨지지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "websockets", "hpack", "slack_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    config.validate()
    await QuantTradingSystem().run()


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("강제 종료")
