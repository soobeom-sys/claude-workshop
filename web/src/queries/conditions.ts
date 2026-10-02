import { sql } from "@/lib/db";

export type ConditionRow = {
  id: number;
  name: string;
  style: string | null;
  total_signals: number;
  buy_signals: number;
  skip_signals: number;
  /** 진입한 매매 수 (보유 중 포함) */
  total_trades: number;
  /** 청산 완료된 매매 수 */
  closed_trades: number;
  win_count: number;
  loss_count: number;
  total_pnl: number;
  best_pnl_pct: number | null;
  worst_pnl_pct: number | null;
  win_rate_pct: number | null;
  exit_target: number;
  exit_stop_loss: number;
  exit_trailing: number;
  exit_time_limit: number;
  exit_market_close: number;
  first_at: Date | null;
  last_at: Date | null;
};

// mv_condition_performance는 백엔드가 refresh_condition_performance()로 갱신할 때만 최신이 되므로,
// 대시보드에서는 signals/trades를 직접 집계해 항상 현재 값을 보여준다.
// exit_reason 값은 trading/models.py의 ExitReason과 같다. 지정가 체결(목표가)과 익절은 목표가로 묶는다.
export async function getConditions() {
  return sql<ConditionRow[]>`
    select
      hc.id, hc.name, hc.style,
      coalesce(s.total_signals, 0)::int as total_signals,
      coalesce(s.buy_signals, 0)::int as buy_signals,
      coalesce(s.skip_signals, 0)::int as skip_signals,
      coalesce(t.total_trades, 0)::int as total_trades,
      coalesce(t.closed_trades, 0)::int as closed_trades,
      coalesce(t.win_count, 0)::int as win_count,
      coalesce(t.loss_count, 0)::int as loss_count,
      coalesce(t.total_pnl, 0)::float8 as total_pnl,
      t.best_pnl_pct::float8 as best_pnl_pct,
      t.worst_pnl_pct::float8 as worst_pnl_pct,
      case when t.closed_trades > 0
        then round(t.win_count::numeric / t.closed_trades * 100, 2)::float8
      end as win_rate_pct,
      coalesce(t.exit_target, 0)::int as exit_target,
      coalesce(t.exit_stop_loss, 0)::int as exit_stop_loss,
      coalesce(t.exit_trailing, 0)::int as exit_trailing,
      coalesce(t.exit_time_limit, 0)::int as exit_time_limit,
      coalesce(t.exit_market_close, 0)::int as exit_market_close,
      least(s.first_at, t.first_at) as first_at,
      greatest(s.last_at, t.last_at) as last_at
    from hts_conditions hc
    left join (
      select
        condition_id,
        count(*) as total_signals,
        count(*) filter (where ai_action in ('매수', 'BUY')) as buy_signals,
        count(*) filter (where ai_action in ('패스', 'SKIP')) as skip_signals,
        min(signal_time) as first_at,
        max(signal_time) as last_at
      from signals group by condition_id
    ) s on s.condition_id = hc.id
    left join (
      select
        condition_id,
        count(*) as total_trades,
        count(*) filter (where exit_time is not null) as closed_trades,
        count(*) filter (where exit_time is not null and pnl_amount > 0) as win_count,
        count(*) filter (where exit_time is not null and pnl_amount < 0) as loss_count,
        sum(pnl_amount) filter (where exit_time is not null) as total_pnl,
        max(pnl_pct) filter (where exit_time is not null) as best_pnl_pct,
        min(pnl_pct) filter (where exit_time is not null) as worst_pnl_pct,
        count(*) filter (where exit_reason in ('목표가', '익절')) as exit_target,
        count(*) filter (where exit_reason = '손절') as exit_stop_loss,
        count(*) filter (where exit_reason = '트레일링') as exit_trailing,
        count(*) filter (where exit_reason = '시간초과') as exit_time_limit,
        count(*) filter (where exit_reason = '장마감') as exit_market_close,
        min(entry_time) as first_at,
        max(coalesce(exit_time, entry_time)) as last_at
      from trades
      group by condition_id
    ) t on t.condition_id = hc.id
    order by total_pnl desc, hc.name
  `;
}
