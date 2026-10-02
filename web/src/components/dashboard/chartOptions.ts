import { ColorType, type DeepPartial, type TimeChartOptions } from "lightweight-charts";

/** 손익 차트 공통 테마 (다크 배경, ₩ 가격 포맷) */
export const pnlChartOptions: DeepPartial<TimeChartOptions> = {
  autoSize: true,
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: "#94a3b8",
    attributionLogo: false,
  },
  grid: {
    vertLines: { color: "#1e293b" },
    horzLines: { color: "#1e293b" },
  },
  rightPriceScale: { borderVisible: false },
  timeScale: { borderVisible: false },
  localization: {
    priceFormatter: (value: number) => `₩${Math.round(value).toLocaleString()}`,
  },
};
