import Link from "next/link";
import PageHeader from "@/components/layout/PageHeader";
import Pagination from "@/components/ui/Pagination";
import TradeTable from "@/components/ui/TradeTable";
import { PAGE_SIZE } from "@/constants/trading";
import { parsePage } from "@/lib/format";
import { getTrades, type TradeStatus } from "@/queries/trades";

const STATUS_TABS: { value?: TradeStatus; label: string }[] = [
  { label: "전체" },
  { value: "open", label: "보유중" },
  { value: "closed", label: "청산" },
];

export default async function TradesPage({ searchParams }: PageProps<"/trades">) {
  const params = await searchParams;
  const page = parsePage(params.page);
  const status = params.status === "open" || params.status === "closed" ? params.status : undefined;
  const { rows, total } = await getTrades({ page, pageSize: PAGE_SIZE, status });

  return (
    <>
      <PageHeader
        title="매매기록"
        description="체결된 매수와 청산 결과"
        actions={
          <div className="flex gap-1 rounded-lg bg-slate-800 p-1">
            {STATUS_TABS.map((tab) => (
              <Link
                key={tab.label}
                href={tab.value ? `/trades?status=${tab.value}` : "/trades"}
                className={`rounded px-3 py-1 text-sm ${
                  status === tab.value ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {tab.label}
              </Link>
            ))}
          </div>
        }
      />
      <section className="rounded-lg border border-slate-700 bg-slate-800">
        <TradeTable rows={rows} emptyMessage="매매 기록이 없습니다." />
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} basePath="/trades" params={{ status }} />
      </section>
    </>
  );
}
