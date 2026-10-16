"use client";
// "Baca ulang" (2026-10-15; the user: "a restart button to select pages to restart or restart all"): pages read again
// FROM SCRATCH — a new AI OCR copy and every AI step after it (about 5 AI calls a page); people's labels and
// corrections stay (POST /uploads/{id}/restart). A page that can't be (its order is sent, it is being read, its file is
// stopped) is shown greyed with why. Asks once, signed with the name, with the calls it will cost.
// 2026-10-16 (the user: "i cant see it", "search through the pages to find the page i want to retry"): pages grouped
// per file as small tiles (thumbnail, number, type), and a search by page number or range, type, SOR, PO, customer.
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";
import { useWords } from "@/components/Words";
import type { PageNow } from "@/lib/types";

const CALLS = 5;
type Done = { restarted: number; calls: number; left: { file_name: string; page_no: number; why: string }[] };
const key = (p: { batch_id: string; page_no: number }) => `${p.batch_id}/${p.page_no}`;

/** Pure: does a page match every word of the search? "12" is page 12 (up to 3 digits), "10-20" pages 10 to 20; any
 *  other word, a longer number too ("9546" of an SOR), is looked for in its type, state, SOR, the numbers read on it,
 *  its customer and its file's name. */
export function matches(p: PageNow, q: string, words: (p: PageNow) => string): boolean {
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => {
    const range = /^(\d{1,4})-(\d{1,4})$/.exec(t);
    if (range) return p.page_no >= +range[1] && p.page_no <= +range[2];
    if (/^\d{1,3}$/.test(t)) return p.page_no === +t;
    return words(p).includes(t);
  });
}

export default function RestartPicker({ upload, pages, notNow }: { upload: number; pages: PageNow[]; notNow: string | null }) {
  const router = useRouter();
  const w = useWords();
  const [by, setBy] = useReviewer();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const words = (p: PageNow) => [p.doc_type, p.doc_type && w.DOC[p.doc_type], p.unsure && "belum pasti",
    p.state === "failed" && "gagal", p.outcome === "needs_person" && "perlu dicek", p.file_name, p.find]
    .filter(Boolean).join(" ").toLowerCase();
  const can = useMemo(() => pages.filter((p) => !p.restart), [pages]);
  const shown = useMemo(() => (q.trim() ? pages.filter((p) => matches(p, q, words)) : pages), [pages, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const shownCan = shown.filter((p) => !p.restart);
  const files = useMemo(() => {
    const m = new Map<string, PageNow[]>();
    shown.forEach((p) => m.set(p.file_name, [...(m.get(p.file_name) ?? []), p]));
    return [...m.entries()];
  }, [shown]);
  const total = new Map<string, number>();
  pages.forEach((p) => total.set(p.file_name, (total.get(p.file_name) ?? 0) + 1));

  const toggle = (k: string) => setPicked((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const pickShown = () => setPicked((s) => new Set([...s, ...shownCan.map(key)]));

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
      <p className="small muted">Dibaca <b>dari awal</b>, sekitar {CALLS} panggilan AI per halaman. Label dan koreksi
        dari orang tetap dipakai.</p>
      {notNow && <p className="small salah">Belum bisa sekarang: {notNow}</p>}
      <div className="rs-bar">
        <input type="search" className="rs-find" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Cari halaman"
               placeholder="Cari: 12 · 10-20 · TTG · SOR… · nomor PO · pelanggan · gagal" />
        <span className="small muted">{shown.length} dari {pages.length} halaman</span>
        <button type="button" className="btn tiny" disabled={!shownCan.length} onClick={pickShown}>
          Pilih {q.trim() ? "yang tampil" : "semua"} ({shownCan.length})</button>
        {picked.size > 0 && <button type="button" className="linkbtn" onClick={() => setPicked(new Set())}>kosongkan</button>}
      </div>
      {files.map(([file, ps]) => (
        <div key={file} className="rs-file">
          {(files.length > 1 || total.size > 1) && <h4>{file} <span className="muted">· {ps.length}{ps.length !== total.get(file) ? ` dari ${total.get(file)}` : ""} hal.</span></h4>}
          <ul className="rs-grid">
            {ps.map((p) => {
              const k = key(p), on = picked.has(k);
              return (
                <li key={k}>
                  <button type="button" className={p.restart ? "off" : on ? "on" : undefined} disabled={!!p.restart}
                          aria-pressed={on} title={p.restart ?? [p.sor, p.find].filter(Boolean).join(" · ") ?? ""}
                          onClick={() => toggle(k)}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    {p.thumb ? <img src={`/img/${p.thumb}`} alt="" loading="lazy" /> : <i />}
                    <span className="rs-no">{p.page_no}</span>
                    {p.doc_type && <span className={`tchip t-${p.doc_type}`} title={w.DOC[p.doc_type]}>{p.doc_type}</span>}
                    {p.state === "failed" && <span className="rs-flag">gagal</span>}
                    {on && <span className="rs-tick">✓</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      {!shown.length && <p className="small muted">Tidak ada halaman yang cocok.</p>}
      <div className="rs-bar rs-go">
        <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
        <button className="btn primary" disabled={busy || !!notNow || !picked.size} onClick={() => send("picked")}>
          {busy ? "Mengirim…" : `Baca ulang yang dipilih (${picked.size})`}</button>
        <span className="small muted">{picked.size ? `±${picked.size * CALLS} panggilan AI` : ""}</span>
        <button className="btn" disabled={busy || !!notNow || !can.length} onClick={() => send("all")}>Baca ulang semua ({can.length})</button>
      </div>
      {error && <p className="salah">{error}</p>}
      {note && <p className="small">{note}</p>}
    </details>
  );
}
