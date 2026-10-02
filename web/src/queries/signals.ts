import { sql } from "@/lib/db";

export type SignalRow = {
  id: string;
  stock_code: string;
  stock_name: string | null;
  condition_name: string;
  style: string | null;
  signal_price: number | null;
  signal_change_pct: number | null;
  volume: number | null;
  signal_time: Date;
  ai_action: string | null;
  ai_summary: string | null;
  ai_model: string | null;
  ai_latency_ms: number | null;
};

/** 화면 필터 값 → DB 저장 값 (main.py는 "매수"/"패스"로 저장한다) */
export type SignalActionFilter = "BUY" | "SKIP";

export type SignalFilterParams = {
  /** KST 기준 YYYY-MM-DD */
  startDate?: string;
  /** KST 기준 YYYY-MM-DD (해당 일자 포함) */
  endDate?: string;
  conditionName?: string;
  action?: SignalActionFilter;
  /** 접두 일치 */
  stockCode?: string;
};

export type SignalListParams = SignalFilterParams & {
  page: number;
  pageSize: number;
};

function signalWhere({ startDate, endDate, conditionName, action, stockCode }: SignalFilterParams) {
  const conds = [];
  if (startDate) conds.push(sql`s.signal_time >= ${startDate}::date::timestamp at time zone 'Asia/Seoul'`);
  if (endDate) conds.push(sql`s.signal_time < (${endDate}::date + 1)::timestamp at time zone 'Asia/Seoul'`);
  if (conditionName) conds.push(sql`s.condition_name = ${conditionName}`);
  if (action === "BUY") conds.push(sql`s.ai_action in ('매수', 'BUY')`);
  if (action === "SKIP") conds.push(sql`s.ai_action in ('패스', 'SKIP')`);
  if (stockCode) conds.push(sql`s.stock_code like ${`${stockCode}%`}`);
  if (conds.length === 0) return sql``;
  return sql`where ${conds.reduce((acc, cond) => sql`${acc} and ${cond}`)}`;
}

export async function getSignals({ page, pageSize, ...filters }: SignalListParams) {
  const where = signalWhere(filters);
  const [rows, [{ total }]] = await Promise.all([
    sql<SignalRow[]>`
      select
        s.id, s.stock_code, s.stock_name, s.condition_name, hc.style,
        s.signal_price, s.signal_change_pct::float8 as signal_change_pct,
        s.volume::float8 as volume, s.signal_time,
        s.ai_action, s.ai_summary, s.ai_model, s.ai_latency_ms
      from signals s
      left join hts_conditions hc on hc.id = s.condition_id
      ${where}
      order by s.signal_time desc
      limit ${pageSize} offset ${(page - 1) * pageSize}
    `,
    sql<{ total: number }[]>`select count(*)::int as total from signals s ${where}`,
  ]);
  return { rows, total };
}

/** 필터 드롭다운용: 신호가 한 번이라도 들어온 조건식 이름 */
export async function getSignalConditionNames() {
  const rows = await sql<{ condition_name: string }[]>`
    select distinct condition_name from signals order by condition_name
  `;
  return rows.map((r) => r.condition_name);
}
