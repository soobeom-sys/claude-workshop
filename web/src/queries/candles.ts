import "server-only";
import { fetchMinuteCandles } from "@/lib/kiwoom";

export type Candle = {
  candle_time: string; // ISO 8601 (+09:00)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const kstDate = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Date → KST 기준 YYYYMMDD */
function toKstYmd(value: Date | string) {
  return kstDate.format(new Date(value)).replaceAll("-", "");
}

/** cntr_tm "YYYYMMDDHHmm[ss]"(KST) → ISO 문자열(+09:00) */
function cntrTmToIso(tm: string) {
  const s = tm.padEnd(14, "0");
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(8, 10)}:${s.slice(10, 12)}:${s.slice(12, 14)}+09:00`;
}

// 키움은 등락을 음수 부호로 표기하므로 절댓값을 쓴다.
const price = (value: string) => Math.abs(parseInt(value, 10)) || 0;

/** 진입일 당일 1분봉. 키움 API가 실패해도 페이지가 죽지 않도록 빈 배열을 반환한다. */
export async function getCandlesForTrade(stockCode: string, entryTime: Date | string): Promise<Candle[]> {
  const baseDt = toKstYmd(entryTime);
  try {
    const rows = await fetchMinuteCandles(stockCode, baseDt);
    return rows
      .filter((r) => r.cntr_tm?.startsWith(baseDt))
      .map((r) => ({
        candle_time: cntrTmToIso(r.cntr_tm),
        open: price(r.open_pric),
        high: price(r.high_pric),
        low: price(r.low_pric),
        close: price(r.cur_prc),
        volume: price(r.trde_qty),
      }))
      .filter((c) => c.open > 0 && c.close > 0)
      .sort((a, b) => a.candle_time.localeCompare(b.candle_time));
  } catch (error) {
    console.error(`[candles] ${stockCode} ${baseDt} 분봉 조회 실패`, error);
    return [];
  }
}
