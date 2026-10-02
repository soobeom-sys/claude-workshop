import type { ReactNode } from "react";
import PageHeader from "@/components/layout/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { PnlAmount, PnlPct } from "@/components/ui/PnlText";
import { styleColor } from "@/constants/trading";
import { getConditions, type ConditionRow } from "@/queries/conditions";

export const dynamic = "force-dynamic";

const dateFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function winRateColor(rate: number | null) {
  if (rate == null) return "text-slate-400";
  if (rate >= 60) return "text-emerald-400";
  if (rate >= 40) return "text-amber-400";
  return "text-red-400";
}

function formatPeriod(from: Date | null, to: Date | null) {
  if (!from || !to) return "기록 없음";
  const start = dateFormat.format(from);
  const end = dateFormat.format(to);
  return start === end ? start : `${start} ~ ${end}`;
}

function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      {children}
    </div>
  );
}

function ConditionCard({ c }: { c: ConditionRow }) {
  const exitReasons = [
    { label: "목표가", count: c.exit_target },
    { label: "손절", count: c.exit_stop_loss },
    { label: "트레일링", count: c.exit_trailing },
    { label: "시간초과", count: c.exit_time_limit },
    // 장마감 강제 청산은 발생한 조건식에서만 표시한다.
    ...(c.exit_market_close > 0 ? [{ label: "장마감", count: c.exit_market_close }] : []),
  ];

  return (
    <article className="rounded-xl border border-slate-700 bg-slate-800/50 p-5">
      <header className="mb-4">
        <h3 className="text-base font-semibold text-white">{c.name}</h3>
        <div className={`text-xs ${styleColor(c.style)}`}>{c.style ?? "기본"}</div>
      </header>

      <div className="grid grid-cols-3 gap-3">
        <Metric label="매매 수">
          <div className="text-lg font-bold text-blue-400 tabular-nums">{c.total_trades}</div>
        </Metric>
        <Metric label="승률">
          <div className={`text-lg font-bold tabular-nums ${winRateColor(c.win_rate_pct)}`}>
            {c.win_rate_pct == null ? "-" : `${c.win_rate_pct.toFixed(1)}%`}
          </div>
        </Metric>
        <Metric label="총 손익">
          <PnlAmount
            value={c.closed_trades > 0 ? c.total_pnl : null}
            className="block text-lg font-bold tabular-nums"
          />
        </Metric>
      </div>

      <div className="mt-4 space-y-1.5 tabular-nums">
        <div className="flex gap-4 text-xs">
          <span className="text-slate-200">신호 {c.total_signals}</span>
          <span className="text-emerald-400">BUY {c.buy_signals}</span>
          <span className="text-slate-400">SKIP {c.skip_signals}</span>
        </div>
        <div className="flex gap-4 text-xs">
          <span className="text-slate-200">청산 {c.closed_trades}</span>
          <span className="text-emerald-400">{c.win_count}W</span>
          <span className="text-red-400">{c.loss_count}L</span>
        </div>
        <div className="flex gap-4 text-xs">
          <span className="text-emerald-400">
            최고 <PnlPct value={c.best_pnl_pct} className="text-emerald-400" />
          </span>
          <span className="text-red-400">
            최저 <PnlPct value={c.worst_pnl_pct} className="text-red-400" />
          </span>
        </div>
      </div>

      <div className="mt-4 border-t border-slate-700 pt-3">
        <div className="grid grid-cols-2 gap-1 text-xs">
          {exitReasons.map((r) => (
            <div key={r.label} className="flex justify-between pr-4">
              <span className="text-slate-400">{r.label}</span>
              <span className="text-slate-200 tabular-nums">{r.count}건</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3 text-xs text-slate-600">{formatPeriod(c.first_at, c.last_at)}</div>
    </article>
  );
}

export default async function ConditionsPage() {
  const rows = await getConditions();

  return (
    <>
      <PageHeader title="조건식" description="HTS 조건식별 매매 성과" />
      {rows.length === 0 ? (
        <section className="rounded-xl border border-slate-700 bg-slate-800/50">
          <EmptyState message="등록된 조건식이 없습니다." />
        </section>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((c) => (
            <ConditionCard key={c.id} c={c} />
          ))}
        </div>
      )}
    </>
  );
}
