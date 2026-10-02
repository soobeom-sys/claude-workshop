import Link from "next/link";
import { styleColor } from "@/constants/trading";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import type { TradeRow } from "@/queries/trades";
import { ExitReasonBadge } from "./Badge";
import EmptyState from "./EmptyState";
import { PnlCell } from "./PnlText";

export default function TradeTable({ rows, emptyMessage }: { rows: TradeRow[]; emptyMessage?: string }) {
  if (rows.length === 0) return <EmptyState message={emptyMessage} />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-slate-700 text-left text-xs text-slate-400">
          <tr>
            <th className="px-4 py-3 font-medium">종목</th>
            <th className="px-4 py-3 font-medium">조건식</th>
            <th className="px-4 py-3 text-right font-medium">진입가 × 수량</th>
            <th className="px-4 py-3 font-medium">진입</th>
            <th className="px-4 py-3 text-right font-medium">청산가</th>
            <th className="px-4 py-3 font-medium">청산</th>
            <th className="px-4 py-3 font-medium">사유</th>
            <th className="px-4 py-3 text-right font-medium">손익</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-700/60">
          {rows.map((t) => (
            <tr key={t.id} className="hover:bg-slate-700/40">
              <td className="px-4 py-3">
                <Link href={`/trades/${t.id}`} className="font-medium text-slate-100 hover:text-sky-300 hover:underline">
                  {t.stock_name ?? t.stock_code}
                </Link>
                <div className="text-xs text-slate-500">{t.stock_code}</div>
              </td>
              <td className="px-4 py-3">
                <div className="text-slate-200">{t.condition_name ?? "-"}</div>
                {t.style && <div className={`text-xs ${styleColor(t.style)}`}>{t.style}</div>}
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                {formatPrice(t.entry_price)} × {formatNumber(t.quantity)}
              </td>
              <td className="px-4 py-3 text-slate-400 tabular-nums">{formatDateTime(t.entry_time)}</td>
              <td className="px-4 py-3 text-right tabular-nums">{formatPrice(t.exit_price)}</td>
              <td className="px-4 py-3 text-slate-400 tabular-nums">{formatDateTime(t.exit_time)}</td>
              <td className="px-4 py-3">
                <ExitReasonBadge reason={t.exit_reason} />
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                <div className="inline-flex">
                  <PnlCell pct={t.pnl_pct} amount={t.pnl_amount} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
