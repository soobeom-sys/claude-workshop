"""조건검색 편입 신호에 대해 Claude Code CLI(quant-trader 에이전트)로 진입 여부를 판단한다.

`.claude/agents/quant-trader.md`에 정의된 에이전트를 subprocess로 호출하며, 에이전트는
kiwoom MCP 도구로 시세/호가/잔고를 조회한 뒤 매수로 결정하면 1차 트랜치 매수 주문까지
직접 실행한다. 이 모듈은 에이전트의 텍스트 응답을 파싱해 Position을 만들어 돌려줄 뿐이며,
청산 판단은 하지 않는다(청산은 trading/의 Position Guard 담당).

notifier(SlackNotifier)를 넘기면 패스 사유를 notify_skip으로, AI 호출 실패·미추적 주문 같은
시스템 문제를 notify_error로 알린다. 매수 체결 알림(notify_entry)은 목표가 매도 주문까지
걸린 뒤 main.py가 보낸다. 알림은 모두 fire()로 백그라운드 전송해 판단 흐름을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import config as cfg
from condition.models import Signal
from config import StrategyParams
from trading.models import Position
from trading.tranche import tranche1_amount

if TYPE_CHECKING:
    from notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_AGENT_NAME = "quant-trader"
_EXCHANGE = "KRX"

# .mcp.json에 등록된 키움 MCP 서버 이름. Claude Code는 MCP 도구를
# mcp__<서버명>__<도구명> 형식으로 식별하므로 --allowedTools에도 이 형식으로 넘긴다.
_MCP_SERVER = "kiwoom_mcp"

# 진입 에이전트가 사용할 수 있는 MCP 도구 화이트리스트.
# 매도/정정/취소 주문 도구와 임의 API를 호출할 수 있는 kiwoom_api는 제외한다
# (kiwoom_api로는 kt10001 매도 주문도 낼 수 있어 화이트리스트를 우회하게 된다).
ALLOWED_MCP_TOOLS: tuple[str, ...] = (
    # 조회
    "get_stock_info",
    "get_orderbook",
    "get_minute_chart",
    "get_daily_chart",
    "get_balance",
    "get_deposit",
    "get_open_orders",
    # 주문
    "place_buy_order",
)

# 화이트리스트와 별개로 명시적으로 막아 두는 도구 (settings.json 등에서 허용돼 있어도 차단).
_DISALLOWED_MCP_TOOLS: tuple[str, ...] = (
    "place_sell_order",
    "modify_order",
    "cancel_order",
    "kiwoom_api",
    "subscribe_condition_realtime",
)

_MAX_CONCURRENT_AGENTS = 2
_agent_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_AGENTS)

# 조건식 이름 -> AI 검증 포인트. quant-trader.md의 "조건식별 검증 가이드"와 동일하게 유지하며,
# config.STRATEGY_MAP에 조건식을 추가할 때마다 이곳과 quant-trader.md에 함께 추가한다.
_VERIFICATION_GUIDES: dict[str, str] = {
    "장중외국인기관동반매수스윙": (
        "이 조건식의 의도: 장 초반 외국인과 기관이 동시에 순매수하는 중형 이상 종목 중, "
        "주가(시가 위 유지)와 거래량(전일 대비 급증)이 실제로 반응하는 종목을 잡아 "
        "당일 스윙(수 시간 보유)으로 +2.5%를 노린다.\n"
        "1. 수급 지속성: 분봉 거래량 급증이 1~2개 봉의 일회성 스파이크가 아니라 최근 5~10분간 "
        "꾸준히 이어지는지 확인. 직전 봉들의 거래량이 급감 중이면 패스.\n"
        "2. 시가 지지: 현재가가 당일 시가 위에서 버티는지, 최근 분봉 저점이 시가 부근에서 지지되며 "
        "올라가는지(저점 상승) 확인. 시가를 반복 이탈하면 동반매수 효과가 약한 것으로 본다.\n"
        "3. 호가 우위: 총 매수잔량이 매도잔량 대비 열세가 아닌지, 상단 매도벽이 얇아지거나 "
        "소화되는지 확인. 1~3호가에 두꺼운 매도벽이 버티면 패스.\n"
        "4. 목표 여력: 현재가 +2.5% 위에 당일 고가·전일 고가 등 뚜렷한 저항이 바로 있는지 확인. "
        "당일 등락률이 이미 +8% 이상이면 스윙 여력 부족으로 패스."
    ),
}

_DEFAULT_VERIFICATION_GUIDE = (
    "미등록 조건식이므로 의도를 알 수 없다. 기본 추세, 거래량, 호가창을 종합 검증하여 "
    "보수적으로 판단한다.\n"
    "1. 최근 분봉 거래량이 증가 추세이며 가격이 고점·저점을 함께 높이고 있는지\n"
    "2. 매수잔량이 매도잔량 대비 열세가 아닌지, 스프레드가 과도하게 벌어지지 않았는지\n"
    "3. 목표가까지 뚜렷한 저항이 없는지\n"
    "판단이 애매하면 패스한다."
)

_DECISION_RE = re.compile(r"\[결정\]\s*(매수|패스)")
_ORDER_RE = re.compile(r"\[주문\]\s*수량\s*=\s*([\d,]+)\s*주?\s*주문번호\s*=\s*([0-9A-Za-z\-]+)")
_PRICE_RE = re.compile(r"\[매입가\]\s*([\d,]+)")
_REASON_RE = re.compile(r"\[이유\]\s*(.+?)(?=\n\[|\Z)", re.DOTALL)


@dataclass(frozen=True)
class _EntryResult:
    """quant-trader 응답 파싱 결과. 매수가 아니면 수량/주문번호/매입가는 None이다."""

    decision: str
    quantity: int | None
    order_no: str | None
    entry_price: int | None
    reason: str


def _mcp_tool_names(tools: tuple[str, ...]) -> str:
    return ",".join(f"mcp__{_MCP_SERVER}__{name}" for name in tools)


def _build_entry_prompt(signal: Signal, strategy: StrategyParams) -> str:
    now = dt.datetime.now(_KST)
    guide = _VERIFICATION_GUIDES.get(signal.condition_name, _DEFAULT_VERIFICATION_GUIDE)
    amount = tranche1_amount()
    trailing = (
        f"활성 +{strategy.trailing_activate_pct}% / 고점 대비 -{strategy.trailing_drop_pct}%"
        if strategy.trailing_activate_pct is not None and strategy.trailing_drop_pct is not None
        else f"활성 +{cfg.TRAILING_ACTIVATE_PCT}% / 고점 대비 -{cfg.TRAILING_DROP_PCT}% (기본값)"
    )

    return f"""\
