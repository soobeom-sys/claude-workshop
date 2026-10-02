import type { Metadata } from "next";
import Sidebar from "@/components/layout/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kiwoom Agent",
  description: "퀀트트레이딩 대시보드",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="dark">
      <body className="bg-[#0f172a] text-slate-100 antialiased">
        <Sidebar />
        <main className="ml-56 p-6">{children}</main>
      </body>
    </html>
  );
}
