import type { ReactNode } from "react";
import OpenPositions from "@/components/dashboard/OpenPositions";
import PnlLineChart from "@/components/dashboard/PnlLineChart";
import PageHeader from "@/components/layout/PageHeader";
import { ExitReasonBadge } from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import { PnlCell, pnlBgColor, pnlColor } from "@/components/ui/PnlText";
import { styleColor } from "@/constants/trading";
import { formatDateTime, formatPrice } from "@/lib/format";
import {
  getDailyPnlSeries,
  getDashboardSummary,
  getOpenPositions,
  getRecentClosedTrades,
} from "@/queries/dashboard";

export const dynamic = "force-dynamic";

function StatCard({ label, value, valueClassName, sub, className = "bg-slate-800" }: {
  label: string;
  value: ReactNode;
  valueClassName: string;
  sub?: string;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-slate-700 p-5 ${className}`}>
      <p className="text-xs font-medium text-slate-400">{label}</p>
      <p className={`mt-2 text-2xl font-bold tabular-nums ${valueClassName}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

function formatSignedWon(value: number | null) {
  if (value == null) return "-";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toLocaleString("ko-KR")}원`;
}

export default async function DashboardPage() {
  const [summary, openPositions, recentTrades, pnlSeries] = await Promise.all([
    getDashboardSummary(),
    getOpenPositions(),
    getRecentClosedTrades(10),
    getDailyPnlSeries(30),
  ]);

  const decided = summary.today_wins + summary.today_losses;
  const winRate = decided > 0 ? `승률 ${((summary.today_wins / decided) * 100).toFixed(1)}%` : "승률 -";

  return (
    <>
      <PageHeader title="대시보드" description="오늘의 매매 현황 요약" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="오늘 신호"
          value={summary.today_signals.toLocaleString("ko-KR")}
          valueClassName="text-blue-400"
          sub="건 탐지됨"
        />
        <StatCard
          label="보유 포지션"
          value={summary.open_positions.toLocaleString("ko-KR")}
          valueClassName="text-amber-400"
          sub="종목 보유 중"
        />
        <StatCard
          label="일일 손익"
          value={formatSignedWon(summary.today_pnl)}
          valueClassName={pnlColor(summary.today_pnl)}
          className={pnlBgColor(summary.today_pnl)}
        />
        <StatCard
          label="승/패"
          value={`${summary.today_wins}승 ${summary.today_losses}패`}
          valueClassName={pnlColor(summary.today_wins - summary.today_losses)}
          sub={winRate}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <PnlLineChart data={pnlSeries} />
        <OpenPositions rows={openPositions} />
      </div>

      <section className="mt-6 rounded-xl border border-slate-700 bg-slate-800/50 p-4">
        <h3 className="mb-3 text-sm font-semibold text-slate-300">최근 매매</h3>
        {recentTrades.length === 0 ? (
          <EmptyState message="청산된 매매가 없습니다." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-700 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-2 py-2 font-medium">종목</th>
                  <th className="px-2 py-2 font-medium">조건식</th>
                  <th className="px-2 py-2 text-right font-medium">매입가</th>
                  <th className="px-2 py-2 text-right font-medium">청산가</th>
                  <th className="px-2 py-2 text-right font-medium">손익</th>
                  <th className="px-2 py-2 font-medium">청산사유</th>
                  <th className="px-2 py-2 font-medium">시각</th>
                </tr>
              </thead>
              <tbody>
                {recentTrades.map((t) => (
                  <tr key={t.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-2 py-2">
                      <div className="text-slate-200">{t.stock_name ?? t.stock_code}</div>
                      <div className="text-xs text-slate-500">{t.stock_code}</div>
                    </td>
                    <td className="px-2 py-2">
                      <div className="text-slate-200">{t.condition_name ?? "-"}</div>
                      {t.style && <div className={`text-xs ${styleColor(t.style)}`}>{t.style}</div>}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums">{formatPrice(t.entry_price)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{formatPrice(t.exit_price)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">
                      <div className="inline-flex">
                        <PnlCell pct={t.pnl_pct} amount={t.pnl_amount} />
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <ExitReasonBadge reason={t.exit_reason} />
                    </td>
                    <td className="px-2 py-2 text-slate-400 tabular-nums">{formatDateTime(t.exit_time)}</td>
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
