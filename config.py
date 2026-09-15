"""환경변수 로딩 및 전략 파라미터 관리 모듈.

다른 모듈에서는 `import config` 후 `config.APP_KEY`, `config.TRADE_AMOUNT`
처럼 모듈 수준 상수로 직접 접근한다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def _get_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _get_int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


# ---------------------------------------------------------------------------
# Kiwoom REST API
# ---------------------------------------------------------------------------
APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
SECRET_KEY = os.getenv("KIWOOM_SECRET_KEY", "")
ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO", "")
USE_MOCK = _get_bool("KIWOOM_USE_MOCK", "true")
BASE_URL = "https://mockapi.kiwoom.com" if USE_MOCK else "https://api.kiwoom.com"

# ---------------------------------------------------------------------------
# Claude Code CLI / AI 모델 (진입 판단)
# ---------------------------------------------------------------------------
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
ENTRY_MODEL = os.getenv("ENTRY_MODEL", "claude-sonnet-4-6")
ENTRY_AGENT_TIMEOUT_SEC = _get_float("ENTRY_AGENT_TIMEOUT_SEC", "180.0")

# ---------------------------------------------------------------------------
# Slack 알림
# ---------------------------------------------------------------------------
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_CONDITION = os.getenv("SLACK_CHANNEL_CONDITION", "#stock-condition")
SLACK_CHANNEL_HEALTH = os.getenv("SLACK_CHANNEL_HEALTH", "#stock-health")

# ---------------------------------------------------------------------------
# 매매 설정
# ---------------------------------------------------------------------------
TRADE_AMOUNT = _get_int("TRADE_AMOUNT", "500000")
MAX_POSITIONS = _get_int("MAX_POSITIONS", "3")
MAX_DAILY_LOSS = _get_int("MAX_DAILY_LOSS", "300000")

# ---------------------------------------------------------------------------
# 포지션 가드
# ---------------------------------------------------------------------------
GUARD_INTERVAL_SEC = _get_int("GUARD_INTERVAL_SEC", "5")

# ---------------------------------------------------------------------------
# 분할매수/매도
# ---------------------------------------------------------------------------
SPLIT_TRADE_ENABLED = _get_bool("SPLIT_TRADE_ENABLED", "true")
TRANCHE1_BUY_RATIO = _get_float("TRANCHE1_BUY_RATIO", "0.6")
TRANCHE2_PULLBACK_PCT = _get_float("TRANCHE2_PULLBACK_PCT", "-0.5")
TRANCHE2_MOMENTUM_PCT = _get_float("TRANCHE2_MOMENTUM_PCT", "0.3")
TRANCHE2_TIMEOUT_SEC = _get_int("TRANCHE2_TIMEOUT_SEC", "180")
QUICK_TARGET_PCT = _get_float("QUICK_TARGET_PCT", "1.5")
QUICK_TARGET_SELL_RATIO = _get_float("QUICK_TARGET_SELL_RATIO", "0.4")

# ---------------------------------------------------------------------------
# 진입/청산 파라미터 (STRATEGY_MAP 미등록 조건식에 쓰이는 기본 전략)
# ---------------------------------------------------------------------------
TARGET_PCT = _get_float("TARGET_PCT", "2.5")
STOP_LOSS_PCT = _get_float("STOP_LOSS_PCT", "-1.5")
MAX_HOLD_MINUTES = _get_int("MAX_HOLD_MINUTES", "15")
MIN_CONFIDENCE = _get_float("MIN_CONFIDENCE", "0.70")
CHASE_BUY_LIMIT_PCT = _get_float("CHASE_BUY_LIMIT_PCT", "1.0")
SKIP_BEFORE_MIN = _get_int("SKIP_BEFORE_MIN", "5")
SKIP_AFTER_HOUR = _get_int("SKIP_AFTER_HOUR", "14")

# ---------------------------------------------------------------------------
# 트레일링 스탑
# ---------------------------------------------------------------------------
TRAILING_ACTIVATE_PCT = _get_float("TRAILING_ACTIVATE_PCT", "1.0")
TRAILING_DROP_PCT = _get_float("TRAILING_DROP_PCT", "0.7")

# ---------------------------------------------------------------------------
# 조건식 모니터링
# ---------------------------------------------------------------------------
CONDITION_IDS = [c.strip() for c in os.getenv("CONDITION_IDS", "").split(",") if c.strip()]


@dataclass(frozen=True)
class StrategyParams:
    target_pct: float
    stop_pct: float
    max_hold_min: int
    skip_after_hour: int
    style: str
    trailing_activate_pct: float | None = None
    trailing_drop_pct: float | None = None


_DEFAULT_STRATEGY = StrategyParams(
    target_pct=TARGET_PCT,
    stop_pct=STOP_LOSS_PCT,
    max_hold_min=MAX_HOLD_MINUTES,
    skip_after_hour=SKIP_AFTER_HOUR,
    style="default",
)

# 조건식 이름 -> 전략 파라미터. Part 05에서 조건식을 만들 때마다 항목을 추가한다.
STRATEGY_MAP: dict[str, StrategyParams] = {
    "급등초입포착": StrategyParams(
        target_pct=3.0,
        stop_pct=-1.2,
        max_hold_min=10,
        skip_after_hour=14,
        style="momentum",
        trailing_activate_pct=1.2,
        trailing_drop_pct=0.6,
    ),
}


def get_condition_strategy(condition_name: str) -> StrategyParams:
    return STRATEGY_MAP.get(condition_name, _DEFAULT_STRATEGY)


def validate() -> None:
    """필수 환경변수와 외부 실행 파일 경로를 검증한다."""
    missing: list[str] = []
    if not APP_KEY:
        missing.append("KIWOOM_APP_KEY")
    if not SECRET_KEY:
        missing.append("KIWOOM_SECRET_KEY")
    if missing:
        raise RuntimeError(f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing)}")

    if shutil.which(CLAUDE_BIN) is None and not os.path.isfile(CLAUDE_BIN):
        raise RuntimeError(
            f"Claude Code CLI 실행 파일을 찾을 수 없습니다: {CLAUDE_BIN} "
            "(PATH에 claude가 있는지, 또는 CLAUDE_BIN 환경변수를 확인하세요)"
        )
