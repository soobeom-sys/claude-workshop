const BADGE_BASE = "inline-block rounded px-2 py-0.5 text-xs font-medium";

// 백엔드(trading/models.py ExitReason)는 DB에 한글 값을 저장하므로 영문 키와 한글 값을 모두 받는다.
const EXIT_REASONS = {
  TARGET: { label: "목표가", className: "bg-emerald-900/60 text-emerald-300" },
  TAKE_PROFIT: { label: "익절", className: "bg-emerald-900/60 text-emerald-300" },
  STOP_LOSS: { label: "손절", className: "bg-red-900/60 text-red-300" },
  TRAILING_STOP: { label: "트레일링", className: "bg-amber-900/60 text-amber-300" },
  TIME_LIMIT: { label: "시간초과", className: "bg-slate-700 text-slate-300" },
  MARKET_CLOSE: { label: "장마감", className: "bg-slate-700 text-slate-300" },
} as const;

type ExitReasonKey = keyof typeof EXIT_REASONS;

const EXIT_REASON_ALIASES: Record<string, ExitReasonKey> = Object.fromEntries(
  Object.entries(EXIT_REASONS).flatMap(([key, { label }]) => [
    [key, key],
    [label, key],
  ]),
) as Record<string, ExitReasonKey>;

export function ExitReasonBadge({ reason }: { reason: string | null }) {
  if (!reason) return <span className="text-slate-500">-</span>;
  const key = EXIT_REASON_ALIASES[reason];
  const style = key ? EXIT_REASONS[key] : { label: reason, className: "bg-slate-700 text-slate-300" };
  return <span className={`${BADGE_BASE} ${style.className}`}>{style.label}</span>;
}

// signals.ai_action은 main.py가 "매수"/"패스"로 저장한다.
const ACTIONS = {
  BUY: { label: "BUY", className: "bg-emerald-900/60 text-emerald-300" },
  SKIP: { label: "SKIP", className: "bg-slate-700 text-slate-300" },
} as const;

const ACTION_ALIASES: Record<string, keyof typeof ACTIONS> = {
  BUY: "BUY",
  매수: "BUY",
  SKIP: "SKIP",
  패스: "SKIP",
};

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-xs text-slate-500">대기중</span>;
  const key = ACTION_ALIASES[action];
  const style = key ? ACTIONS[key] : { label: action, className: "bg-slate-700 text-slate-300" };
  return <span className={`${BADGE_BASE} ${style.className}`}>{style.label}</span>;
}
