"use client";
// "Hapus batch" (2026-10-16; the user: "a delete batch button so that i can retry to upload the same document again;
// think when can we delete, when can we not"). Folded at the end of the batch page. When it can't be deleted it says
// why (an order made from it was sent, it still runs: stop it first); otherwise it lists what goes, and deletes only
// once the batch's code is typed, signed with the name (services/common/deleting.py).
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, why } from "@/lib/client";
import { useReviewer } from "@/components/useReviewer";
import type { DeleteInfo } from "@/lib/types";

export default function DeleteBatch({ upload, info }: { upload: number; info: DeleteInfo }) {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const n = info.impact;
  const typed = code.trim().toUpperCase() === info.code.toUpperCase();

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const a = await api.post(`/uploads/${upload}/delete`, { by, code });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    router.push("/");
    router.refresh();
  }

  return (
    <details className="del">
      <summary>Hapus batch…</summary>
      <div className="del-body">
        {!info.can ? <p className="small">Belum bisa dihapus: {info.why_not}</p> : (
          <form onSubmit={send}>
            <p className="small">Dihapus permanen, lalu file yang sama bisa diunggah lagi:</p>
            <ul>
              <li>{n.files} file, {n.pages} halaman, dan hasil bacaannya</li>
              {n.orders > 0 && <li>{n.orders} order yang hanya berisi dokumen batch ini</li>}
              {n.shared > 0 && <li>{n.shared} order lain kehilangan dokumen dari batch ini, lalu dicek ulang</li>}
              {n.approved > 0 && <li><b>{n.approved} order yang sudah disetujui</b> perlu disetujui lagi</li>}
              {(n.labels > 0 || n.confirmations > 0) && <li>yang ditentukan orang di halamannya: {[
                n.labels > 0 && `${n.labels} jenis halaman`, n.confirmations > 0 && `${n.confirmations} nilai`].filter(Boolean).join(" dan ")}</li>}
              {n.efaktur > 0 && <li>{n.efaktur} e-Faktur</li>}
            </ul>
            <p className="small muted">Tetap: yang sudah dipelajari AI, Jejak, dan catatan panggilan AI.</p>
            <div className="del-row">
              <input className="mono" value={code} onChange={(e) => setCode(e.target.value)} placeholder={`ketik ${info.code}`}
                     aria-label="Ketik kode batch" autoComplete="off" />
              <input required autoComplete="name" placeholder="nama Anda" aria-label="Nama Anda" value={by} onChange={(e) => setBy(e.target.value)} />
              <button className="btn danger" disabled={busy || !typed || !by.trim()}>{busy ? "Menghapus…" : "Hapus permanen"}</button>
            </div>
          </form>
        )}
        {error && <p className="salah">{error}</p>}
      </div>
    </details>
  );
}
