import { sql } from "@/lib/db";
import { tradeColumns, type TradeRow } from "./trades";

export type DashboardSummary = {
  today_signals: number;
  open_positions: number;
  today_wins: number;
  today_losses: number;
  today_pnl: number | null;
};

export type OpenPositionRow = {
  id: string;
  stock_code: string;
  stock_name: string | null;
  entry_price: number;
  quantity: number;
  entry_time: Date;
  /** DB에 실시간 시세가 저장되지 않으므로 현재는 항상 null */
  current_price: number | null;
  pnl_pct: number | null;
};

export type DailyPnlPoint = {
  date: string;
  pnl: number;
};

// "오늘"은 한국 장 기준이므로 Asia/Seoul 자정을 timestamptz로 만들어 인덱스를 탈 수 있게 비교한다.
export const todayStartKst = sql`(date_trunc('day', now() at time zone 'Asia/Seoul') at time zone 'Asia/Seoul')`;

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const [row] = await sql<DashboardSummary[]>`
    select
      (select count(*)::int from signals where signal_time >= ${todayStartKst}) as today_signals,
      (select count(*)::int from v_open_positions) as open_positions,
      count(*) filter (where pnl_amount > 0)::int as today_wins,
      count(*) filter (where pnl_amount < 0)::int as today_losses,
      sum(pnl_amount)::float8 as today_pnl
    from trades
    where entry_time >= ${todayStartKst} and exit_time is not null
  `;
  return row;
}

export async function getOpenPositions() {
  return sql<OpenPositionRow[]>`
    select
      id, stock_code, stock_name, entry_price, quantity, entry_time,
      null::int as current_price,
      null::float8 as pnl_pct
    from v_open_positions
    order by entry_time desc
  `;
}

export async function getRecentClosedTrades(limit = 10) {
  return sql<TradeRow[]>`
    select ${tradeColumns}
    from trades t
    left join hts_conditions hc on hc.id = t.condition_id
    where t.exit_time is not null
    order by t.exit_time desc
    limit ${limit}
  `;
}

/** 최근 30일(오늘 포함) 청산 기준 일별 실현손익 */
export async function getDailyPnlSeries(days = 30) {
  return sql<DailyPnlPoint[]>`
    select
      to_char((exit_time at time zone 'Asia/Seoul')::date, 'YYYY-MM-DD') as date,
      sum(pnl_amount)::float8 as pnl
    from trades
    where exit_time >= ${todayStartKst} - make_interval(days => ${days - 1})
    group by 1
    order by 1
  `;
}
