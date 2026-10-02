"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "@/queries/candles";
import type { TradeChartMarker } from "@/queries/trades";

const UP = "#22c55e";
const DOWN = "#ef4444";
const KST_OFFSET_SEC = 9 * 60 * 60;

// lightweight-charts는 시간축을 UTC로 그리므로, 모든 시각에 +9h를 더해 KST 벽시계로 보이게 한다.
// trades.entry_time/exit_time도 백엔드가 tz-aware KST(+09:00)로 저장해 실제 UTC 시점이므로 캔들과 같은 보정을 한다.
function toChartTime(value: Date | string): UTCTimestamp {
  return (Math.floor(new Date(value).getTime() / 1000) + KST_OFFSET_SEC) as UTCTimestamp;
}

// 마커는 봉 시각과 일치해야 하므로 초 단위 체결 시각을 1분봉 시각으로 내린다.
function toBarTime(value: Date | string): UTCTimestamp {
  const t = toChartTime(value);
  return (t - (t % 60)) as UTCTimestamp;
}

const won = (value: number) => value.toLocaleString("ko-KR");

export default function CandlestickChart({ candles, marker }: { candles: Candle[]; marker: TradeChartMarker }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 450,
      layout: {
        background: { type: ColorType.Solid, color: "#0f172a" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      rightPriceScale: { borderColor: "#334155", scaleMargins: { top: 0.1, bottom: 0.2 } },
      timeScale: { borderColor: "#334155", timeVisible: true, secondsVisible: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    });
    candleSeries.setData(
      candles.map((c) => ({ time: toChartTime(c.candle_time), open: c.open, high: c.high, low: c.low, close: c.close })),
    );

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volumeSeries.setData(
      candles.map((c) => ({
        time: toChartTime(c.candle_time),
        value: c.volume,
        color: c.close >= c.open ? `${UP}30` : `${DOWN}30`,
      })),
    );

    const markers: SeriesMarker<UTCTimestamp>[] = [
      {
        time: toBarTime(marker.buy_time),
        position: "belowBar",
        color: UP,
        shape: "arrowUp",
        text: `BUY ${won(marker.buy_price)}`,
      },
    ];
    if (marker.sell_time && marker.sell_price != null) {
      markers.push({
        time: toBarTime(marker.sell_time),
        position: "aboveBar",
        color: DOWN,
        shape: "arrowDown",
        text: `SELL ${won(marker.sell_price)}`,
      });
    }
    createSeriesMarkers(candleSeries, markers);

    if (marker.target_line != null) {
      candleSeries.createPriceLine({
        price: marker.target_line,
        color: `${UP}60`,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `목표 ${won(marker.target_line)}`,
      });
    }
    if (marker.stop_line != null) {
      candleSeries.createPriceLine({
        price: marker.stop_line,
        color: `${DOWN}60`,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `손절 ${won(marker.stop_line)}`,
      });
    }

    chart.timeScale().fitContent();

    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candles, marker]);

  if (candles.length === 0) {
    return (
      <div className="flex h-[450px] items-center justify-center rounded-xl border border-slate-700 bg-slate-900 text-sm text-slate-500">
        캔들 데이터 없음 — 에이전트 실행 후 1분봉이 조회되면 표시됩니다
      </div>
    );
  }

  return <div ref={containerRef} className="w-full overflow-hidden rounded-xl border border-slate-700" />;
}
