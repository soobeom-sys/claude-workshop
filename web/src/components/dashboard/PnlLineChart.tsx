"use client";

import { createChart, HistogramSeries } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { DailyPnlPoint } from "@/queries/dashboard";
import { pnlChartOptions } from "./chartOptions";

const POSITIVE_COLOR = "#22c55e80";
const NEGATIVE_COLOR = "#ef444480";

type PnlLineChartProps = {
  data: DailyPnlPoint[];
  title?: string;
  emptyMessage?: string;
  /** 차트 영역 높이(px) */
  height?: number;
};

export default function PnlLineChart({
  data,
  title = "최근 30일 손익",
  emptyMessage = "최근 30일 청산 내역이 없습니다.",
  height = 220,
}: PnlLineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, pnlChartOptions);
    const series = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    series.setData(
      data.map((d) => ({
        time: d.date,
        value: d.pnl,
        color: d.pnl >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR,
      })),
    );
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
