"use client";
// The drop zone: choose or drop a PDF, then POST /api/v1/scans; the scan's page opens when it's taken.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type DragEvent } from "react";
import { api } from "@/lib/client";
import { tgl } from "@/lib/format";

type Dup = { id: string; file_name: string; received_at: string };

export default function UploadForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ error: string; detail?: string } | null>(null);
  const [dup, setDup] = useState<Dup | null>(null);

  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  }
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true); setError(null); setDup(null);
    const body = new FormData();
    body.append("file", file);
    const a = await api.post<{ batch_id: string; dup?: Dup; detail?: string }>("/scans", body);
    if (a.ok) { router.push(`/batches/${a.data.batch_id}`); return; }
    setBusy(false);
    if (a.status === 409 && a.data.dup) setDup(a.data.dup);
    else setError({ error: a.data.error ?? `Gagal mengunggah (kode ${a.status}).`, detail: a.data.detail });
  }

  return (
    <>
      {error && (
        <section className="alert bad"><strong>{error.error}</strong>
          {error.detail && <details className="small"><summary>Detail untuk tim IT</summary><code>{error.detail}</code></details>}
        </section>
      )}
      {dup && (
        <section className="alert warn">
          <strong>File ini sudah pernah diunggah.</strong> File yang persis sama masuk sebagai{" "}
          <Link href={`/batches/${dup.id}`}>{dup.file_name}</Link> pada {tgl(dup.received_at)}, jadi tidak diproses lagi.
        </section>
      )}
      <form className="drop" onSubmit={send}>
        <label className={"drop-zone" + (file ? " has" : "") + (over ? " over" : "")}
               onDragEnter={(e) => { e.preventDefault(); setOver(true); }} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
               onDragLeave={() => setOver(false)} onDrop={drop}>
          <input type="file" name="file" accept="application/pdf" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <b>{file ? file.name : "Pilih file PDF, atau tarik ke sini"}</b>
          <span className="muted small">{file ? `${(file.size / 1048576).toFixed(1)} MB, siap diunggah` : "Hanya file PDF. Satu file boleh berisi ratusan halaman."}</span>
        </label>
        <div className="drop-act">
          <button type="submit" className="btn primary big" disabled={!file || busy}>{busy ? "Mengunggah…" : "Unggah dan proses"}</button>
          {busy && <span className="muted small">Mengunggah… jangan tutup halaman ini.</span>}
        </div>
      </form>
    </>
  );
}
