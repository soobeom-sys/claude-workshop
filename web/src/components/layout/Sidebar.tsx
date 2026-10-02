"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type NavItem = {
  href: string;
  label: string;
  icon: ReactNode;
};

const iconProps = {
  className: "h-5 w-5",
  fill: "none",
  viewBox: "0 0 24 24",
  stroke: "currentColor",
  strokeWidth: 1.8,
  "aria-hidden": true,
} as const;

const NAV_ITEMS: NavItem[] = [
  {
    href: "/dashboard",
    label: "대시보드",
    icon: (
      <svg {...iconProps}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 5h6v6H4zM14 5h6v4h-6zM14 13h6v6h-6zM4 15h6v4H4z" />
      </svg>
    ),
  },
  {
    href: "/trades",
    label: "매매기록",
    icon: (
      <svg {...iconProps}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7 16V4m0 0L3 8m4-4 4 4M17 8v12m0 0 4-4m-4 4-4-4" />
      </svg>
    ),
  },
  {
    href: "/signals",
    label: "신호목록",
    icon: (
      <svg {...iconProps}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13 3 4 14h7l-1 7 9-11h-7l1-7z" />
      </svg>
    ),
  },
  {
    href: "/conditions",
    label: "조건식",
    icon: (
      <svg {...iconProps}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 5h18l-7 8v6l-4 2v-8L3 5z" />
      </svg>
    ),
  },
  {
    href: "/daily-pnl",
    label: "일일손익",
    icon: (
      <svg {...iconProps}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 3v3m8-3v3M4 9h16M5 6h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm3 10 3-3 2 2 3-3" />
      </svg>
    ),
  },
];

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 flex h-screen w-56 flex-col border-r border-slate-700 bg-slate-900">
      <div className="border-b border-slate-700 px-4 py-5">
        <h1 className="text-lg font-bold text-white">Kiwoom Agent</h1>
        <p className="text-xs text-slate-500">퀀트트레이딩 대시보드</p>
      </div>

      <nav className="flex-1 space-y-1 px-2 py-4">
        {NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${
                active
                  ? "bg-blue-600/20 text-blue-400"
                  : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              {item.icon}
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-slate-700 px-4 py-3 text-xs text-slate-600">v1.0.0</div>
    </aside>
  );
}
