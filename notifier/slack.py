"""Slack 알림 전송 모듈.

SlackNotifier의 notify_* 메서드는 모두 async이며 내부적으로 asyncio.to_thread()로
slack_sdk를 호출한다. 각 알림은 Block Kit(header + section)으로 된 독립 메시지다.

- SLACK_CHANNEL_CONDITION: 종목 탐지, 매수/패스, 청산 등 매매 이벤트
- SLACK_CHANNEL_HEALTH: 시작/종료, 오류

slack-sdk가 설치되지 않았거나 SLACK_BOT_TOKEN이 비어 있으면 메시지를 logging으로 대신
남긴다. 알림 실패가 매매 로직을 막아서는 안 되므로, 모든 notify_* 메서드는 메시지 생성·전송
중 어떤 예외도 호출부로 올리지 않는다(@_safe).

인스턴스는 get_notifier()로 얻는 프로세스당 하나의 싱글톤을 쓰고, main.py가 이를 각 모듈에
주입한다. 수신 루프처럼 지연이 치명적인 곳에서는 fire()로 백그라운드 전송한다.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import unicodedata
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any, TypeVar

import config
from condition.models import Signal
from trading.models import ExitDecision, Position

try:
    from slack_sdk import WebClient
except ImportError:  # pragma: no cover - 의존성 누락 시 폴백
    WebClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Slack Block Kit 제한: header plain_text 150자, section text 3000자
_HEADER_MAX = 150
_SECTION_MAX = 3000
_AI_SUMMARY_MAX = 1500


def _display_width(text: str) -> int:
    """고정폭 글꼴에서의 표시 폭. 한글 등 전각 문자는 2칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _escape(text: str) -> str:
    """Slack mrkdwn 제어 문자(&, <, >)를 이스케이프한다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _won(value: int | None) -> str:
    return "-" if value is None else f"{value:,}원"


def _signed_won(value: int) -> str:
    return f"{value:+,}원"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}%"


def _price_at(base: int, pct: float) -> int:
    return round(base * (1 + pct / 100))


def _hhmmss(text: str) -> str:
    """키움 원본 시각 'HHMMSS'를 'HH:MM:SS'로. 형식이 다르면 그대로 둔다."""
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"
    return text or "-"


def _stock_label(name: str, code: str) -> str:
    return f"{name or '?'}({code})"


_F = TypeVar("_F", bound=Callable[..., Awaitable[None]])


def _safe(fn: _F) -> _F:
    """알림 메서드가 어떤 예외도 호출부(매매 로직)로 올리지 않게 한다."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            await fn(*args, **kwargs)
        except Exception:
            logger.exception("Slack 알림 처리 실패: %s", fn.__name__)

    return wrapper  # type: ignore[return-value]


