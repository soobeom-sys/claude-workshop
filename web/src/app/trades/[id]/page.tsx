import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import CandlestickChart from "@/components/charts/CandlestickChart";
import { ExitReasonBadge } from "@/components/ui/Badge";
import { PnlAmount, PnlPct } from "@/components/ui/PnlText";
import { styleColor } from "@/constants/trading";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { getCandlesForTrade, type Candle } from "@/queries/candles";
import { getTradeById, type SellOrderStatus } from "@/queries/trades";

export const dynamic = "force-dynamic";

const SELL_STATUS_CLASS: Record<SellOrderStatus, string> = {
  체결: "bg-emerald-900/60 text-emerald-300",
  취소: "bg-slate-700 text-slate-300",
  대기: "bg-amber-900/60 text-amber-300",
};

function formatDuration(from: Date, to: Date | null) {
  const minutes = Math.max(0, Math.floor(((to ?? new Date()).getTime() - from.getTime()) / 60000));
  const text = minutes >= 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `${minutes}분`;
  return to ? text : `${text} (보유중)`;
}

/** 보유 구간(진입~청산, 보유중이면 마지막 봉까지) 1분봉의 최고가 */
function peakPrice(candles: Candle[], from: Date, to: Date | null) {
  const start = from.getTime() - 60_000;
  const end = to ? to.getTime() : Infinity;
  const highs = candles
    .filter((c) => {
      const t = new Date(c.candle_time).getTime();
      return t >= start && t <= end;
    })
    .map((c) => c.high);
  return highs.length ? Math.max(...highs) : null;
}

function InfoCard({ title, rows }: { title: string; rows: [string, ReactNode][] }) {
  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-300">{title}</h3>
      <dl className="space-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4">
            <dt className="text-slate-400">{label}</dt>
            <dd className="text-right text-slate-100 tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default async function TradeDetailPage({ params }: PageProps<"/trades/[id]">) {
  const { id } = await params;
  // trades.id는 uuid라 Number(id) 대신 문자열 그대로 조회한다.
  const { trade, sellOrders, marker } = await getTradeById(id);
  if (!trade || !marker) notFound();

  const candles = await getCandlesForTrade(trade.stock_code, trade.entry_time);
  const peak = peakPrice(candles, trade.entry_time, trade.exit_time);
  const trailing =
    trade.trailing_activate_pct != null && trade.trailing_drop_pct != null
      ? `+${trade.trailing_activate_pct}% / -${trade.trailing_drop_pct}%`
      : "미사용";

  return (
    <div className="space-y-6">
      <Link href="/trades" className="text-sm text-slate-400 hover:text-slate-200">
        ← 매매 내역
      </Link>

      <div>
        <h2 className="text-2xl font-bold text-white">
          {trade.stock_name ?? trade.stock_code} <span className="text-slate-400">({trade.stock_code})</span>
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          Trade #{trade.id.slice(0, 8)} | {formatDateTime(trade.entry_time)}
        </p>
      </div>

      <CandlestickChart candles={candles} marker={marker} />

      <div className="flex flex-wrap items-center gap-6 rounded-xl border border-slate-700 bg-slate-800/50 p-4">
        <div>
          <div className="text-xs text-slate-400">수익률</div>
          <PnlPct value={trade.pnl_pct} className="text-2xl font-bold tabular-nums" />
        </div>
        <div>
          <div className="text-xs text-slate-400">손익금</div>
          <PnlAmount value={trade.pnl_amount} className="text-2xl font-bold tabular-nums" />
        </div>
        <div>
          <div className="mb-1 text-xs text-slate-400">청산사유</div>
          {trade.exit_time ? <ExitReasonBadge reason={trade.exit_reason} /> : <span className="text-sm text-amber-300">보유중</span>}
        </div>
        {marker.ai_summary && <p className="min-w-0 flex-1 text-sm text-slate-300">{marker.ai_summary}</p>}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <InfoCard
          title="종목 정보"
          rows={[
            ["종목코드", trade.stock_code],
            ["거래소", trade.exchange ?? "-"],
            ["조건식", trade.condition_name ?? "-"],
            ["전략스타일", trade.style ? <span className={styleColor(trade.style)}>{trade.style}</span> : "-"],
            // 분할매수 차수·매수 주문번호는 백엔드가 trades에 저장하지 않는다(Position에만 존재).
            ["분할매수 차수", "-"],
            ["주문번호", "-"],
          ]}
        />
        <InfoCard
          title="매매 정보"
          rows={[
            ["매입가", formatPrice(trade.entry_price)],
            ["매도가", trade.exit_price == null ? "보유중" : formatPrice(trade.exit_price)],
            ["수량", `${formatNumber(trade.quantity)}주`],
            ["매매금액", formatPrice(trade.entry_price * trade.quantity)],
            ["보유시간", formatDuration(trade.entry_time, trade.exit_time)],
            ["고점", peak == null ? "-" : formatPrice(peak)],
          ]}
        />
        <InfoCard
          title="전략 파라미터"
          rows={[
            ["목표가", trade.target_pct == null ? "-" : `+${trade.target_pct}% (${formatPrice(marker.target_line)})`],
            ["손절가", trade.stop_pct == null ? "-" : `-${Math.abs(trade.stop_pct)}% (${formatPrice(marker.stop_line)})`],
            ["최대보유", trade.max_hold_min == null ? "-" : `${trade.max_hold_min}분`],
            ["트레일링 활성/하락", trailing],
          ]}
        />
      </div>

      {sellOrders.length > 0 && (
        <section className="rounded-xl border border-slate-700 bg-slate-800/50">
          <h3 className="border-b border-slate-700 px-4 py-3 text-sm font-semibold text-slate-300">매도 주문</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-700 text-left text-xs text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">주문번호</th>
                  <th className="px-4 py-3 text-right font-medium">수량</th>
                  <th className="px-4 py-3 text-right font-medium">목표가</th>
                  <th className="px-4 py-3 font-medium">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {sellOrders.map((o) => (
                  <tr key={o.id}>
                    <td className="px-4 py-3 tabular-nums text-slate-200">{o.order_no}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatNumber(o.quantity)}주</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatPrice(o.target_price)}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${SELL_STATUS_CLASS[o.status]}`}>
                        {o.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
