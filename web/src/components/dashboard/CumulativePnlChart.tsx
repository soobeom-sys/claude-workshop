"use client";

import { AreaSeries, createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { DailyPnlPoint } from "@/queries/dashboard";
import { pnlChartOptions } from "./chartOptions";

const LINE_COLOR = "#3b82f6";
const FILL_COLOR = "#3b82f620";

type CumulativePnlChartProps = {
  /** pnl은 해당 날짜까지의 누적 손익 */
  data: DailyPnlPoint[];
  title?: string;
  emptyMessage?: string;
  /** 차트 영역 높이(px) */
  height?: number;
};

export default function CumulativePnlChart({
  data,
  title = "누적 손익",
  emptyMessage = "청산 내역이 없습니다.",
  height = 250,
}: CumulativePnlChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, pnlChartOptions);
    const series = chart.addSeries(AreaSeries, {
      lineColor: LINE_COLOR,
      lineWidth: 2,
      topColor: FILL_COLOR,
      bottomColor: FILL_COLOR,
      priceLineVisible: false,
    });
    series.setData(data.map((d) => ({ time: d.date, value: d.pnl })));
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [data]);

  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-300">{title}</h3>
      {data.length === 0 ? (
        <div className="flex items-center justify-center text-sm text-slate-500" style={{ height }}>
          {emptyMessage}
        </div>
      ) : (
        <div ref={containerRef} style={{ height }} />
      )}
    </section>
  );
}
