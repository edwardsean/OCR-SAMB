"use client";
// "Baca ulang" (2026-10-15; the user: "a restart button to select pages to restart or restart all"): pages read again
// FROM SCRATCH — a new AI OCR copy and every AI step after it (about 5 AI calls a page); people's labels and
// corrections stay (POST /uploads/{id}/restart). A page that can't be (its order is sent, it is being read, its file is
// stopped) is shown greyed with why. Asks once, signed with the name, with the calls it will cost.
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";
import type { PageNow } from "@/lib/types";

const CALLS = 5;
type Done = { restarted: number; calls: number; left: { file_name: string; page_no: number; why: string }[] };
const key = (p: { batch_id: string; page_no: number }) => `${p.batch_id}/${p.page_no}`;

export default function RestartPicker({ upload, pages, notNow }: { upload: number; pages: PageNow[]; notNow: string | null }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const can = useMemo(() => pages.filter((p) => !p.restart), [pages]);
  const files = new Set(pages.map((p) => p.file_name)).size;

  const toggle = (k: string) => setPicked((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const all = picked.size === can.length && can.length > 0;

  async function send(which: "picked" | "all") {
    if (!by.trim()) { setError("Isi nama Anda dulu."); return; }
    const n = which === "all" ? can.length : picked.size;
    if (!window.confirm(`Baca ulang ${n} halaman dari awal? Sekitar ${n * CALLS} panggilan AI.`)) return;
    setBusy(true); setError(null); setNote(null);
    const body = which === "all" ? { by } : { by, pages: pages.filter((p) => picked.has(key(p))).map((p) => ({ batch_id: p.batch_id, page_no: p.page_no })) };
    const a = await api.post<{ result?: Done }>(`/uploads/${upload}/restart`, body);
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    const r = a.data.result!;
    setNote(`${r.restarted} halaman dibaca ulang dari awal (±${r.calls} panggilan AI).`
      + (r.left.length ? ` ${r.left.length} tidak: ${r.left.slice(0, 4).map((l) => `hal. ${l.page_no} (${l.why})`).join(", ")}${r.left.length > 4 ? ", …" : ""}.` : ""));
    setPicked(new Set());
    router.refresh();
  }

  return (
    <details className="ws-files rs">
      <summary className="ws-grp">↻ Baca ulang halaman…</summary>
      <p className="small muted">Halaman dibaca ulang <b>dari awal</b>: salinan AI OCR baru dan semua langkah AI sesudahnya,
        sekitar {CALLS} panggilan AI per halaman. Label dan koreksi dari orang tetap dipakai; ordernya diperiksa ulang.</p>
      {notNow && <p className="small salah">Belum bisa sekarang: {notNow}</p>}
      <div className="rs-bar">
        <label className="small"><input type="checkbox" checked={all} disabled={!can.length}
          onChange={() => setPicked(all ? new Set() : new Set(can.map(key)))} /> Pilih semua yang bisa ({can.length})</label>
        <span className="small muted">{picked.size} dipilih{picked.size ? ` · ±${picked.size * CALLS} panggilan AI` : ""}</span>
      </div>
      <ul className="rs-grid">
        {pages.map((p) => {
          const k = key(p);
          return (
            <li key={k} className={p.restart ? "off" : picked.has(k) ? "on" : ""} title={p.restart ?? "bisa dibaca ulang"}>
              <label>
                <input type="checkbox" disabled={!!p.restart} checked={picked.has(k)} onChange={() => toggle(k)} />
                {files > 1 && <span className="muted">{p.file_name} · </span>}hal. {p.page_no}
              </label>
            </li>
          );
        })}
      </ul>
      <div className="rs-bar">
        <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
        <button className="btn" disabled={busy || !!notNow || !picked.size} onClick={() => send("picked")}>
          {busy ? "Mengirim…" : `Baca ulang yang dipilih (${picked.size})`}</button>
        <button className="btn" disabled={busy || !!notNow || !can.length} onClick={() => send("all")}>Baca ulang semua ({can.length})</button>
      </div>
      {error && <p className="salah">{error}</p>}
      {note && <p className="small">{note}</p>}
    </details>
  );
}
