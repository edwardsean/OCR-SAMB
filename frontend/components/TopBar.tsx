"use client";
// The top bar (DESIGN.md): the wordmark, the Finance screens in the order of the work with a count where something
// waits, the teacher's one line, and the developers' screens under "Teknis" (still served by FastAPI).
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/client";
import type { Session } from "@/lib/types";

const NAV: [string, string, keyof Session | null][] = [
  ["/", "Beranda", null], ["/upload", "Unggah scan", null], ["/review", "Periksa order", "needs_you"],
  ["/label", "Jenis halaman", "unsure_left"], ["/bundles", "Berkas per SOR", null],
  ["/published", "Data terkirim", null], ["/batches", "Riwayat scan", null],
];
const TECH: [string, string][] = [
  ["/status", "Status sistem"], ["/context", "Konteks Jev"], ["/knowledge", "Pengetahuan AI"],
  ["/compare", "Bandingkan dengan v1"], ["/fields", "Daftar field"], ["/labels", "Semua label"],
];

export default function TopBar({ initial }: { initial: Session | null }) {
  const path = usePathname();
  const [s, setS] = useState<Session | null>(initial);
  const menu = useRef<HTMLDetailsElement>(null);

  useEffect(() => {                                   // the counts and the teacher's line, asked every few seconds
    let live = true;
    const ask = async () => {
      const a = await api.get<Session>("/session");
      if (live && a.ok) setS(a.data);
    };
    const t = setInterval(ask, 5000);
    return () => { live = false; clearInterval(t); };
  }, []);
  useEffect(() => {                                   // the Teknis menu closes when you click anywhere else
    const close = (e: MouseEvent) => { if (menu.current?.open && !menu.current.contains(e.target as Node)) menu.current.open = false; };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const on = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));
  return (
    <header className="top">
      <Link className="brand" href="/" title="Ke Beranda"><b>SAMB</b><small>Rekonsiliasi AR</small></Link>
      <nav className="main" aria-label="Menu utama">
        {NAV.map(([href, label, count]) => {
          const n = count && s ? (s[count] as number) : 0;
          return (
            <Link key={href} href={href} className={on(href) ? "on" : undefined} aria-current={on(href) ? "page" : undefined}>
              {label}
              {n ? (
                <span className={"nbadge" + (count === "unsure_left" ? " amber" : "")}
                      title={count === "needs_you" ? "order baru yang perlu Anda cek" : "halaman yang jenisnya belum pasti"}>{n}</span>
              ) : null}
            </Link>
          );
        })}
      </nav>
      <div className="top-right">
        {s?.teacher ? (
          <div className="teacher-now">
            <a className="teacher-badge" href="/knowledge" title="Guru AI belajar dari perbaikan yang Anda buat">{s.teacher}</a>
          </div>
        ) : null}
        <details className="techmenu" ref={menu}>
          <summary>Teknis</summary>
          <div className="menu">
            <p>Untuk tim pengembang</p>
            {/* plain links: these pages are FastAPI's, not this app's */}
            {TECH.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
          </div>
        </details>
      </div>
    </header>
  );
}