class SlackNotifier:
    def __init__(self, token: str | None = None) -> None:
        token = config.SLACK_BOT_TOKEN if token is None else token
        self._client: Any = None
        # fire()로 띄운 전송 태스크. 참조를 쥐고 있어야 GC로 중간에 사라지지 않는다.
        self._pending: set[asyncio.Task[None]] = set()
        if WebClient is None:
            logger.warning("slack-sdk가 설치되지 않아 Slack 알림을 로그로 대신합니다")
        elif not token:
            logger.info("SLACK_BOT_TOKEN이 비어 있어 Slack 알림을 로그로 대신합니다")
        else:
            self._client = WebClient(token=token)

    def fire(self, coro: Coroutine[Any, Any, None]) -> None:
        """알림 코루틴을 백그라운드 태스크로 띄우고 즉시 반환한다.

        호출부가 Slack 응답(수백 ms)을 기다리지 않아야 할 때 쓴다.
        예: notifier.fire(notifier.notify_signal(signal))
        """
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            logger.warning("이벤트 루프가 없어 Slack 알림을 건너뜁니다")
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def flush(self, timeout: float = 10.0) -> None:
        """fire()로 띄운 전송이 끝나길 최대 timeout초 기다린다. 종료 직전에 호출한다."""
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=timeout)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _table(rows: Sequence[tuple[str, str]]) -> str:
        """(항목, 값) 목록을 항목 열을 맞춘 코드 블록 문자열로 만든다."""
        if not rows:
            return ""
        width = max(_display_width(label) for label, _ in rows)
        lines = [f"{label}{' ' * (width - _display_width(label))}  {value}" for label, value in rows]
        return "```\n" + _escape("\n".join(lines)) + "\n```"

    @staticmethod
    def _env_label() -> str:
        return "모의" if config.USE_MOCK else "실계좌"

    def _blocks(self, title: str, *sections: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": _truncate(f"[{self._env_label()}] {title}", _HEADER_MAX), "emoji": True},
            }
        ]
        for text in sections:
            if text:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text, _SECTION_MAX)}})
        return blocks

    def _post_sync(self, channel: str, title: str, sections: tuple[str, ...]) -> None:
        # text는 알림 미리보기와 Block Kit 미지원 클라이언트용 대체 문구다.
        fallback = f"[{self._env_label()}] {title}"
        if self._client is None:
            logger.info("[Slack 미전송 %s] %s\n%s", channel, fallback, "\n".join(s for s in sections if s))
            return
        try:
            self._client.chat_postMessage(channel=channel, text=fallback, blocks=self._blocks(title, *sections))
        except Exception as exc:
            logger.warning("Slack 전송 실패 (%s) %s: %s", channel, fallback, exc)

    async def _post(self, channel: str, title: str, *sections: str) -> None:
        try:
            await asyncio.to_thread(self._post_sync, channel, title, sections)
        except Exception:
            # to_thread 자체가 실패하는 경우(종료 중 등)까지 삼킨다.
            logger.exception("Slack 알림 처리 실패: %s", title)

    # ------------------------------------------------------------------
    # #stock-condition: 매매 이벤트
    # ------------------------------------------------------------------
    @_safe
    async def notify_signal(self, signal: Signal) -> None:
        """조건식 편입 종목 탐지."""
        table = self._table([
            ("종목", _stock_label(signal.stock_name, signal.stock_code)),
            ("조건식", signal.condition_name),
            ("현재가", _won(signal.signal_price)),
            ("등락률", _pct(signal.signal_change_pct)),
            ("거래량", "-" if signal.volume is None else f"{signal.volume:,}주"),
            ("편입시각", _hhmmss(signal.signal_time)),
        ])
        await self._post(config.SLACK_CHANNEL_CONDITION, f"🔎 종목 탐지 {_stock_label(signal.stock_name, signal.stock_code)}", table)

    @_safe
    async def notify_entry(self, position: Position, ai_summary: str) -> None:
        """매수 체결. 매입가/목표가/손절가/전략/매도예약 정보를 포함한다."""
        s = position.strategy
        rows = [
            ("종목", _stock_label(position.stock_name, position.stock_code)),
            ("조건식", position.condition_name),
            ("매입가", _won(position.entry_price)),
            ("수량", f"{position.quantity:,}주"),
            ("매입금액", _won(position.entry_price * position.quantity)),
            ("목표가", f"{_won(_price_at(position.entry_price, s.target_pct))} ({s.target_pct:+.2f}%)"),
            ("손절가", f"{_won(_price_at(position.entry_price, s.stop_pct))} ({s.stop_pct:+.2f}%)"),
            ("전략", f"{s.style} / 최대보유 {s.max_hold_min}분"),
        ]
        if s.trailing_activate_pct is not None and s.trailing_drop_pct is not None:
            rows.append(("트레일링", f"+{s.trailing_activate_pct:.2f}% 활성 / -{s.trailing_drop_pct:.2f}% 하락 시"))
        if position.pending_tranche2:
            rows.append(("분할매수", "1차 체결, 2차 대기"))
        if position.sell_orders:
            for i, order in enumerate(position.sell_orders, 1):
                rows.append((f"매도예약{i}", f"{order.target_price:,}원 × {order.quantity:,}주 (#{order.order_no})"))
        else:
            rows.append(("매도예약", "없음"))

        sections = [self._table(rows)]
        if ai_summary:
            sections.append("*AI 판단*\n" + self._quote(_escape(_truncate(ai_summary.strip(), _AI_SUMMARY_MAX))))
        await self._post(
            config.SLACK_CHANNEL_CONDITION,
            f"🟢 매수 체결 {_stock_label(position.stock_name, position.stock_code)}",
            *sections,
        )

    @_safe
    async def notify_skip(self, signal: Signal, reason: str) -> None:
        """AI 판단 등으로 매수를 패스한 경우."""
        table = self._table([
            ("종목", _stock_label(signal.stock_name, signal.stock_code)),
            ("조건식", signal.condition_name),
            ("신호가", _won(signal.signal_price)),
            ("등락률", _pct(signal.signal_change_pct)),
            ("편입시각", _hhmmss(signal.signal_time)),
        ])
        await self._post(
            config.SLACK_CHANNEL_CONDITION,
            f"⚪ 매수 패스 {_stock_label(signal.stock_name, signal.stock_code)}",
            table,
            "*패스 사유*\n" + self._quote(_escape(_truncate((reason or "-").strip(), _AI_SUMMARY_MAX))),
        )

    @_safe
    async def notify_exit(
        self,
        decision: ExitDecision,
        order_no: str,
        *,
        position: Position,
        sold_qty: int | None = None,
    ) -> None:
        """청산. ExitDecision에는 종목/매입가가 없으므로 position을 함께 받는다.

        수익률/손익금액은 decision 값(부분 익절분까지 합친 포지션 전체 기준)을 그대로 쓴다.
        """
        qty = position.quantity if sold_qty is None else sold_qty
        rows = [
            ("종목", _stock_label(position.stock_name, position.stock_code)),
            ("사유", str(decision.exit_reason)),
            ("매입가", _won(position.entry_price)),
            ("청산가", _won(decision.exit_price)),
            ("청산수량", f"{qty:,}주"),
        ]
        if position.sold_quantity:
            rows.append(("기청산", f"{position.sold_quantity:,}주 (실현 {_signed_won(position.realized_pnl)})"))
        rows += [
            ("수익률", _pct(decision.pnl_pct)),
            ("손익금액", _signed_won(decision.pnl_amount)),
            ("주문번호", order_no or "-"),
        ]
        icon = "🔵" if decision.pnl_amount >= 0 else "🔴"
        await self._post(
            config.SLACK_CHANNEL_CONDITION,
            f"{icon} 청산({decision.exit_reason}) {_stock_label(position.stock_name, position.stock_code)}",
            self._table(rows),
        )

    @_safe
    async def notify_tranche2(self, position: Position, added_qty: int, price: int, trigger: str) -> None:
        """분할매수 2차 트랜치 체결."""
        table = self._table([
            ("종목", _stock_label(position.stock_name, position.stock_code)),
            ("트리거", trigger),
            ("추가매수", f"{added_qty:,}주 @ {_won(price)}"),
            ("총 수량", f"{position.quantity:,}주"),
            ("평균단가", _won(position.entry_price)),
        ])
        await self._post(
            config.SLACK_CHANNEL_CONDITION,
            f"➕ 2차 매수 {_stock_label(position.stock_name, position.stock_code)}",
            table,
        )

    @_safe
    async def notify_partial_fill(self, position: Position, sold_qty: int) -> None:
        """목표가 매도 주문 부분 체결(분할 익절)."""
        table = self._table([
            ("종목", _stock_label(position.stock_name, position.stock_code)),
            ("체결수량", f"{sold_qty:,}주"),
            ("잔여수량", f"{position.quantity:,}주"),
            ("누적실현", _signed_won(position.realized_pnl)),
        ])
        await self._post(
            config.SLACK_CHANNEL_CONDITION,
            f"💰 부분 익절 {_stock_label(position.stock_name, position.stock_code)}",
            table,
        )

    # ------------------------------------------------------------------
    # #stock-health: 시스템 상태
    # ------------------------------------------------------------------
    @_safe
    async def notify_startup(self, restored_positions: int = 0) -> None:
        buy_window = f"09:{config.SKIP_BEFORE_MIN:02d} ~ {config.SKIP_AFTER_HOUR:02d}:00"
        table = self._table([
            ("모드", "모의투자" if config.USE_MOCK else "실계좌"),
            ("Entry 모델", config.ENTRY_MODEL),
            ("매수 시간", f"{buy_window} (조건식별 전략 우선)"),
            ("Guard 주기", f"{config.GUARD_INTERVAL_SEC}초"),
            ("최대 보유", f"{config.MAX_POSITIONS}종목"),
            ("1회 매수금액", _won(config.TRADE_AMOUNT)),
            ("분할매매", "사용" if config.SPLIT_TRADE_ENABLED else "미사용"),
            ("복구 포지션", f"{restored_positions}개"),
        ])
        await self._post(config.SLACK_CHANNEL_HEALTH, "🚀 에이전트 시작", table)

    @_safe
    async def notify_shutdown(
        self, signals: int = 0, entries: int = 0, exits: int = 0, positions: int = 0
    ) -> None:
        table = self._table([
            ("수신 신호", f"{signals}건"),
            ("매수", f"{entries}건"),
            ("청산", f"{exits}건"),
            ("미청산 보유", f"{positions}개 (재시작 시 복구)"),
        ])
        await self._post(config.SLACK_CHANNEL_HEALTH, "🛑 에이전트 종료", table)

    @_safe
    async def notify_error(self, msg: str) -> None:
        await self._post(config.SLACK_CHANNEL_HEALTH, "⚠️ 오류 발생", "```\n" + _escape(_truncate(msg, _SECTION_MAX - 100)) + "\n```")

    @_safe
    async def notify_health(self, entries: int, exits: int, positions: int) -> None:
        """헬스체크는 Slack으로 보내지 않고 로그만 남긴다.

        main.py가 같은 주기에 더 자세한 Health 로그를 INFO로 남기므로 중복되지 않게 DEBUG로 둔다.
        """
        logger.debug("Health: 매수 %d건 / 청산 %d건 / 보유 %d개", entries, exits, positions)

    @staticmethod
    def _quote(text: str) -> str:
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


_instance: SlackNotifier | None = None


def get_notifier() -> SlackNotifier:
    """프로세스 전체에서 공유하는 SlackNotifier 싱글톤을 반환한다."""
    global _instance
    if _instance is None:
        _instance = SlackNotifier()
    return _instance
