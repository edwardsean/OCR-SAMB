"use client";
// "Coba lagi" on a page that failed to read: it goes back on the reading queue (POST …/retry), signed with the name.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";

export default function Retry({ batch, page }: { batch: string; page: number }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const a = await api.post(`/scans/${encodeURIComponent(batch)}/pages/${page}/retry`, { by });
    setBusy(false);
    if (!a.ok) setError(why(a)); else router.refresh();
  }
  return (
    <form className="ws-retry" onSubmit={send}>
      <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
      <button className="btn tiny" disabled={busy}>{busy ? "Mengirim…" : "Coba lagi"}</button>
      {error && <p className="salah">{error}</p>}
    </form>
  );
}
