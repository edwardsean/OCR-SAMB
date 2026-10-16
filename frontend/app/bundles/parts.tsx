"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, why } from "@/lib/client";
import { rp, tgl } from "@/lib/format";
import type { BundleDoc, Closest } from "@/lib/types";
import { fieldName, useWords } from "@/components/Words";
import { useReviewer } from "@/components/useReviewer";

/** A held document: why it waits, the line where its number is printed, and the form to confirm it. */
export function HeldCard({ h, batch }: { h: BundleDoc; batch: string }) {
  const w = useWords();
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [value, setValue] = useState(h.confirm?.value ?? "");
  const [crop, setCrop] = useState(true);
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {                      // a crop that failed before the page came alive never fired onError
    const i = img.current;
    if (i && i.complete && i.naturalWidth === 0) setCrop(false);
  }, []);
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
              <img ref={img} className="bx-crop" src={`/crop/${batch}/${h.page_from}/${h.confirm.field}`} alt="bagian halaman tempat nomornya tercetak" onError={() => setCrop(false)} />
            ) : <p className="small muted">AI tidak menemukan nomornya di halaman ini. <Link href={`/batches/${batch}/pages/${h.page_from}`}>Buka halamannya</Link> untuk mencarinya.</p>}
            <label className="bx-who">Nama Anda <input required autoComplete="name" value={by} onChange={(e) => setBy(e.target.value)} /></label>
            <ClosestOrders h={h} batch={batch} by={by} />
            <form className="bx-form" onSubmit={send}>
              <label>{fieldName(w, h.confirm.field, h.type)}, seperti tercetak{h.confirm.read && <> <span className="muted">(terbaca {h.confirm.read})</span></>}
                <input className="mono" required value={value} onChange={(e) => setValue(e.target.value)} /></label>
              <button type="submit" className="btn primary" disabled={busy || !by.trim()}>{busy ? "Menyimpan…" : "Pastikan"}</button>
            </form>
            {error && <p className="salah">{error}</p>}
            <p className="small muted">Setelah dipastikan, halaman dicek ulang dan masuk ke ordernya.</p>
          </>
        )}
      </div>
    </article>
  );
}

const HOW: Record<string, string> = { same: "nomornya sama", part: "nomor terbaca ada di dalamnya" };

/** The orders in Satellite closest to the number read (the user, 2026-10-16: "show the most similar ids, so the user
 *  can verify"): a hint to choose from, never linked by itself. Choosing one confirms the key that names that order
 *  (GET /bundles/closest gives each its `pick`). */
function ClosestOrders({ h, batch, by }: { h: BundleDoc; batch: string; by: string }) {
  const w = useWords();
  const router = useRouter();
  const [got, setGot] = useState<Closest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.get<Closest>(`/bundles/closest?batch=${encodeURIComponent(batch)}&page=${h.page_from}`)
      .then((a) => { if (live && a.ok) setGot(a.data); });
    return () => { live = false; };
  }, [batch, h.page_from]);
  if (!got || !got.candidates.length) return null;
  async function choose(sor: string, pick: { field: string; value: string }) {
    if (!by.trim()) { setError("Isi Nama Anda dulu."); return; }
    setBusy(sor); setError(null);
    const a = await api.post("/bundles/confirmations", { batch, page: h.page_from, field: pick.field, value: pick.value, by });
    setBusy(null);
    if (!a.ok) setError(why(a)); else router.refresh();
  }
  const doc = w.DOC[h.type] ?? h.type;
  return (
    <div className="bx-near">
      <h4>Order paling mirip di Satellite</h4>
      <ul>
        {got.candidates.map((c) => {
          const [a, b] = c.mark ?? [0, 0];
          return (
            <li key={c.sor} className={c.lacks ? "lacks" : undefined}>
              <div className="bx-near-main">
                <p><b className="mono">{c.sor}</b> <span>{c.customer}</span></p>
                <p className="small muted">
                  {got.key === "cpo_no" && <>CPO <span className="mono">{c.mark ? <>{c.cpo.slice(0, a)}<mark>{c.cpo.slice(a, b)}</mark>{c.cpo.slice(b)}</> : c.cpo}</span> · </>}
                  {tgl(c.date, false)} · {rp(c.total)} · {c.status}</p>
                <p className="bx-near-tags">
                  <span>{HOW[c.how] ?? `beda ${c.how.split(":")[1]} karakter`}</span>
                  {c.lacks && <span className="ok">order ini belum punya {doc}</span>}
                  {c.shared.length > 0 && <span className="warn">CPO sama dengan {c.shared.join(", ")}</span>}
                </p>
              </div>
              {c.pick ? <button type="button" className="btn" disabled={!!busy} onClick={() => choose(c.sor, c.pick!)}>
                {busy === c.sor ? "Menyimpan…" : "Ini ordernya"}</button>
                : <span className="small muted" title="Nomor CPO ini dipakai beberapa order">tidak bisa dipilih</span>}
            </li>
          );
        })}
      </ul>
      {error && <p className="salah">{error}</p>}
      <p className="small muted">Atau ketik nomornya seperti tercetak:</p>
    </div>
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
