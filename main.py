"""엔트리포인트: QuantTradingSystem 오케스트레이터를 구동한다."""

from __future__ import annotations

import asyncio

import config


async def main() -> None:
    config.validate()
    # TODO: QuantTradingSystem 오케스트레이터 초기화 및 실행


if __name__ == "__main__":
    asyncio.run(main())