현재 시각: {now:%Y-%m-%d %H:%M:%S} (KST, {"월화수목금토일"[now.weekday()]}요일)

## 신호
- 종목코드: {signal.stock_code}
- 종목명: {signal.stock_name or "(미상)"}
- 조건식: {signal.condition_name}
- 편입 시각: {signal.signal_time or "(미상)"}
- 편입 시 가격/등락률: {signal.signal_price or "-"}원 / {signal.signal_change_pct if signal.signal_change_pct is not None else "-"}%

## 전략 파라미터 (청산은 시스템이 처리하므로 참고용)
- 목표 수익률: +{strategy.target_pct}%
- 손절률: {strategy.stop_pct}%
- 최대 보유 시간: {strategy.max_hold_min}분
- 진입 금지 시각: {strategy.skip_after_hour}시 이후, 장 시작 후 {cfg.SKIP_BEFORE_MIN}분 이내
- 트레일링 스탑: {trailing}
- 추격매수 제한: 당일 고가 대비 -{cfg.CHASE_BUY_LIMIT_PCT}% 이내에서만 매수
- 최대 보유 종목 수: {cfg.MAX_POSITIONS}

## 1차 트랜치 매수 금액
- {amount:,}원 (이 금액 이내에서 살 수 있는 최대 정수 수량만 주문. 2차 트랜치는 시스템이 처리)

## 조건식별 검증 가이드
{guide}

## 사용할 MCP 도구 (이 환경의 실제 도구 이름)
- 종목 기본 정보(현재가·시고저·등락률·거래량·시총): get_stock_info(stk_cd)
- 호가창(매수/매도 1~10호가 잔량): get_orderbook(stk_cd)
- 분봉 차트: get_minute_chart(stk_cd, tic_scope="1" 또는 "3", upd_stkpc_tp="1")
- 일봉 차트(전일 고가 등 저항 확인): get_daily_chart(stk_cd, base_dt=오늘 YYYYMMDD, upd_stkpc_tp="1")
- 계좌 잔고·보유 종목: get_balance(qry_tp="1", dmst_stex_tp="{_EXCHANGE}")
- 미체결 주문: get_open_orders
- 매수 주문: place_buy_order(dmst_stex_tp="{_EXCHANGE}", stk_cd, ord_qty, trde_tp="3")  ※ 시장가

