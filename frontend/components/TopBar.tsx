"use client";
// The top bar (DESIGN.md): the wordmark, one Finance tab, Batch, with how many batches wait for a person (the user,
// 2026-10-05: "cant we just show a "Batches" tab only?"), Unggah batch, a search for any order, batch or file, the
// teacher's one line, and under "Teknis" the screens over every batch at once and the developers' screens.
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { api } from "@/lib/client";
import type { Session } from "@/lib/types";

const ALL: [string, string][] = [                   // this app's screens over every batch (a batch's steps are the way in)
  ["/review", "Periksa order"], ["/label", "Jenis halaman"], ["/bundles", "Berkas per SOR"], ["/published", "Data terkirim"],
];
const TECH: [string, string][] = [
  ["/status", "Status sistem"], ["/settings", "Model & kunci API"], ["/product-codes", "Kode produk pelanggan"],
  ["/context", "Konteks klasifikasi"],
  ["/knowledge", "Pengetahuan AI"], ["/fields", "Daftar field"], ["/labels", "Semua label"],
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

  const upload = path === "/upload" || path.startsWith("/upload/");
  const batch = !upload && path !== "/cari" && !ALL.some(([h]) => path === h);
  const n = s?.batches_need ?? 0;
  return (
    <header className="top">
      <Link className="brand" href="/" title="Ke daftar batch"><b>SAMB</b><small>Rekonsiliasi AR</small></Link>
      <nav className="main" aria-label="Menu utama">
        <Link href="/" className={batch ? "on" : undefined} aria-current={batch ? "page" : undefined}>
          Batch{n ? <span className="nbadge" title="batch yang menunggu Anda">{n}</span> : null}</Link>
        <Link href="/upload" className={upload ? "on" : undefined} aria-current={upload ? "page" : undefined}>Unggah batch</Link>
        <Suspense fallback={null}><Find /></Suspense>
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
            <p>Semua batch sekaligus</p>
            {ALL.map(([href, label]) => <Link key={href} href={href} className={path === href ? "on" : undefined}
                                              onClick={() => menu.current?.removeAttribute("open")}>{label}</Link>)}
            <p>Untuk tim pengembang</p>
            {/* plain links: these pages are FastAPI's, not this app's */}
            {TECH.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
          </div>
        </details>
      </div>
    </header>
  );
}

/** "Cari SOR, PO, atau file": a plain GET form to /cari, so it works before the page's script has loaded. */
function Find() {
  const q = useSearchParams().get("q") ?? "";
  const path = usePathname();
  return (
    <form className="find" action="/cari" role="search">
      <input type="search" name="q" key={path + q} defaultValue={path === "/cari" ? q : ""} placeholder="Cari SOR, PO, atau file"
             aria-label="Cari order, batch, atau file" />
    </form>
  );
}
