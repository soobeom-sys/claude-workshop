"""키움증권 조건검색 실시간 WebSocket 클라이언트.

연결 후 LOGIN → CNSRLST(조건식 목록) → CNSRREQ(search_type=1, 실시간 구독)
순서로 핸드셰이크한 뒤, 조건식에 종목이 편입(843=I)/이탈(843=O)될 때마다
on_entry / on_exit 콜백을 호출한다.

생성자에 notifier(SlackNotifier)를 넘기면 편입 종목을 Slack으로 알린다. 전송은
백그라운드 태스크라 수신 루프를 막지 않는다.

생성자에 db(TradingDB)를 넘기면 편입 신호를 signals 테이블에도 적재한다.
저장은 TradingDB 내부 ThreadPoolExecutor로 넘어가는 fire-and-forget이라
수신 루프를 막지 않으며, 저장 실패는 로그만 남기고 수신을 계속한다.
발급된 signal_id는 on_entry로 넘어가는 이벤트의 'signal_id' 키에 담긴다.

키움 WebSocket은 표준 WebSocket ping/pong 프레임을 쓰지 않고 JSON 본문으로
된 자체 PING 프로토콜을 쓴다. 그래서 websockets의 자동 keepalive는 끄고
(ping_interval=None, ping_timeout=None) 아래 듀얼 타임아웃으로 직접 관리한다.

- _IDLE_PROBE_SEC(45초) 동안 아무 메시지도 없으면 PING을 보내고,
- _PING_WAIT_SEC(15초) 안에 응답이 없으면 죽은 연결로 보고 ConnectionClosed를
  올려 상위(run_forever)가 재접속하게 한다.

서버가 보낸 PING은 받은 그대로 되돌려주며, 이 메시지는 _recv_skip_ping()에서
걸러지므로 업무 로직에는 노출되지 않는다.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

import config
from condition.models import Signal
from market import token

if TYPE_CHECKING:
    from notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)

_WS_URL_REAL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
_WS_URL_MOCK = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"

# 키움 자체 PING 프로토콜용 듀얼 타임아웃.
_IDLE_PROBE_SEC = 45
_PING_WAIT_SEC = 15

_CONNECT_TIMEOUT_SEC = 10

# 재연결 지수 백오프. 첫 재시도는 즉시(0초), 이후 최대 60초까지 늘린다.
_BACKOFF_SEC = (0, 5, 10, 20, 40, 60)
# 이 시간 이상 연결이 유지됐다면 일시적 끊김으로 보고 실패 카운터를 리셋한다.
_STABLE_CONNECTION_SEC = 300
# 연속 실패가 이 횟수에 도달하면 notify_error로 알린다.
_MAX_CONSECUTIVE_FAILURES = 3

# 실시간 조건검색 응답의 필드 ID -> 사람이 읽을 수 있는 키.
_FIELD_MAP = {
    "9001": "stock_code",
    "302": "stock_name",
    "10": "current_price",
    "12": "change_pct",
    "13": "volume",
    "843": "signal_type",  # I=편입, O=이탈
    "20": "time",
}

CallbackType = Callable[[dict[str, Any]], Awaitable[None] | None]


def _ws_url() -> str:
    return _WS_URL_MOCK if config.USE_MOCK else _WS_URL_REAL


def _clean_code(raw: str | None) -> str:
    """'A005930', '005930_AL' 같은 표기를 6자리 종목코드로 정규화한다."""
    if not raw:
        return ""
    code = raw.strip().split("_")[0]
    if code[:1].isalpha():
        code = code[1:]
    return code


def _to_int(raw: str | None) -> int | None:
    """'+12,345' / '-1200' 형태의 수치를 절댓값 정수로 바꾼다.

    키움은 현재가·거래량 앞에 대비부호를 붙여 보내지만, 부호는 방향 표시일
    뿐 값 자체는 절댓값이다. 방향은 등락률(12)로 판단한다.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").lstrip("+-")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_change_pct(raw: str | None) -> float | None:
    """등락률(12)은 1000배 스케일로 수신되므로 실제 퍼센트로 환산한다."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"+", "-"}:
        return None
    try:
        return float(text) / 1000
    except ValueError:
        return None


class KiwoomConditionClient:
    """조건검색식 실시간 편입/이탈 이벤트를 수신하는 WebSocket 클라이언트.

    사용 예::

        client = KiwoomConditionClient(on_entry=handle_entry)
        await client.run_forever()
    """

    def __init__(
        self,
        on_entry: CallbackType | None = None,
        on_exit: CallbackType | None = None,
        condition_names: Iterable[str] | None = None,
        db: Any | None = None,
        notify_error: Callable[[str], Awaitable[None] | None] | None = None,
        notifier: SlackNotifier | None = None,
    ) -> None:
        self.on_entry = on_entry
        self.on_exit = on_exit
        # TradingDB 인스턴스. 주면 편입 신호를 signals 테이블에 적재한다.
        self.db = db
        # 연속 재연결 실패가 쌓였을 때 부를 알림 함수(예: Slack 전송).
        self.notify_error = notify_error
        # 편입 종목 탐지 알림용. notify_error를 따로 주지 않으면 이 notifier의 notify_error를 쓴다.
        self.notifier = notifier
        if self.notify_error is None and notifier is not None:
            self.notify_error = notifier.notify_error
        # 지정하지 않으면 config.CONDITION_IDS를, 그것도 비어 있으면 계좌에
        # 등록된 조건식 전부를 구독한다.
        names = condition_names if condition_names is not None else config.CONDITION_IDS
        self._wanted = [n.strip() for n in names if n and n.strip()]

        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        # seq -> 조건식 이름
        self._conditions: dict[str, str] = {}
        self._subscribed: list[str] = []
        self._running = False

    # ------------------------------------------------------------------
    # 송수신 기본기
    # ------------------------------------------------------------------
    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket이 연결되지 않았습니다")
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    async def _recv_skip_ping(self) -> dict[str, Any]:
        """PING을 처리하며 다음 업무 메시지를 반환한다.

        _IDLE_PROBE_SEC 동안 조용하면 PING을 던져보고, _PING_WAIT_SEC 안에
        아무 응답도 없으면 ConnectionClosedError를 올린다.
        """
        if self._ws is None:
            raise RuntimeError("WebSocket이 연결되지 않았습니다")

        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=_IDLE_PROBE_SEC)
            except asyncio.TimeoutError:
                logger.debug("%d초간 수신 없음 → PING 전송", _IDLE_PROBE_SEC)
                await self._send({"trnm": "PING"})
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=_PING_WAIT_SEC)
                except asyncio.TimeoutError:
                    logger.warning(
                        "PING 후 %d초간 응답이 없습니다. 연결이 끊긴 것으로 처리합니다.",
                        _PING_WAIT_SEC,
                    )
                    raise ConnectionClosedError(None, None) from None

            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("JSON이 아닌 메시지를 무시합니다: %r", raw)
                continue

            if msg.get("trnm") == "PING":
                # 키움 PING은 받은 본문을 그대로 돌려주는 것이 규약이다.
                await self._send(msg)
                continue

            return msg

    async def _request(self, payload: dict[str, Any], expect: str) -> dict[str, Any]:
        """요청을 보내고 같은 trnm의 응답이 올 때까지 기다린다.

        핸드셰이크 도중에도 REAL 메시지가 섞여 들어올 수 있으므로, 응답이
        아닌 메시지는 그때그때 처리한 뒤 계속 기다린다.
        """
        await self._send(payload)
        while True:
            msg = await self._recv_skip_ping()
            if msg.get("trnm") == expect:
                return msg
            await self._dispatch(msg)

    @staticmethod
    def _check_return_code(msg: dict[str, Any], what: str) -> None:
        return_code = msg.get("return_code")
        if return_code not in (None, 0, "0"):
            raise RuntimeError(
                f"{what} 실패: return_code={return_code} return_msg={msg.get('return_msg')}"
            )

    # ------------------------------------------------------------------
    # 연결 흐름: 연결 → LOGIN → CNSRLST → CNSRREQ
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        url = _ws_url()
        logger.info("조건검색 WebSocket 연결 중: %s", url)
        self._ws = await websockets.connect(
            url,
            ping_interval=None,  # 키움은 자체 PING 프로토콜을 쓴다
            ping_timeout=None,
            open_timeout=_CONNECT_TIMEOUT_SEC,
        )
        logger.info("조건검색 WebSocket 연결 완료")

    async def login(self) -> None:
        """LOGIN 메시지로 접근토큰을 인증한다."""
        access_token = await asyncio.to_thread(token.get_token)
        msg = await self._request({"trnm": "LOGIN", "token": access_token}, expect="LOGIN")
        self._check_return_code(msg, "WebSocket LOGIN")
        logger.info("WebSocket LOGIN 성공")

    async def fetch_condition_list(self) -> dict[str, str]:
        """CNSRLST로 등록된 조건식 목록(seq -> 이름)을 조회한다."""
        msg = await self._request({"trnm": "CNSRLST"}, expect="CNSRLST")
        self._check_return_code(msg, "조건식 목록 조회(CNSRLST)")

        conditions: dict[str, str] = {}
        for item in msg.get("data") or []:
            # 응답은 [["0", "장중외국인기관동반매수스윙"], ...] 형태다.
            if isinstance(item, dict):
                seq, name = item.get("seq"), item.get("name")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                seq, name = item[0], item[1]
            else:
                logger.warning("해석할 수 없는 조건식 항목을 건너뜁니다: %r", item)
                continue
            if seq is None:
                continue
            conditions[str(seq)] = str(name)

        self._conditions = conditions
        logger.info(
            "등록된 조건식 %d개: %s",
            len(conditions),
            ", ".join(f"[{s}] {n}" for s, n in conditions.items()) or "(없음)",
        )
        return conditions

    def _target_seqs(self) -> list[str]:
        """구독 대상 seq 목록. config.CONDITION_IDS는 이름 또는 seq로 적을 수 있다."""
        if not self._wanted:
            return list(self._conditions)

        by_name = {name: seq for seq, name in self._conditions.items()}
        seqs: list[str] = []
        for wanted in self._wanted:
            seq = by_name.get(wanted) or (wanted if wanted in self._conditions else None)
            if seq is None:
                logger.warning("조건식을 찾을 수 없어 건너뜁니다: %s", wanted)
                continue
            if seq not in seqs:
                seqs.append(seq)
        return seqs

    async def subscribe(self, seq: str) -> None:
        """CNSRREQ(search_type=1)로 조건식 실시간 편입/이탈을 구독한다."""
        msg = await self._request(
            {
                "trnm": "CNSRREQ",
                "seq": str(seq),
                "search_type": "1",  # 1 = 실시간 구독
                "stex_tp": "K",  # 거래소 구분: KRX
            },
            expect="CNSRREQ",
        )
        self._check_return_code(msg, f"조건식 실시간 구독(CNSRREQ seq={seq})")
        if seq not in self._subscribed:
            self._subscribed.append(seq)
        logger.info("조건식 실시간 구독: [%s] %s", seq, self._conditions.get(seq, "?"))

    async def subscribe_all(self) -> None:
        seqs = self._target_seqs()
        if not seqs:
            logger.warning("구독할 조건식이 없습니다. CONDITION_IDS 설정을 확인하세요.")
            return
        for seq in seqs:
            await self.subscribe(seq)

    # ------------------------------------------------------------------
    # 실시간 수신
    # ------------------------------------------------------------------
    async def _dispatch(self, msg: dict[str, Any]) -> None:
        trnm = msg.get("trnm")
        if trnm != "REAL":
            logger.debug("업무 외 메시지 수신: %s", msg)
            return

        for item in msg.get("data") or []:
            event = self._parse_event(item)
            if event is None:
                continue
            await self._fire(event)

    def _parse_event(self, item: dict[str, Any]) -> dict[str, Any] | None:
        values = item.get("values") or {}
        if not values:
            return None

        raw = {key: values.get(field_id) for field_id, key in _FIELD_MAP.items()}
        signal_type = (raw.get("signal_type") or "").strip().upper()
        if signal_type not in ("I", "O"):
            logger.debug("편입/이탈 구분(843)이 없어 무시합니다: %s", values)
            return None

        seq = str(item.get("item") or values.get("841") or "").strip()
        return {
            "stock_code": _clean_code(raw.get("stock_code")),
            "stock_name": (raw.get("stock_name") or "").strip(),
            "current_price": _to_int(raw.get("current_price")),
            "change_pct": _to_change_pct(raw.get("change_pct")),
            "volume": _to_int(raw.get("volume")),
            "signal_type": signal_type,
            "time": (raw.get("time") or "").strip(),
            "condition_seq": seq,
            "condition_name": self._conditions.get(seq, ""),
            "values": values,
        }

    def _save_signal(self, event: dict[str, Any]) -> str | None:
        """편입 신호를 signals 테이블에 적재하고 signal_id를 반환한다.

        TradingDB.save_signal()은 ThreadPoolExecutor에 작업을 넘기고 즉시
        signal_id를 돌려주므로(fire-and-forget) 수신 루프를 막지 않는다.
        DB가 죽어 있어도 WebSocket 수신은 계속돼야 하므로 예외는 삼킨다.
        """
        if self.db is None:
            return None
        try:
            return self.db.save_signal(Signal.from_event(event).to_row())
        except Exception:
            logger.error("signals 저장 실패 (수신은 계속합니다): %s", event, exc_info=True)
            return None

    async def _fire(self, event: dict[str, Any]) -> None:
        is_entry = event["signal_type"] == "I"
        logger.info(
            "조건식 %s: %s(%s) %s원 %s%% [%s]",
            "편입" if is_entry else "이탈",
            event["stock_name"] or "?",
            event["stock_code"],
            event["current_price"],
            event["change_pct"],
            event["condition_name"] or event["condition_seq"],
        )

        if is_entry:
            # 콜백보다 먼저 저장해, 이후 AI 진입 판단이 같은 행을
            # update_ai_decision(signal_id, ...)으로 갱신할 수 있게 한다.
            event["signal_id"] = self._save_signal(event)
            if self.notifier is not None:
                # 기다리지 않는다. Slack 응답 지연이 PING 응답·다음 신호 수신을 막으면 안 된다.
                try:
                    self.notifier.fire(self.notifier.notify_signal(Signal.from_event(event)))
                except Exception:
                    logger.error("종목 탐지 알림 예약 실패: %s", event, exc_info=True)

        callback = self.on_entry if is_entry else self.on_exit
        if callback is None:
            return
        try:
            result = callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # 콜백 예외가 수신 루프를 끊지 않도록 삼킨다.
            logger.error(
                "%s 콜백 처리 실패: %s", "on_entry" if is_entry else "on_exit", event, exc_info=True
            )

    async def listen(self) -> None:
        """연결이 끊길 때까지 실시간 메시지를 수신한다."""
        while self._running:
            msg = await self._recv_skip_ping()
            await self._dispatch(msg)

    # ------------------------------------------------------------------
    # 수명 주기
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """연결부터 구독까지 한 번에 수행한다."""
        await self.connect()
        self._running = True
        await self.login()
        await self.fetch_condition_list()
        await self.subscribe_all()

    async def run(self) -> None:
        """한 번 연결해 수신하며, 연결이 끊기면 예외를 올린다."""
        await self.start()
        await self.listen()

    async def run_forever(self) -> None:
        """연결이 끊기면 자동으로 재접속하며 계속 수신한다.

        재접속은 매번 connect → LOGIN → CNSRLST → CNSRREQ 전체 흐름을 다시
        수행하므로, 끊긴 사이 바뀐 조건식 목록도 그대로 반영된다.

        - 백오프: _BACKOFF_SEC(0, 5, 10, 20, 40, 60초)를 순서대로 사용하고
          마지막 값(60초)에서 고정된다.
        - _STABLE_CONNECTION_SEC(5분) 이상 붙어 있었다면 일시적 끊김으로 보고
          실패 카운터를 리셋해, 다음 재시도를 다시 즉시 수행한다.
        - 연속 _MAX_CONSECUTIVE_FAILURES(3)회 실패하면 notify_error로 알린다.
          복구될 때까지 알림이 쏟아지지 않도록, 한 번 알린 뒤에는 안정 연결로
          카운터가 리셋될 때까지 다시 알리지 않는다.
        """
        failures = 0
        notified = False

        while True:
            connected_at = time.monotonic()
            try:
                if failures:
                    logger.info("조건검색 재연결 시도 (연속 실패 %d회)", failures)
                # connect → LOGIN → CNSRLST → CNSRREQ 전체 흐름을 다시 수행한다.
                await self.start()
                if failures:
                    logger.info("조건검색 재연결 성공 (연속 실패 %d회 만에 복구)", failures)
                # 구독이 끝난 시점부터 연결 유지 시간을 센다.
                connected_at = time.monotonic()
                await self.listen()
                reason = "수신 루프가 종료되었습니다"
            except asyncio.CancelledError:
                await self.close()
                raise
            except ConnectionClosed as exc:
                reason = f"WebSocket 연결이 끊겼습니다 ({exc.__class__.__name__})"
                logger.warning("%s", reason)
            except Exception as exc:
                reason = f"조건검색 클라이언트 오류: {exc}"
                logger.error("조건검색 클라이언트 오류", exc_info=True)
            finally:
                await self.close()

            uptime = time.monotonic() - connected_at
            if uptime >= _STABLE_CONNECTION_SEC:
                if failures:
                    logger.info(
                        "직전 연결이 %.0f분간 유지돼 재시도 카운터를 리셋합니다.", uptime / 60
                    )
                failures = 0
                notified = False

            failures += 1
            delay = _BACKOFF_SEC[min(failures - 1, len(_BACKOFF_SEC) - 1)]

            if failures >= _MAX_CONSECUTIVE_FAILURES and not notified:
                notified = True
                await self._fire_notify_error(
                    f"조건검색 WebSocket 재연결에 연속 {failures}회 실패했습니다. "
                    f"마지막 원인: {reason}"
                )

            logger.warning(
                "재연결 대기 %d초 (연속 실패 %d회, 직전 연결 유지 %.0f초)",
                delay,
                failures,
                uptime,
            )
            # delay가 0이어도 호출한다. 즉시 재시도라도 이벤트 루프에 한 번은
            # 양보해야 다른 태스크가 굶지 않는다.
            await asyncio.sleep(delay)

    async def _fire_notify_error(self, message: str) -> None:
        """알림 콜백을 호출한다. 알림 실패가 재연결 루프를 막아서는 안 된다."""
        logger.error("%s", message)
        if self.notify_error is None:
            return
        try:
            result = self.notify_error(message)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.error("notify_error 호출 실패", exc_info=True)

    async def close(self) -> None:
        self._running = False
        self._subscribed.clear()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("WebSocket 종료 중 오류", exc_info=True)
            finally:
                self._ws = None
