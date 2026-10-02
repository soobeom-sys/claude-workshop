import Form from "next/form";
import Link from "next/link";
import type { SignalFilterParams } from "@/queries/signals";

const LABEL = "flex flex-col gap-1 text-xs text-slate-400";
const INPUT =
  "rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-slate-500 focus:outline-none";

type SignalFiltersProps = {
  filters: SignalFilterParams;
  conditionNames: string[];
};

/** GET 폼: 제출하면 /signals?start=...&end=... 로 이동하고 page는 1로 초기화된다. */
export default function SignalFilters({ filters, conditionNames }: SignalFiltersProps) {
  return (
    // key로 URL 필터가 바뀔 때 비제어 입력값을 다시 채운다.
    <Form
      key={JSON.stringify(filters)}
      action="/signals"
      className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-slate-700 bg-slate-800/50 p-4"
    >
      <label className={LABEL}>
        시작일
        <input type="date" name="start" defaultValue={filters.startDate} className={INPUT} />
      </label>
      <label className={LABEL}>
        종료일
        <input type="date" name="end" defaultValue={filters.endDate} className={INPUT} />
      </label>
      <label className={LABEL}>
        조건식
        <select name="condition" defaultValue={filters.conditionName ?? ""} className={INPUT}>
          <option value="">전체</option>
          {conditionNames.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label className={LABEL}>
        AI 판단
        <select name="action" defaultValue={filters.action ?? ""} className={INPUT}>
          <option value="">전체</option>
          <option value="BUY">BUY</option>
          <option value="SKIP">SKIP</option>
        </select>
      </label>
      <label className={LABEL}>
        종목코드
        <input
          type="text"
          name="code"
          defaultValue={filters.stockCode}
          placeholder="005930"
          inputMode="numeric"
          className={`${INPUT} w-28`}
        />
      </label>
      <div className="flex gap-2">
        <button type="submit" className="rounded-lg bg-slate-700 px-4 py-1.5 text-sm text-white hover:bg-slate-600">
          조회
        </button>
        <Link
          href="/signals"
          className="rounded-lg px-4 py-1.5 text-sm text-slate-400 hover:bg-slate-700/60 hover:text-slate-200"
        >
          초기화
        </Link>
      </div>
    </Form>
  );
}
