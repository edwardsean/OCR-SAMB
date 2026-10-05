"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";

/** The needs-you notices are marked seen once a browser shows them (never by a fetch: a test once marked one seen). */
export function NoticesSeen() {
  useEffect(() => { api.post("/notices/seen"); }, []);
  return null;
}

/** Kirim ke Satellite: the finished orders (of the chosen scan, or every one), written with their PDFs; then what was
 * written. `back`: a batch's step 5, which shows what was just sent (else Data terkirim). */
export function PublishForm({ batch, upload, n, back }: { batch: string; upload?: number | null; n: number; back?: string }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const a = await api.post<{ result: string[] }>("/publications", { batch: batch || null, upload: upload ?? null, by });   // none: every scan
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    const done = a.data.result;
    const scan = upload ? `upload=${upload}&` : batch ? `batch=${encodeURIComponent(batch)}&` : "";
    if (back) router.push(`${back}${back.includes("?") ? "&" : "?"}just=${encodeURIComponent(done.join(","))}`);
    else router.push(done.length ? `/published?${scan}just=${encodeURIComponent(done.join(","))}` : `/review?${scan}published=0`);
    router.refresh();
  }
  return (
    <form className="o-kirim" onSubmit={send}>
      <p><b>{n} order siap dikirim.</b> Datanya ditulis ke Satellite beserta satu PDF per SOR, setelah dicek sekali lagi.</p>
      <label>Nama Anda <input required autoComplete="name" value={by} onChange={(e) => setBy(e.target.value)} /></label>
      <button className="btn primary" disabled={busy}>{busy ? "Mengirim…" : "Kirim ke Satellite"}</button>
      {error && <p className="salah">{error}</p>}
    </form>
  );
}