## 단계별 지시
1. 조회: 사전 제약(장 시간, 보유 종목 수, 중복 보유)을 먼저 확인하고, 하나라도 위반하면
   추가 조회 없이 즉시 패스한다. 통과하면 기본 정보·호가·분봉·일봉을 조회한다.
2. 분석: 추격매수 제한과 위 검증 가이드의 항목을 수치로 하나씩 점검한다.
3. 판단: 모든 항목을 통과하면 매수, 하나라도 명확히 불리하거나 데이터가 부족하면 패스.
4. 주문: 매수로 결정한 경우에만 place_buy_order를 1회 호출하고, 응답의 주문번호를 확인한다.
   체결가를 모르면 주문 직전 현재가를 [매입가]로 쓴다. 주문이 거부/실패하면 패스로 보고한다.
   매도·정정·취소 주문은 절대 하지 않는다.

출력은 quant-trader 지침의 출력 형식([결정]/[종목]/[이유]/[주문]/[매입가])만 따른다.
"""


def _parse_int(text: str) -> int:
    return int(text.replace(",", ""))


def _parse_entry_result(text: str) -> _EntryResult | None:
    """quant-trader 응답을 파싱한다. [결정] 태그를 찾지 못하면 None."""
    decision_match = _DECISION_RE.search(text)
    if decision_match is None:
        return None

    reason_match = _REASON_RE.search(text)
    reason = reason_match.group(1).strip() if reason_match else ""
    decision = decision_match.group(1)

    if decision != "매수":
        return _EntryResult(decision=decision, quantity=None, order_no=None, entry_price=None, reason=reason)

    order_match = _ORDER_RE.search(text)
    price_match = _PRICE_RE.search(text)
    return _EntryResult(
        decision=decision,
        quantity=_parse_int(order_match.group(1)) if order_match else None,
        order_no=order_match.group(2) if order_match else None,
        entry_price=_parse_int(price_match.group(1)) if price_match else None,
        reason=reason,
    )


def _resolve_claude_bin() -> str:
    # Windows의 npm 설치본은 claude.cmd 셸이므로 which로 확장자까지 포함한 경로를 얻는다.
    resolved = shutil.which(cfg.CLAUDE_BIN)
    if resolved:
        return resolved
    if os.path.isfile(cfg.CLAUDE_BIN):
        return cfg.CLAUDE_BIN
    raise RuntimeError(
        f"Claude Code CLI 실행 파일을 찾을 수 없습니다: {cfg.CLAUDE_BIN} "
        "(PATH에 claude가 있는지, 또는 CLAUDE_BIN 환경변수를 확인하세요)"
    )


async def _run_entry_agent(prompt: str) -> tuple[int | None, str, str]:
    """CLI를 실행해 (returncode, stdout, stderr)를 돌려준다. 타임아웃 시 TimeoutError."""
    allowed = _mcp_tool_names(ALLOWED_MCP_TOOLS)
    disallowed = _mcp_tool_names(_DISALLOWED_MCP_TOOLS)

    # 프롬프트는 argv가 아닌 stdin으로 넘긴다. Windows에서 claude.cmd를 거치면
    # cmd.exe가 인자의 줄바꿈 이후를 잘라 먹어 여러 줄 프롬프트가 깨지기 때문이다.
    proc = await asyncio.create_subprocess_exec(
        _resolve_claude_bin(),
        "--print",
        f"--model={cfg.ENTRY_MODEL}",
        f"--agent={_AGENT_NAME}",
        f"--allowedTools={allowed}",
        f"--disallowedTools={disallowed}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=cfg.ENTRY_AGENT_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    return (
        proc.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _notify_skip(notifier: SlackNotifier | None, signal: Signal, reason: str) -> None:
    if notifier is not None:
        notifier.fire(notifier.notify_skip(signal, reason))


def _notify_error(notifier: SlackNotifier | None, msg: str) -> None:
    if notifier is not None:
        notifier.fire(notifier.notify_error(msg))


async def call_entry_agent(
    signal: Signal, strategy: StrategyParams, notifier: SlackNotifier | None = None
) -> tuple[bool, Position | None, str]:
    """신호 1건에 대해 진입 에이전트를 호출한다.

    Returns:
        (bought, position, ai_summary). 매수 주문이 확인되면 bought=True와 Position을,
        패스·타임아웃·실행 실패·파싱 실패는 모두 (False, None, 사유)를 돌려준다.
    """
    label = f"{signal.stock_code} {signal.stock_name} [{signal.condition_name}]"
    prompt = _build_entry_prompt(signal, strategy)

    async with _agent_semaphore:
        logger.info("진입 에이전트 호출: %s", label)
        try:
            returncode, stdout, stderr = await _run_entry_agent(prompt)
        except asyncio.TimeoutError:
            logger.warning("진입 에이전트 타임아웃(%.0f초) → 패스: %s", cfg.ENTRY_AGENT_TIMEOUT_SEC, label)
            reason = f"AI 판단 타임아웃({cfg.ENTRY_AGENT_TIMEOUT_SEC:.0f}초)으로 패스"
            _notify_skip(notifier, signal, reason)
            return False, None, reason
        except Exception as exc:
            logger.exception("진입 에이전트 실행 실패 → 패스: %s", label)
            reason = f"AI 호출 실패로 패스: {exc}"
            _notify_skip(notifier, signal, reason)
            _notify_error(notifier, f"{label}: 진입 에이전트 실행 실패 — {exc}")
            return False, None, reason

    if stderr.strip():
        logger.warning("진입 에이전트 stderr (%s):\n%s", label, stderr.strip())
    if returncode != 0:
        logger.error("진입 에이전트 비정상 종료(exit=%s) → 패스: %s\n%s", returncode, label, stdout.strip())
        reason = f"AI 호출 비정상 종료(exit={returncode})로 패스"
        _notify_skip(notifier, signal, reason)
        _notify_error(notifier, f"{label}: 진입 에이전트 비정상 종료(exit={returncode})\n{stderr.strip()[-500:]}")
        return False, None, reason

    ai_summary = stdout.strip()
    result = _parse_entry_result(ai_summary)
    if result is None:
        logger.error("진입 에이전트 응답 파싱 실패([결정] 없음) → 패스: %s\n%s", label, ai_summary)
        _notify_skip(notifier, signal, "AI 응답에서 [결정]을 찾지 못해 패스")
        _notify_error(notifier, f"{label}: 진입 에이전트 응답 파싱 실패([결정] 없음)")
        return False, None, ai_summary or "AI 응답이 비어 있어 패스"

    if result.decision != "매수":
        logger.info("진입 패스: %s — %s", label, result.reason)
        _notify_skip(notifier, signal, result.reason or ai_summary)
        return False, None, ai_summary

    if not result.quantity or not result.order_no or not result.entry_price:
        # 주문은 나갔을 수 있으나 포지션을 만들 수 없는 상태다. Guard가 추적하지 못하므로
        # 사람이 계좌를 직접 확인해야 한다.
        logger.error(
            "매수 결정이지만 [주문]/[매입가] 파싱 실패 — 미추적 주문 가능성, 계좌 확인 필요: %s "
            "(수량=%s 주문번호=%s 매입가=%s)\n%s",
            label, result.quantity, result.order_no, result.entry_price, ai_summary,
        )
        _notify_error(
            notifier,
            f"{label}: AI가 매수로 결정했지만 주문 정보를 파싱하지 못했습니다. "
            "미추적 주문일 수 있으니 계좌를 직접 확인하세요.",
        )
        return False, None, ai_summary

    position = Position(
        stock_code=signal.stock_code,
        stock_name=signal.stock_name,
        entry_price=result.entry_price,
        quantity=result.quantity,
        entry_time=dt.datetime.now(_KST),
        condition_name=signal.condition_name,
        strategy=strategy,
        exchange=_EXCHANGE,
        order_no=result.order_no,
        high_water_mark=result.entry_price,
    )
    logger.info(
        "진입 매수: %s %d주 @ %s원 (주문번호 %s)",
        label, position.quantity, f"{position.entry_price:,}", position.order_no,
    )
    return True, position, ai_summary
