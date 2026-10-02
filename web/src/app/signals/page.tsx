import PageHeader from "@/components/layout/PageHeader";
import SignalFilters from "@/components/signals/SignalFilters";
import { ActionBadge } from "@/components/ui/Badge";
import EmptyState from "@/components/ui/EmptyState";
import Pagination from "@/components/ui/Pagination";
import { PnlPct } from "@/components/ui/PnlText";
import { SIGNALS_PAGE_SIZE } from "@/constants/trading";
import { formatDateTime, formatNumber, formatPrice, formatTime, parsePage } from "@/lib/format";
import { getSignalConditionNames, getSignals, type SignalFilterParams } from "@/queries/signals";

export const dynamic = "force-dynamic";

type SearchParamValue = string | string[] | undefined;

function first(value: SearchParamValue) {
  const v = Array.isArray(value) ? value[0] : value;
  return v?.trim() || undefined;
}

function parseDate(value: SearchParamValue) {
  const v = first(value);
  return v && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v)) ? v : undefined;
}

function parseFilters(params: Record<string, SearchParamValue>): SignalFilterParams {
  const action = first(params.action);
  const code = first(params.code);
  return {
    startDate: parseDate(params.start),
    endDate: parseDate(params.end),
    conditionName: first(params.condition),
    action: action === "BUY" || action === "SKIP" ? action : undefined,
    stockCode: code && /^[0-9A-Za-z]{1,12}$/.test(code) ? code.toUpperCase() : undefined,
  };
}

export default async function SignalsPage({ searchParams }: PageProps<"/signals">) {
  const params = await searchParams;
  const page = parsePage(params.page);
  const filters = parseFilters(params);
  const [{ rows, total }, conditionNames] = await Promise.all([
    getSignals({ page, pageSize: SIGNALS_PAGE_SIZE, ...filters }),
    getSignalConditionNames(),
  ]);

  return (
    <>
      <PageHeader title="신호목록" description="조건검색 편입 신호와 AI 진입 판단" />
      <SignalFilters filters={filters} conditionNames={conditionNames} />
      <section className="rounded-xl border border-slate-700 bg-slate-800/50">
        {rows.length === 0 ? (
          <EmptyState message="조건에 맞는 신호가 없습니다." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-700 text-left text-xs text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">시각</th>
                  <th className="px-4 py-3 font-medium">종목</th>
                  <th className="px-4 py-3 font-medium">조건식</th>
                  <th className="px-4 py-3 text-right font-medium">신호가</th>
                  <th className="px-4 py-3 text-right font-medium">등락률</th>
                  <th className="px-4 py-3 text-right font-medium">거래량</th>
                  <th className="px-4 py-3 font-medium">AI</th>
                  <th className="px-4 py-3 font-medium">AI 요약</th>
                  <th className="px-4 py-3 text-right font-medium">응답(ms)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {rows.map((s) => (
                  <tr key={s.id} className="hover:bg-slate-700/40">
                    <td
                      className="whitespace-nowrap px-4 py-3 text-xs text-slate-400 tabular-nums"
                      title={formatDateTime(s.signal_time)}
                    >
                      {formatTime(s.signal_time)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <span className="text-slate-200">{s.stock_name ?? s.stock_code}</span>
                      <span className="ml-1 text-xs text-slate-500">{s.stock_code}</span>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{s.condition_name}</td>
                    <td className="px-4 py-3 text-right text-slate-200 tabular-nums">{formatPrice(s.signal_price)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <PnlPct value={s.signal_change_pct} />
                    </td>
                    <td className="px-4 py-3 text-right text-slate-400 tabular-nums">{formatNumber(s.volume)}</td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <ActionBadge action={s.ai_action} />
                    </td>
                    <td className="max-w-xs truncate px-4 py-3 text-xs text-slate-400" title={s.ai_summary ?? undefined}>
                      {s.ai_summary ?? "-"}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-slate-500 tabular-nums">
                      {formatNumber(s.ai_latency_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination
          page={page}
          pageSize={SIGNALS_PAGE_SIZE}
          total={total}
          basePath="/signals"
          params={{
            start: filters.startDate,
            end: filters.endDate,
            condition: filters.conditionName,
            action: filters.action,
            code: filters.stockCode,
          }}
        />
      </section>
    </>
  );
}
