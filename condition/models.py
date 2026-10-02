"""condition/ 모듈이 공유하는 불변 데이터 모델."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Signal:
    """조건식에 종목이 편입될 때 수신한 신호.

    signal_time은 키움이 보내주는 원본 표기(HHMMSS, KST)를 그대로 보관한다.
    signals 테이블의 signal_time은 timestamptz이므로 저장 시점에
    to_row()가 '오늘 날짜 + 이 시각'으로 환산한다.
    """

    stock_code: str
    stock_name: str
    condition_name: str
    signal_price: int | None
    signal_change_pct: float | None
    volume: int | None
    signal_time: str

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Signal:
        """KiwoomConditionClient가 파싱한 실시간 이벤트에서 Signal을 만든다."""
        # condition_name은 NOT NULL이다. CNSRLST 목록에 없는 seq가 오면
        # 이름을 알 수 없으므로 seq로 식별 가능한 값을 채워 넣는다.
        seq = event.get("condition_seq") or "?"
        condition_name = event.get("condition_name") or f"seq-{seq}"
        return cls(
            stock_code=event.get("stock_code") or "",
            stock_name=event.get("stock_name") or "",
            condition_name=condition_name,
            signal_price=event.get("current_price"),
            signal_change_pct=event.get("change_pct"),
            volume=event.get("volume"),
            signal_time=event.get("time") or "",
        )

    def to_row(self) -> dict[str, Any]:
        """signals 테이블에 INSERT할 행으로 변환한다."""
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name or None,
            "condition_name": self.condition_name,
            "signal_price": self.signal_price,
            "signal_change_pct": self.signal_change_pct,
            "volume": self.volume,
            "signal_time": _to_timestamptz(self.signal_time),
        }


def _to_timestamptz(hhmmss: str, *, now: dt.datetime | None = None) -> str:
    """'093015'(KST)를 오늘 날짜와 합쳐 ISO timestamptz 문자열로 만든다.

    값이 비었거나 형식이 깨졌으면 수신 시각(현재 시각)으로 대체한다.
    signals.signal_time이 NOT NULL이라 비워 둘 수 없기 때문이다.
    """
    now = now or dt.datetime.now(_KST)
    text = (hhmmss or "").strip()

    if len(text) >= 6 and text[:6].isdigit():
        try:
            clock = dt.time(int(text[0:2]), int(text[2:4]), int(text[4:6]))
        except ValueError:
            return now.isoformat()
        return dt.datetime.combine(now.date(), clock, tzinfo=_KST).isoformat()

    return now.isoformat()
