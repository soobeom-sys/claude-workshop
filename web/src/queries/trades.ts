import { sql } from "@/lib/db";

export type TradeRow = {
  id: string;
  stock_code: string;
  stock_name: string | null;
  condition_name: string | null;
  style: string | null;
  entry_price: number;
  quantity: number;
  entry_time: Date;
  exit_price: number | null;
  exit_time: Date | null;
  exit_reason: string | null;
  pnl_amount: number | null;
  pnl_pct: number | null;
};

export type TradeStatus = "open" | "closed";

export type TradeListParams = {
  page: number;
  pageSize: number;
  status?: TradeStatus;
};

// numeric 컬럼은 postgres-js가 문자열로 돌려주므로 float8로 캐스팅한다.
export const tradeColumns = sql`
  t.id, t.stock_code, t.stock_name,
  hc.name as condition_name, hc.style,
  t.entry_price, t.quantity, t.entry_time,
  t.exit_price, t.exit_time, t.exit_reason,
  t.pnl_amount, t.pnl_pct::float8 as pnl_pct
`;

function statusFilter(status?: TradeStatus) {
  if (status === "open") return sql`where t.exit_time is null`;
  if (status === "closed") return sql`where t.exit_time is not null`;
  return sql``;
}

export async function getTrades({ page, pageSize, status }: TradeListParams) {
  const where = statusFilter(status);
  const [rows, [{ total }]] = await Promise.all([
    sql<TradeRow[]>`
      select ${tradeColumns}
      from trades t
      left join hts_conditions hc on hc.id = t.condition_id
      ${where}
      order by t.entry_time desc
      limit ${pageSize} offset ${(page - 1) * pageSize}
    `,
    sql<{ total: number }[]>`select count(*)::int as total from trades t ${where}`,
  ]);
  return { rows, total };
}

export type TradeDetail = TradeRow & {
  exchange: string | null;
  target_pct: number | null;
  stop_pct: number | null;
  max_hold_min: number | null;
  trailing_activate_pct: number | null;
  trailing_drop_pct: number | null;
  ai_summary: string | null;
};

export type SellOrderStatus = "체결" | "취소" | "대기";

export type SellOrderRow = {
  id: string;
  order_no: string;
  quantity: number;
  target_price: number;
  status: SellOrderStatus;
};

/** 차트용 매수/매도 마커 + 목표/손절 수평선 */
export type TradeChartMarker = {
  buy_time: Date;
  buy_price: number;
  sell_time: Date | null;
  sell_price: number | null;
  exit_reason: string | null;
  target_line: number | null;
  stop_line: number | null;
  pnl_pct: number | null;
  pnl_amount: number | null;
  ai_summary: string | null;
  condition_name: string | null;
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * 매매 한 건 + 매도 주문 + 차트 마커.
 *
 * 전략 파라미터는 진입 시점 값이 저장된 trades.strategy_json을 우선하고, 없으면 hts_conditions를 쓴다.
 * config.py의 stop_pct는 음수(-1.5)로 저장되므로 부호와 무관하게 abs()로 손절선을 진입가 아래에 긋는다.
 */
export async function getTradeById(id: string) {
  if (!UUID_RE.test(id)) return { trade: null, sellOrders: [], marker: null };

  const [trade] = await sql<TradeDetail[]>`
    select ${tradeColumns},
      t.exchange,
      coalesce((t.strategy_json->>'target_pct')::float8, hc.target_pct::float8) as target_pct,
      coalesce((t.strategy_json->>'stop_pct')::float8, hc.stop_pct::float8) as stop_pct,
      coalesce((t.strategy_json->>'max_hold_min')::int, hc.max_hold_min) as max_hold_min,
      (t.strategy_json->>'trailing_activate_pct')::float8 as trailing_activate_pct,
      (t.strategy_json->>'trailing_drop_pct')::float8 as trailing_drop_pct,
      s.ai_summary
    from trades t
    left join hts_conditions hc on hc.id = t.condition_id
    left join signals s on s.id = t.signal_id
    where t.id = ${id}
  `;
  if (!trade) return { trade: null, sellOrders: [], marker: null };

  // sell_orders에는 상태 컬럼이 없어 매매 결과로 추정한다:
  // 목표가 청산 → 체결, 그 외 청산 → Position Guard가 취소, 보유중 → 대기.
  const sellOrders = await sql<SellOrderRow[]>`
    select so.id, so.order_no, so.quantity, so.target_price,
      case
        when t.exit_time is null then '대기'
        when t.exit_reason in ('목표가', 'TARGET') then '체결'
        else '취소'
      end as status
    from sell_orders so
    join trades t on t.id = so.trade_id
    where so.trade_id = ${id}
    order by so.target_price asc
  `;

  const marker: TradeChartMarker = {
    buy_time: trade.entry_time,
    buy_price: trade.entry_price,
    sell_time: trade.exit_time,
    sell_price: trade.exit_price,
    exit_reason: trade.exit_reason,
    target_line: trade.target_pct == null ? null : Math.round(trade.entry_price * (1 + trade.target_pct / 100)),
    stop_line: trade.stop_pct == null ? null : Math.round(trade.entry_price * (1 - Math.abs(trade.stop_pct) / 100)),
    pnl_pct: trade.pnl_pct,
    pnl_amount: trade.pnl_amount,
    ai_summary: trade.ai_summary,
    condition_name: trade.condition_name,
  };

  return { trade, sellOrders, marker };
}
