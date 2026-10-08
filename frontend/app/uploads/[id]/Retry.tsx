"use client";
// "Coba lagi": a stuck page, a file that couldn't be split, or everything stuck in the batch goes back to be tried
// (POST …/retry or …/retries), signed with the name. The server refuses what may not be tried (an order already sent
// to Satellite, a page being tried, no model set) and says why; trying everything says what was left out.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";

type Done = { pages?: number; files?: number; left?: { page_no: number; why: string }[] };

export default function Retry({ path, label = "Coba lagi" }: { path: string; label?: string }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setNote(null);
    const a = await api.post<{ result?: Done }>(path, { by });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    const r = a.data.result;
    if (r && (r.pages !== undefined || r.files !== undefined)) {
      const left = r.left || [];
      setNote(`${r.pages || 0} halaman dan ${r.files || 0} file dicoba lagi.`
        + (left.length ? ` ${left.length} tidak: ${left.map((l) => `hal. ${l.page_no} (${l.why})`).join(", ")}.` : ""));
    }
    router.refresh();
  }
  return (
    <form className="ws-retry" onSubmit={send}>
      <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
      <button className="btn tiny" disabled={busy}>{busy ? "Mengirim…" : label}</button>
      {error && <p className="salah">{error}</p>}
      {note && <p className="small">{note}</p>}
    </form>
  );
}
