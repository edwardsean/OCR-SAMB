"use client";
// "Ubah jenis" (the user, 2026-10-08): a person says the classifier got this page's type wrong. The answer is a label,
// the same as on the Jenis halaman screen (POST /labels): the page is processed again as that type, and the teacher
// learns from the miss (services/api/relabel.py). Afterwards the status bar says what the correction is doing.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, why } from "@/lib/client";
import type { PageDetail } from "@/lib/types";
import { useWords } from "@/components/Words";
import { useReviewer } from "@/components/useReviewer";
import LessonStatus from "@/components/LessonStatus";

export default function Relabel({ batch, d }: { batch: string; d: PageDetail }) {
  const w = useWords();
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = d.page.doc_type;
  const page = d.page.page_no;
  const machine = d.machine?.status === "decided" ? d.machine.doc_type : null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const a = await api.post("/labels", { batch, page, label, note, labelled_by: by, customer: "" });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    setOpen(false); setLabel(""); setNote("");
    router.refresh();
  }

  return (
    <div className="rl">
      {d.label ? (
        <p className="small rl-who">Jenis ditentukan oleh {d.label.labelled_by || "seseorang"}
          {machine && machine !== d.label.label && <>; sistem tadinya menjawab <span className={`tchip t-${machine}`}>{w.DOC_SHORT[machine] ?? machine}</span></>}.
          {!open && !d.relabel_refused && <> <button type="button" className="linkbtn" onClick={() => setOpen(true)}>Ubah lagi</button></>}</p>
      ) : !open && (
        d.relabel_refused ? <p className="small muted">{d.relabel_refused}</p>
          : <p className="small">Jenisnya salah? <button type="button" className="linkbtn" onClick={() => setOpen(true)}>Ubah jenis</button></p>
      )}
      {d.label && !open && <LessonStatus key={d.label.labelled_at} batch={batch} page={page} type />}

      {open && (
        <form className="rl-form" onSubmit={save}>
          <h2>Ini dokumen apa?</h2>
          <p className="small muted">Halaman ini akan diproses ulang sebagai jenis yang Anda pilih. Bila sistem salah, guru AI
            mempelajari kenapa; bila perbaikannya lolos uji ulang, sistem langsung memakainya untuk halaman berikutnya.</p>
          <div className="typepick">
            {w.LABEL_TYPES.map((t) => (
              <label className="tp" key={t.key}>
                <input type="radio" name="label" value={t.key} checked={label === t.key} onChange={() => setLabel(t.key)}
                       disabled={t.key === now} required />
                <span className={`tchip t-${t.key}`}>{w.DOC_SHORT[t.key] ?? t.key}</span>
                <span><strong>{t.name}</strong>{t.key === now && <span className="muted"> (sekarang)</span>}<br /><em>{t.what}</em></span>
              </label>
            ))}
          </div>
          <label className="small" htmlFor="rl-note">Kenapa? Boleh dikosongkan; guru AI membaca catatan ini.</label>
          <textarea id="rl-note" className="txt" rows={2} placeholder="mis. Surat Pesanan dari Hero, judulnya tulisan tangan"
                    value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="ws-retry">
            <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
            <button className="btn primary" disabled={busy || !label}>{busy ? "Menyimpan…" : "Simpan jenis"}</button>
            <button type="button" className="linkbtn" onClick={() => { setOpen(false); setError(null); }}>Batal</button>
          </div>
          {error && <p className="salah">{error}</p>}
        </form>
      )}
    </div>
  );
}
