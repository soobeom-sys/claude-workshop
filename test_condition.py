"""condition/client.py 수동 점검 스크립트.

조건검색 WebSocket에 접속해 편입/이탈 신호를 10초간 콘솔에 출력하고 종료한다.
매매는 하지 않으며, 연결·인증·구독·수신 경로가 살아 있는지 확인하는 용도다.

    uv run python test_condition.py

장 시간(09:00~15:30)이 아니면 신호가 한 건도 오지 않는 것이 정상이다.
그 경우에도 접속/구독 로그까지는 정상적으로 찍혀야 한다.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import config
from condition.client import KiwoomConditionClient

LISTEN_SEC = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)


def _fmt(event: dict[str, Any]) -> str:
    """수신 이벤트를 한 줄로 요약한다. 값이 없는 필드는 생략한다."""
    parts = [f"{event['stock_name'] or '?'}({event['stock_code']})"]

    price = event["current_price"]
    if price is not None:
        parts.append(f"{price:,}원")

    pct = event["change_pct"]
    if pct is not None:
        parts.append(f"{pct:+.2f}%")

    volume = event["volume"]
    if volume is not None:
        parts.append(f"거래량 {volume:,}")

    parts.append(f"[{event['condition_name'] or event['condition_seq']}]")
    if event["time"]:
        parts.append(event["time"])
    return " ".join(parts)


def on_entry(event: dict[str, Any]) -> None:
    print(f"[편입] {_fmt(event)}")


def on_exit(event: dict[str, Any]) -> None:
    print(f"[이탈] {_fmt(event)}")


async def main() -> None:
    client = KiwoomConditionClient(on_entry=on_entry, on_exit=on_exit)
    try:
        await client.start()
        print(f"\n구독 완료 → {LISTEN_SEC}초간 신호를 수신합니다. (Ctrl+C로 중단)\n")
        try:
            await asyncio.wait_for(client.listen(), timeout=LISTEN_SEC)
        except asyncio.TimeoutError:
            print(f"\n{LISTEN_SEC}초 경과, 수신을 종료합니다.")
    finally:
        await client.close()
        print("연결을 정리했습니다.")


if __name__ == "__main__":
    # Windows 콘솔에서도 종목명이 깨지지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    env = "모의투자" if config.USE_MOCK else "실계좌"
    print(f"조건검색 수신 테스트 ({env}) — 구독 대상: {config.CONDITION_IDS or '등록된 전체'}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n사용자 중단")
