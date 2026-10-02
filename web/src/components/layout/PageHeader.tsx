import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  description?: string;
  /** 제목 오른쪽에 놓을 필터/버튼 등 */
  actions?: ReactNode;
};

export default function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-2xl font-bold text-white">{title}</h2>
        {description && <p className="mt-1 text-sm text-slate-400">{description}</p>}
      </div>
      {actions}
    </div>
  );
}
