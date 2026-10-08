"use client";
// One page to label: the page (click for full size, arrows move between its neighbours), and the choices (keys 1–8,
// Enter saves). The machine's guess is never shown, so it can't bias the answer.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, why } from "@/lib/client";
import { cx } from "@/lib/format";
import type { LabelData } from "@/lib/types";
import { useWords } from "@/components/Words";

export default function LabelForm({ d }: { d: LabelData }) {
  const w = useWords();
  const router = useRouter();
  const p = d.p!, page = d.page!, near = d.near ?? [], types = d.types ?? [];
  const [label, setLabel] = useState(d.existing?.label ?? "");
  const [customer, setCustomer] = useState(d.existing?.customer ?? "");
  const [note, setNote] = useState(d.existing?.note ?? "");
  const [who, setWho] = useState(d.existing?.labelled_by ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vi, setVi] = useState(-1);
  const [actual, setActual] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null), form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!d.existing?.labelled_by) try { setWho(localStorage.getItem("labeller") || ""); } catch { /* not kept */ }
  }, [d.existing]);

  function view(n: number) {
    const i = near.findIndex((x) => x.page_no === n);
    setVi(i); setActual(false);
    dialog.current?.showModal();
  }
  const step = (k: number) => near.length && setVi((i) => (i + k + near.length) % near.length);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (dialog.current?.open) {
        if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
        else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
        else if (e.key === "z" || e.key === "Z") setActual((a) => !a);
        return;                                          // never let 1–8 or Enter reach the form while viewing
      }
      const tag = (document.activeElement?.tagName ?? "").toUpperCase();
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= types.length) { setLabel(types[n - 1].key); e.preventDefault(); }
      if (e.key === "Enter" && label) form.current?.requestSubmit();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!label) return;
    setBusy(true); setError(null);
    try { localStorage.setItem("labeller", who); } catch { /* not kept */ }
    const a = await api.post("/labels", { batch: d.batch, page, label, customer, note, labelled_by: who });
    if (!a.ok) { setBusy(false); setError(why(a)); return; }
    router.push(d.upload ? `/label?upload=${d.upload.id}&batch=${encodeURIComponent(d.batch!)}&after=${page}&saved=${page}`
      : `/label?batch=${encodeURIComponent(d.batch!)}&saved=${page}`);
    router.refresh();
  }

  const cur = vi >= 0 ? near[vi] : null;
  const inUpload = d.upload ? `upload=${d.upload.id}&` : "";              // opened from a batch's step 2
  return (
    <>
      <div className="labelv">
        <section className="img">
          <h2>Halaman {page} <span className="muted">dari {d.total}</span>{" "}
            {(p.quality_flags ?? []).map((f) => <span key={f} className={`badge ${f}`}>{w.FLAG[f] ?? f}</span>)}
            <span className="tabs"><button type="button" className="linkbtn" onClick={() => view(page)}>lihat ukuran penuh</button></span></h2>
          <button type="button" className="mainimg" title="Klik untuk melihat ukuran penuh" onClick={() => view(page)}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/img/${p.upright_path || p.original_path}`} alt={`halaman ${page}`} /></button>
          <h2 style={{ marginTop: 14 }}>Halaman di sekitarnya</h2>
          <div className="near">
            {near.map((n) => (
              <button key={n.page_no} type="button" className={cx(n.page_no === page && "cur")} onClick={() => view(n.page_no)}
                      title={`Halaman ${n.page_no}${n.type ? " · " + (n.type === "unsure" ? "belum pasti" : w.DOC[n.type] ?? n.type) : ""}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`/img/${n.thumb}`} alt={`halaman ${n.page_no}`} loading="lazy" />
                <span>{n.page_no} {n.type && <i className={`tchip t-${n.type}`}>{n.type === "unsure" ? "?" : w.DOC_SHORT[n.type] ?? n.type}</i>}</span></button>
            ))}
          </div>
        </section>

        <section>
          <form ref={form} onSubmit={save}>
            <h2>1. Jenis dokumen</h2>
            <p className="muted small">Klik, atau tekan tombol 1–8.</p>
            <div className="typepick">
              {types.map((t, i) => (
                <label className="tp" key={t.key}>
                  <input type="radio" name="label" value={t.key} checked={label === t.key} onChange={() => setLabel(t.key)} required />
                  <span className="num">{i + 1}</span><span className={`tchip t-${t.key}`}>{t.key}</span>
                  <span><strong>{t.name}</strong><br /><em>{t.what}</em></span>
                </label>
              ))}
            </div>
            <h2>2. Pelanggan</h2>
            <p className="muted small">Boleh dikosongkan.</p>
            <input className="txt" list="customers" placeholder="mis. Indomaret" value={customer} onChange={(e) => setCustomer(e.target.value)} />
            <datalist id="customers">{(d.customers ?? []).map((c) => <option key={c} value={c} />)}</datalist>
            <h2>3. Catatan</h2>
            <p className="muted small">Boleh dikosongkan. Mis. &quot;tanpa judul; SAMB sebagai pemasok&quot;.</p>
            <textarea className="txt" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
            <h2>Nama Anda</h2>
            <input className="txt" placeholder="diingat di komputer ini" value={who} onChange={(e) => setWho(e.target.value)} />
            <div className="actions" style={{ marginTop: 14 }}>
              <Link className="ghostbtn" href={`/label?${inUpload}batch=${d.batch}&after=${page}`}>Lewati halaman ini</Link>
              <button type="submit" className="btn primary" disabled={busy || !label}>{busy ? "Menyimpan…" : "Simpan dan lanjut"}</button>
            </div>
            {error && <p className="salah">{error}</p>}
            {d.existing && <p className="note">Sudah dijawab <strong>{w.DOC[d.existing.label] ?? d.existing.label}</strong> oleh{" "}
              {d.existing.labelled_by || "seseorang"}. Menyimpan akan mengganti jawaban itu.</p>}
          </form>
        </section>
      </div>

      <dialog ref={dialog} className="viewer" aria-label="Halaman ukuran penuh" onClick={(e) => { if (e.target === dialog.current) dialog.current?.close(); }}>
        <div className="vbar">
          <button type="button" title="Halaman sebelumnya (←)" onClick={() => step(-1)}>←</button>
          <strong>{cur ? `Halaman ${cur.page_no}${cur.type ? " · " + (cur.type === "unsure" ? "belum pasti" : cur.type) : ""}${cur.page_no === page ? " (halaman ini)" : ""}` : ""}</strong>
          <button type="button" title="Halaman berikutnya (→)" onClick={() => step(1)}>→</button>
          <span className="vsp" />
          <button type="button" title="Pas layar / ukuran asli (Z)" onClick={() => setActual((a) => !a)}>{actual ? "Pas layar" : "Ukuran asli"}</button>
          {cur && cur.page_no !== page && <Link href={`/label?${inUpload}batch=${d.batch}&page=${cur.page_no}`} onClick={() => dialog.current?.close()}>Tentukan jenis halaman ini</Link>}
          <button type="button" title="Tutup (Esc)" onClick={() => dialog.current?.close()}>Tutup ✕</button>
        </div>
        <div className={cx("vbody", actual && "actual")}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {cur?.full && <img src={`/img/${cur.full}`} alt={`halaman ${cur.page_no}`} onClick={() => setActual((a) => !a)} />}
        </div>
      </dialog>
    </>
  );
}
