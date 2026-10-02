import { sql } from "@/lib/db";
import { todayStartKst } from "./dashboard";

export type DailyPnlRow = {
  date: string;
  trade_count: number;
  win_count: number;
  loss_count: number;
  total_pnl: number;
  avg_pnl_pct: number | null;
  win_rate_pct: number | null;
  cumulative_pnl: number;
};

/** 최근 N일(오늘 포함) 청산 기준 일별 집계. 누적 손익은 조회 구간 내 누적이며 날짜 오름차순으로 반환한다. */
export async function getDailyPnl(days = 90) {
  return sql<DailyPnlRow[]>`
    select
      to_char(date, 'YYYY-MM-DD') as date,
      trade_count, win_count, loss_count, total_pnl, avg_pnl_pct,
      case when win_count + loss_count > 0
        then round(win_count::numeric / (win_count + loss_count) * 100, 2)::float8
      end as win_rate_pct,
      sum(total_pnl) over (order by date)::float8 as cumulative_pnl
    from (
      select
        (exit_time at time zone 'Asia/Seoul')::date as date,
        count(*)::int as trade_count,
        count(*) filter (where pnl_amount > 0)::int as win_count,
        count(*) filter (where pnl_amount < 0)::int as loss_count,
        coalesce(sum(pnl_amount), 0)::float8 as total_pnl,
        avg(pnl_pct)::float8 as avg_pnl_pct
      from trades
      where exit_time >= ${todayStartKst} - make_interval(days => ${days - 1})
      group by 1
    ) d
    order by date
  `;
}
