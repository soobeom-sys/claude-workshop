"""보유 포지션을 .positions.json에 영구 저장한다.

프로그램이 재시작되어도 Position Guard가 추적하던 포지션을 복구하기 위한 모듈이다.
Position은 frozen dataclass이므로 갱신은 항상 dataclasses.replace()로 새 인스턴스를 만든다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from config import StrategyParams
from trading.models import Position, SellOrder

POSITIONS_FILE = Path(__file__).resolve().parent.parent / ".positions.json"


def _to_dict(position: Position) -> dict[str, Any]:
    data = dataclasses.asdict(position)
    data["entry_time"] = position.entry_time.isoformat()
    data["last_buy_time"] = position.last_buy_time.isoformat() if position.last_buy_time else None
    return data


def _from_dict(data: dict[str, Any]) -> Position:
    # 필드가 추가되기 전에 저장된 파일도 읽을 수 있도록 새 필드는 기본값에 맡긴다.
    last_buy_time = data.get("last_buy_time")
    return Position(
        **{
            **data,
            "entry_time": dt.datetime.fromisoformat(data["entry_time"]),
            "strategy": StrategyParams(**data["strategy"]),
            "sell_orders": tuple(SellOrder(**o) for o in data.get("sell_orders") or ()),
            "last_buy_time": dt.datetime.fromisoformat(last_buy_time) if last_buy_time else None,
        }
    )


def load() -> list[Position]:
    """파일에서 포지션 목록을 읽는다. 파일이 없으면 빈 리스트를 반환한다."""
    if not POSITIONS_FILE.exists():
        return []
    raw = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    return [_from_dict(item) for item in raw]


def save(positions: list[Position]) -> None:
    """포지션 목록을 파일에 쓴다.

    쓰기 도중 프로세스가 죽어도 기존 파일이 깨지지 않도록 임시 파일에 먼저 쓰고 교체한다.
    """
    payload = json.dumps([_to_dict(p) for p in positions], ensure_ascii=False, indent=2)
    tmp_path = POSITIONS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, POSITIONS_FILE)


def add(position: Position) -> None:
    """포지션을 추가하고 저장한다. 같은 종목이 이미 있으면 새 포지션으로 교체한다."""
    positions = [p for p in load() if p.stock_code != position.stock_code]
    save([*positions, position])


def remove(stock_code: str) -> None:
    """해당 종목의 포지션을 제거하고 저장한다."""
    save([p for p in load() if p.stock_code != stock_code])


def update_high_water_mark(stock_code: str, new_high: int) -> None:
    """해당 종목의 최고가를 갱신하고 저장한다.

    new_high가 기존 최고가 이하이거나 종목이 없으면 아무것도 하지 않는다.
    """
    positions = load()
    updated = [
        dataclasses.replace(p, high_water_mark=new_high)
        if p.stock_code == stock_code and new_high > p.high_water_mark
        else p
        for p in positions
    ]
    if updated != positions:
        save(updated)
