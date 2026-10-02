/** 조건식 매매 스타일(hts_conditions.style)별 텍스트 색상 */
export const STYLE_COLORS = {
  "매도벽 흡수": "text-blue-400",
  "분봉 구조 전환": "text-purple-400",
  "대장 재돌파": "text-amber-400",
  "선행 수급": "text-cyan-400",
  "기관 동반 돌파": "text-emerald-400",
  "갭상승 눌림 반등": "text-rose-400",
  기본: "text-slate-400",
} as const;

export type TradingStyle = keyof typeof STYLE_COLORS;

export function styleColor(style: string | null | undefined): string {
  return STYLE_COLORS[(style ?? "기본") as TradingStyle] ?? STYLE_COLORS["기본"];
}

/** 목록 페이지 기본 페이지 크기 */
export const PAGE_SIZE = 20;

/** 신호목록 페이지 크기 */
export const SIGNALS_PAGE_SIZE = 30;
