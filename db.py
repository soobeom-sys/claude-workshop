"""Supabase(Postgres) 저장 계층.

트레이딩 루프는 초 단위로 반응해야 하므로, 모든 쓰기 작업은
ThreadPoolExecutor로 백그라운드 실행되는 fire-and-forget 패턴을 따른다.
DB 오류가 매매 로직(주문/청산)을 절대 막아서는 안 되므로, 쓰기 메서드는
예외를 삼키고 logging.error()로만 기록한다.

config.DB_ENABLED=false면 클라이언트를 만들지 않고 모든 메서드가 즉시
no-op으로 리턴한다.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from supabase import Client, create_client

import config
from trading.models import ExitDecision

logger = logging.getLogger(__name__)


class TradingDB:
    def __init__(self) -> None:
        self._enabled = config.DB_ENABLED
        self._client: Client | None = None
        self._executor: ThreadPoolExecutor | None = None

        if self._enabled:
            self._client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            # 워커 1개로 쓰기 순서를 보장한다. trades INSERT보다 sell_orders INSERT가 먼저
            # 실행되면 FK 위반이 나고, signals INSERT보다 UPDATE가 먼저 실행되면 갱신이 유실된다.
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tradingdb")

    def close(self) -> None:
        """대기 중인 쓰기 작업을 모두 끝낸 뒤 워커를 정리한다. 종료 시 한 번 호출한다."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------
    def save_signal(self, data: dict[str, Any]) -> str | None:
        """signals 테이블에 INSERT. 생성된 signal_id를 즉시 반환한다.

        condition_name에 해당하는 조건식이 hts_conditions에 없으면 함께 등록한다.
        """
        if not self._enabled:
            return None

        signal_id = str(data.get("id") or uuid.uuid4())
        self._executor.submit(self._save_signal_sync, signal_id, data)
        return signal_id

    def _save_signal_sync(self, signal_id: str, data: dict[str, Any]) -> None:
        try:
            row = {**data, "id": signal_id}

            condition_name = row.get("condition_name")
            if condition_name and row.get("condition_id") is None:
                row["condition_id"] = self._ensure_condition_sync(condition_name)

            self._client.table("signals").insert(row).execute()
        except Exception:
            logger.error("save_signal 실패: signal_id=%s", signal_id, exc_info=True)

    def _ensure_condition_sync(self, condition_name: str) -> int | None:
        """hts_conditions에 조건식이 없으면 등록하고 id를 반환한다."""
        try:
            self._client.table("hts_conditions").upsert(
                {"name": condition_name}, on_conflict="name", ignore_duplicates=True
            ).execute()
            resp = (
                self._client.table("hts_conditions")
                .select("id")
                .eq("name", condition_name)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return rows[0]["id"] if rows else None
        except Exception:
            logger.error("조건식 자동등록 실패: condition_name=%s", condition_name, exc_info=True)
            return None

    def update_ai_decision(
        self,
        signal_id: str,
        ai_action: str,
        ai_summary: str,
        ai_model: str,
        ai_latency_ms: int,
    ) -> None:
        if not self._enabled:
            return
        self._executor.submit(
            self._update_ai_decision_sync, signal_id, ai_action, ai_summary, ai_model, ai_latency_ms
        )

    def _update_ai_decision_sync(
        self,
        signal_id: str,
        ai_action: str,
        ai_summary: str,
        ai_model: str,
        ai_latency_ms: int,
    ) -> None:
        try:
            self._client.table("signals").update(
                {
                    "ai_action": ai_action,
                    "ai_summary": ai_summary,
                    "ai_model": ai_model,
                    "ai_latency_ms": ai_latency_ms,
                }
            ).eq("id", signal_id).execute()
        except Exception:
            logger.error("update_ai_decision 실패: signal_id=%s", signal_id, exc_info=True)

    # ------------------------------------------------------------------
    # trades
    # ------------------------------------------------------------------
    def save_trade(self, data: dict[str, Any]) -> str | None:
        """trades 테이블에 INSERT. 생성된 trade_id를 즉시 반환한다.

        data에 condition_name을 주면 hts_conditions에서 condition_id를 찾아(없으면 등록) 채운다.
        """
        if not self._enabled:
            return None

        trade_id = str(data.get("id") or uuid.uuid4())
        self._executor.submit(self._save_trade_sync, trade_id, data)
        return trade_id

    def _save_trade_sync(self, trade_id: str, data: dict[str, Any]) -> None:
        try:
            row = {**data, "id": trade_id}
            condition_name = row.pop("condition_name", None)
            if condition_name and row.get("condition_id") is None:
                row["condition_id"] = self._ensure_condition_sync(condition_name)
            self._client.table("trades").insert(row).execute()
        except Exception:
            logger.error("save_trade 실패: trade_id=%s", trade_id, exc_info=True)

    def update_trade(self, trade_id: str, fields: dict[str, Any]) -> None:
        """trades 행의 일부 필드를 UPDATE한다 (예: 2차 트랜치 매수 후 평균단가/수량)."""
        if not self._enabled:
            return
        self._executor.submit(self._update_trade_exit_sync, trade_id, fields)

    def save_sell_orders(self, trade_id: str, orders: list[dict[str, Any]]) -> None:
        if not self._enabled or not orders:
            return
        self._executor.submit(self._save_sell_orders_sync, trade_id, orders)

    def _save_sell_orders_sync(self, trade_id: str, orders: list[dict[str, Any]]) -> None:
        try:
            rows = [{**order, "trade_id": trade_id} for order in orders]
            self._client.table("sell_orders").insert(rows).execute()
        except Exception:
            logger.error("save_sell_orders 실패: trade_id=%s", trade_id, exc_info=True)

    def save_exit(
        self,
        trade_id: str,
        exit_price: int,
        exit_reason: str,
        pnl_amount: int,
        pnl_pct: float,
    ) -> None:
        if not self._enabled:
            return
        fields = {
            "exit_price": exit_price,
            "exit_time": dt.datetime.now(dt.timezone.utc).isoformat(),
            "exit_reason": exit_reason,
            "pnl_amount": pnl_amount,
            "pnl_pct": pnl_pct,
        }
        self._executor.submit(self._update_trade_exit_sync, trade_id, fields)

    def _update_trade_exit_sync(self, trade_id: str, fields: dict[str, Any]) -> None:
        try:
            self._client.table("trades").update(fields).eq("id", trade_id).execute()
        except Exception:
            logger.error("trades 청산 UPDATE 실패: trade_id=%s", trade_id, exc_info=True)

    def close_trade(self, trade_id: str, decision: ExitDecision) -> None:
        """Position Guard의 ExitDecision(손절/익절/트레일링/시간초과)을 trades에 반영한다."""
        self.save_exit(
            trade_id,
            exit_price=decision.exit_price,
            exit_reason=decision.exit_reason,
            pnl_amount=decision.pnl_amount,
            pnl_pct=decision.pnl_pct,
        )

    def close_trade_target(
        self,
        trade_id: str,
        stock_code: str,
        exit_price: int,
        pnl_pct: float,
        pnl_amount: int,
    ) -> None:
        """지정가(목표가) 매도 주문 체결 시 trades를 UPDATE한다."""
        if not self._enabled:
            return
        fields = {
            "exit_price": exit_price,
            "exit_time": dt.datetime.now(dt.timezone.utc).isoformat(),
            "exit_reason": "목표가",
            "pnl_amount": pnl_amount,
            "pnl_pct": pnl_pct,
        }
        self._executor.submit(self._close_trade_target_sync, trade_id, stock_code, fields)

    def _close_trade_target_sync(self, trade_id: str, stock_code: str, fields: dict[str, Any]) -> None:
        try:
            self._client.table("trades").update(fields).eq("id", trade_id).eq(
                "stock_code", stock_code
            ).execute()
        except Exception:
            logger.error(
                "close_trade_target 실패: trade_id=%s stock_code=%s", trade_id, stock_code, exc_info=True
            )

    def get_open_positions(self) -> list[dict[str, Any]]:
        """v_open_positions 뷰를 조회한다. 읽기이므로 동기 실행한다."""
        if not self._enabled:
            return []
        try:
            resp = self._client.table("v_open_positions").select("*").execute()
            return resp.data or []
        except Exception:
            logger.error("get_open_positions 실패", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # daily_summaries
    # ------------------------------------------------------------------
    def save_daily_summary(self, data: dict[str, Any]) -> None:
        if not self._enabled:
            return
        self._executor.submit(self._save_daily_summary_sync, data)

    def _save_daily_summary_sync(self, data: dict[str, Any]) -> None:
        try:
            self._client.table("daily_summaries").upsert(data, on_conflict="date").execute()
        except Exception:
            logger.error("save_daily_summary 실패: date=%s", data.get("date"), exc_info=True)

    def build_daily_summary(self, date: dt.date) -> None:
        """해당 날짜의 signals/trades를 집계해 daily_summaries에 UPSERT한다."""
        if not self._enabled:
            return
        self._executor.submit(self._build_daily_summary_sync, date)

    def _build_daily_summary_sync(self, date: dt.date) -> None:
        try:
            start = dt.datetime.combine(date, dt.time.min, tzinfo=dt.timezone.utc).isoformat()
            end = dt.datetime.combine(date, dt.time.max, tzinfo=dt.timezone.utc).isoformat()

            signals_resp = (
                self._client.table("signals")
                .select("id", count="exact")
                .gte("signal_time", start)
                .lte("signal_time", end)
                .execute()
            )
            # 집계 기준: 그 날 진입(entry_time)한 trades. 승/패/손익은 그중 청산 완료된 건만 집계한다.
            trades_resp = (
                self._client.table("trades")
                .select("pnl_amount, exit_time")
                .gte("entry_time", start)
                .lte("entry_time", end)
                .execute()
            )
            trades = trades_resp.data or []
            closed = [t for t in trades if t.get("exit_time") is not None]
            win_count = sum(1 for t in closed if (t.get("pnl_amount") or 0) > 0)
            loss_count = sum(1 for t in closed if (t.get("pnl_amount") or 0) < 0)
            total_pnl = sum(t.get("pnl_amount") or 0 for t in closed)

            summary = {
                "date": date.isoformat(),
                "total_signals": signals_resp.count or 0,
                "total_trades": len(trades),
                "win_count": win_count,
                "loss_count": loss_count,
                "total_pnl": total_pnl,
            }
            self._client.table("daily_summaries").upsert(summary, on_conflict="date").execute()
        except Exception:
            logger.error("build_daily_summary 실패: date=%s", date, exc_info=True)

    # ------------------------------------------------------------------
    # mv_condition_performance
    # ------------------------------------------------------------------
    def refresh_condition_performance(self) -> None:
        """RPC(refresh_condition_performance)로 mv_condition_performance를 갱신한다."""
        if not self._enabled:
            return
        self._executor.submit(self._refresh_condition_performance_sync)

    def _refresh_condition_performance_sync(self) -> None:
        try:
            self._client.rpc("refresh_condition_performance").execute()
        except Exception:
            logger.error("refresh_condition_performance 실패", exc_info=True)
