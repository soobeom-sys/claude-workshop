import type { ReactNode } from "react";
import CumulativePnlChart from "@/components/dashboard/CumulativePnlChart";
import PnlLineChart from "@/components/dashboard/PnlLineChart";
import PageHeader from "@/components/layout/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { PnlAmount, PnlPct, pnlColor } from "@/components/ui/PnlText";
import { getDailyPnl } from "@/queries/daily-pnl";

export const dynamic = "force-dynamic";

const DAYS = 90;
const CHART_HEIGHT = 250;

function SummaryCard({ label, value, valueClassName }: { label: string; value: ReactNode; valueClassName: string }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`mt-1 text-xl font-bold tabular-nums ${valueClassName}`}>{value}</p>
    </div>
  );
}

function formatSignedWon(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toLocaleString("ko-KR")}원`;
}

export default async function DailyPnlPage() {
  const rows = await getDailyPnl(DAYS);

  const totalPnl = rows.reduce((acc, d) => acc + d.total_pnl, 0);
  const totalTrades = rows.reduce((acc, d) => acc + d.trade_count, 0);
  const wins = rows.reduce((acc, d) => acc + d.win_count, 0);
  const losses = rows.reduce((acc, d) => acc + d.loss_count, 0);
  const winRate = wins + losses > 0 ? (wins / (wins + losses)) * 100 : null;

  const dailySeries = rows.map((d) => ({ date: d.date, pnl: d.total_pnl }));
  const cumulativeSeries = rows.map((d) => ({ date: d.date, pnl: d.cumulative_pnl }));
  const tableRows = [...rows].reverse();

  return (
    <>
      <PageHeader title="일일손익" description={`최근 ${DAYS}일 청산 기준 일별 손익 분석`} />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <SummaryCard label="총 손익" value={formatSignedWon(totalPnl)} valueClassName={pnlColor(totalPnl)} />
        <SummaryCard label="총 매매" value={totalTrades.toLocaleString("ko-KR")} valueClassName="text-blue-400" />
        <SummaryCard label="승/패" value={`${wins}승 ${losses}패`} valueClassName="text-slate-200" />
        <SummaryCard
          label="승률"
          value={winRate == null ? "-" : `${winRate.toFixed(1)}%`}
          valueClassName={winRate != null && winRate >= 60 ? "text-emerald-400" : "text-amber-400"}
        />
        <SummaryCard label="거래일" value={`${rows.length}일`} valueClassName="text-slate-200" />
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <PnlLineChart
          data={dailySeries}
          title="일별 손익"
          emptyMessage={`최근 ${DAYS}일 청산 내역이 없습니다.`}
          height={CHART_HEIGHT}
        />
        <CumulativePnlChart
          data={cumulativeSeries}
          emptyMessage={`최근 ${DAYS}일 청산 내역이 없습니다.`}
          height={CHART_HEIGHT}
        />
      </div>

      <section className="rounded-lg border border-slate-700 bg-slate-800">
        {tableRows.length === 0 ? (
          <EmptyState message={`최근 ${DAYS}일 청산 내역이 없습니다.`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-700 text-left text-xs text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">날짜</th>
                  <th className="px-4 py-3 text-right font-medium">청산</th>
                  <th className="px-4 py-3 text-right font-medium">승</th>
                  <th className="px-4 py-3 text-right font-medium">패</th>
                  <th className="px-4 py-3 text-right font-medium">승률</th>
                  <th className="px-4 py-3 text-right font-medium">일일 손익</th>
                  <th className="px-4 py-3 text-right font-medium">평균 수익률</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {tableRows.map((d) => (
                  <tr key={d.date} className="hover:bg-slate-700/40">
                    <td className="px-4 py-3 font-medium text-slate-200 tabular-nums">{d.date}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{d.trade_count}</td>
                    <td className="px-4 py-3 text-right text-emerald-400 tabular-nums">{d.win_count}</td>
                    <td className="px-4 py-3 text-right text-red-400 tabular-nums">{d.loss_count}</td>
                    {/* 승률은 50% 기준으로 손익 색상을 적용한다. */}
                    <td
                      className={`px-4 py-3 text-right tabular-nums ${pnlColor(d.win_rate_pct == null ? null : d.win_rate_pct - 50)}`}
                    >
                      {d.win_rate_pct == null ? "-" : `${d.win_rate_pct.toFixed(1)}%`}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PnlAmount value={d.total_pnl} />
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PnlPct value={d.avg_pnl_pct} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
