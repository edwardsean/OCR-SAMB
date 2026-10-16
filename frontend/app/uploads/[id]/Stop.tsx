"use client";
// "Hentikan" and "Lanjutkan membaca" (2026-10-15; the user: "a stop button to stop a working batch, but there should be
// a condition on where can we stop and when can we not stop"). Stopping asks once, signed with the name: pages not read
// yet are never sent to the AI, a page being read stops at its next AI call; what is read stays, and complete orders
// can still be sent (services/common/stopping.py). The server refuses what can't be stopped and says why.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";
import { tgl } from "@/lib/format";
import type { StopInfo } from "@/lib/types";

type Left = { file_name: string; why: string }[];

export default function Stop({ upload, stop }: { upload: number; stop: StopInfo }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const stopped = stop.stopped.length > 0;

  async function send(e: React.FormEvent, what: "stop" | "resume") {
    e.preventDefault();
    setBusy(true); setError(null); setNote(null);
    const a = await api.post<{ result?: { left?: Left } }>(`/uploads/${upload}/${what}`, { by });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    const left = a.data.result?.left || [];
    if (left.length) setNote(`${left.length} file tidak: ${left.map((l) => `${l.file_name} (${l.why})`).join(", ")}`);
    setAsking(false);
    router.refresh();
  }

  if (stopped) {
    const first = stop.stopped[0];
    return (
      <form className="stopbox" onSubmit={(e) => send(e, "resume")}>
        <p className="small"><b className="cap merah">■ Dihentikan</b> oleh <b>{first.by ?? "seseorang"}</b>
          {first.at && <> · {tgl(first.at)}</>}
          {stop.stopped.length > 1 && <> · {stop.stopped.length} file</>}</p>
        {stop.finishing > 0
          ? <p className="small muted">{stop.finishing} halaman menyelesaikan panggilan AI terakhirnya…</p>
          : <span className="stoprow">
              <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
              <button className="btn primary" disabled={busy}>{busy ? "Mengirim…" : "Lanjutkan membaca"}</button>
            </span>}
        {error && <p className="salah">{error}</p>}
        {note && <p className="small">{note}</p>}
      </form>
    );
  }
  if (!stop.can) return null;
  if (!asking) return <button className="btn stop" onClick={() => setAsking(true)}>■ Hentikan</button>;
  return (
    <form className="stopbox" onSubmit={(e) => send(e, "stop")}>
      <p className="small">Halaman yang belum dibaca tidak dikirim ke AI. Yang sedang dibaca berhenti di panggilan AI
        berikutnya. Yang sudah dibaca tetap tersimpan; order yang lengkap tetap bisa dikirim.</p>
      <span className="stoprow">
        <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
        <button className="btn stop" disabled={busy}>{busy ? "Menghentikan…" : "Ya, hentikan"}</button>
        <button type="button" className="btn" onClick={() => setAsking(false)}>Batal</button>
      </span>
      {error && <p className="salah">{error}</p>}
    </form>
  );
}
