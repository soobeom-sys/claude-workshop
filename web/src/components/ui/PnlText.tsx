type PnlValue = number | null | undefined;

export function pnlColor(value: PnlValue) {
  if (value == null || value === 0) return "text-slate-400";
  return value > 0 ? "text-emerald-400" : "text-red-400";
}

export function pnlBgColor(value: PnlValue) {
  if (value == null || value === 0) return "bg-slate-800";
  return value > 0 ? "bg-emerald-950/50" : "bg-red-950/50";
}

function sign(value: number) {
  return value > 0 ? "+" : value < 0 ? "-" : "";
}

/** 수익률: ±N.NN% */
export function PnlPct({ value, className = "" }: { value: PnlValue; className?: string }) {
  if (value == null) return <span className={`text-slate-400 ${className}`}>-</span>;
  return (
    <span className={`${pnlColor(value)} ${className}`}>
      {sign(value)}
      {Math.abs(value).toFixed(2)}%
    </span>
  );
}

/** 금액: ±N,NNN원 */
export function PnlAmount({ value, className = "" }: { value: PnlValue; className?: string }) {
  if (value == null) return <span className={`text-slate-400 ${className}`}>-</span>;
  return (
    <span className={`${pnlColor(value)} ${className}`}>
      {sign(value)}
      {Math.abs(value).toLocaleString("ko-KR")}원
    </span>
  );
}

/** 수익률 + 금액 세로 스택 (테이블 셀용) */
export function PnlCell({ pct, amount }: { pct: PnlValue; amount: PnlValue }) {
  return (
    <div className="flex flex-col leading-tight">
      <PnlPct value={pct} className="font-medium" />
      <PnlAmount value={amount} className="text-xs" />
    </div>
  );
}
