"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, why } from "@/lib/client";
import type { BundleDoc } from "@/lib/types";
import { fieldName, useWords } from "@/components/Words";
import { useReviewer } from "@/components/useReviewer";

/** A held document: why it waits, the line where its number is printed, and the form to confirm it. */
export function HeldCard({ h, batch }: { h: BundleDoc; batch: string }) {
  const w = useWords();
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [value, setValue] = useState(h.confirm?.value ?? "");
  const [crop, setCrop] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const a = await api.post("/bundles/confirmations", { batch, page: h.page_from, field: h.confirm!.field, value, by });
    setBusy(false);
    if (!a.ok) setError(why(a)); else router.refresh();
  }
  return (
    <article className="bx-hcard">
      <Link className="bx-tile small" href={`/batches/${batch}/pages/${h.page_from}`} title={`${h.scan ?? ""}, halaman ${h.page_from}`}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {h.thumb && <img src={h.thumb} alt="" loading="lazy" />}
        <span className={`tchip t-${h.type}`}>{w.DOC_SHORT[h.type] ?? h.type}</span><span>hal. {h.page_from}{h.page_to !== h.page_from ? `–${h.page_to}` : ""}</span>
        {h.scan && <small className="bx-scan" title={`${h.upload ?? ""} ${h.scan}`}>{h.scan}</small>}
        {h.upload && <small className="bx-scan" title={`diunggah ${h.uploaded_by ?? ""}`}>{h.upload}</small>}</Link>
      <div className="bx-hbody">
        <p className="bx-hwhy"><b>{w.DOC[h.type] ?? h.type}</b>: {h.why}</p>
        {h.suggested_sor && <p className="small muted">Mungkin order <b className="mono">{h.suggested_sor}</b> (hanya petunjuk, tidak otomatis dihubungkan).</p>}
        {h.confirm && (
          <>
            {crop ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img className="bx-crop" src={`/crop/${batch}/${h.page_from}/${h.confirm.field}`} alt="bagian halaman tempat nomornya tercetak" onError={() => setCrop(false)} />
            ) : <p className="small muted">Potongan halamannya tidak ada: buka halamannya.</p>}
            <form className="bx-form" onSubmit={send}>
              <label>{fieldName(w, h.confirm.field, h.type)}, seperti tercetak{h.confirm.read && <> <span className="muted">(terbaca {h.confirm.read})</span></>}
                <input className="mono" required value={value} onChange={(e) => setValue(e.target.value)} /></label>
              <label>Nama Anda <input required autoComplete="name" value={by} onChange={(e) => setBy(e.target.value)} /></label>
              <button type="submit" className="btn primary" disabled={busy}>{busy ? "Menyimpan…" : "Pastikan"}</button>
            </form>
            {error && <p className="salah">{error}</p>}
            <p className="small muted">Setelah dipastikan, halaman dicek ulang dan masuk ke ordernya.</p>
          </>
        )}
      </div>
    </article>
  );
}

/** The orders' list with a search by customer or SOR, as you type. */
export function BundleSearch({ n, children }: { n: number; children: ReactNode }) {
  const [q, setQ] = useState("");
  const [none, setNone] = useState(false);
  useEffect(() => {                                 // the orders are drawn on the server; the search only hides
    let shown = 0;
    document.querySelectorAll<HTMLElement>("article.bx").forEach((a) => {
      const on = !q || (a.dataset.q ?? "").includes(q.trim().toLowerCase());
      a.hidden = !on;
      if (on) shown++;
    });
    setNone(!!n && shown === 0);
  }, [q, n, children]);
  return (
    <>
      <div className="bx-tools" id="berkas">
        <h2 className="sect">Berkas order ({n})</h2>
        {n > 3 && <input type="search" id="bx-find" placeholder="Cari pelanggan atau nomor SOR" aria-label="Cari berkas" value={q} onChange={(e) => setQ(e.target.value)} />}
      </div>
      {children}
      {!n && <p className="kosong">Belum ada berkas di scan ini. Halaman disatukan per order setelah AI selesai membacanya.</p>}
      {none && <p className="muted small">Tidak ada berkas yang cocok dengan pencarian.</p>}
    </>
  );
}
