import EmptyState from "@/components/ui/EmptyState";
import { PnlCell } from "@/components/ui/PnlText";
import { formatNumber, formatPrice } from "@/lib/format";
import type { OpenPositionRow } from "@/queries/dashboard";

export default function OpenPositions({ rows }: { rows: OpenPositionRow[] }) {
  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-300">
        보유 종목 <span className="text-blue-400">{rows.length}</span>
      </h3>
      {rows.length === 0 ? (
        <EmptyState message="보유 중인 종목이 없습니다." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-700 text-left text-xs text-slate-500">
              <tr>
                <th className="px-2 py-2 font-medium">종목</th>
                <th className="px-2 py-2 text-right font-medium">매입가</th>
                <th className="px-2 py-2 text-right font-medium">현재가</th>
                <th className="px-2 py-2 text-right font-medium">수익률</th>
                <th className="px-2 py-2 text-right font-medium">수량</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const pnlAmount =
                  p.current_price == null ? null : (p.current_price - p.entry_price) * p.quantity;
                return (
                  <tr key={p.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-2 py-2">
                      <div className="text-slate-200">{p.stock_name ?? p.stock_code}</div>
                      <div className="text-xs text-slate-500">{p.stock_code}</div>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">{formatPrice(p.entry_price)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{formatPrice(p.current_price)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">
                      <div className="inline-flex">
                        <PnlCell pct={p.pnl_pct} amount={pnlAmount} />
                      </div>
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">{formatNumber(p.quantity)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
