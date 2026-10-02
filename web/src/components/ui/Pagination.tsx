import Link from "next/link";

type PaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  /** 현재 경로 (예: "/trades") */
  basePath: string;
  /** page 외에 유지할 쿼리스트링 (필터 등) */
  params?: Record<string, string | undefined>;
};

const BUTTON = "rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600 disabled:opacity-30";

function pageHref(basePath: string, params: PaginationProps["params"], page: number) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value) qs.set(key, value);
  }
  if (page > 1) qs.set("page", String(page));
  const query = qs.toString();
  return query ? `${basePath}?${query}` : basePath;
}

export default function Pagination({ page, pageSize, total, basePath, params }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="flex items-center justify-between border-t border-slate-700 px-4 py-3">
      <p className="text-sm text-slate-400">
        전체 {total.toLocaleString("ko-KR")}건 중 {from.toLocaleString("ko-KR")}–{to.toLocaleString("ko-KR")}
      </p>
      <div className="flex items-center gap-2">
        {page > 1 ? (
          <Link href={pageHref(basePath, params, page - 1)} className={BUTTON}>
            이전
          </Link>
        ) : (
          <button type="button" className={BUTTON} disabled>
            이전
          </button>
        )}
        <span className="text-sm text-slate-400">
          {page} / {totalPages}
        </span>
        {page < totalPages ? (
          <Link href={pageHref(basePath, params, page + 1)} className={BUTTON}>
            다음
          </Link>
        ) : (
          <button type="button" className={BUTTON} disabled>
            다음
          </button>
        )}
      </div>
    </div>
  );
}
