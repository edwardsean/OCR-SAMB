"use client";
// Unggah: who uploads and the day the papers were scanned, then one PDF or many. Pressing upload makes one upload
// batch (POST /api/v1/uploads: BATCH-YYYYMMDD-NN) and sends each file into it (POST /api/v1/scans, one after another;
// each file is its own scan), then opens the batch's page. The user, 2026-10-05: "can we put a lot of files in the
// upload?" and "input the uploader's name, date, and a generated batch number".
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type DragEvent } from "react";
import { api } from "@/lib/client";
import { tgl } from "@/lib/format";
import type { UploadRef } from "@/lib/types";
import { useReviewer } from "@/components/useReviewer";

type Dup = { id: string; file_name: string; received_at: string };
type Step = "waiting" | "sending" | "taken" | "dup" | "failed";
type Row = { file: File; step: Step; batch_id?: string; dup?: Dup; error?: string; detail?: string };

const SAYS: Record<Step, [string, string]> = {          // what each step says, and its ink
  waiting: ["menunggu giliran", "chip"],
  sending: ["mengunggah…", "chip wait"],
  taken: ["diterima, sedang diproses", "chip ok"],
  dup: ["sudah pernah diunggah", "chip done"],
  failed: ["gagal", "chip need"],
};

function pdfs(list: FileList | null): File[] {
  return Array.from(list ?? []).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
}

function today() {                                   // in WIB, as the server counts days
  return new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 10);
}

export default function UploadForm() {
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [day, setDay] = useState(today());
  const [batch, setBatch] = useState<UploadRef | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  function choose(list: FileList | null) {
    const all = Array.from(list ?? []);
    const ok = pdfs(list);
    setSkipped(all.length - ok.length);
    setRows(ok.sort((a, b) => a.name.localeCompare(b.name, "id", { numeric: true })).map((file) => ({ file, step: "waiting" })));
    setDone(false);
    setBatch(null);                                       // new files: a new batch
  }
  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (!busy) choose(e.dataTransfer.files);
  }
  function mark(i: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!rows.length || !by.trim()) return;
    setBusy(true); setDone(false);
    let b = batch;
    if (!b) {                                             // one batch for this whole upload (a retry keeps it)
      const n = await api.post<UploadRef & { error?: string }>("/uploads", { by: by.trim(), date: day });
      if (!n.ok) { setBusy(false); setRows((rs) => rs.map((r) => ({ ...r, step: "failed", error: n.data.error ?? "Batch tidak bisa dibuat." }))); return; }
      b = n.data; setBatch(b);
    }
    let taken = 0;
    for (let i = 0; i < rows.length; i++) {                // one after another: the intake takes them in order
      if (rows[i].step === "taken" || rows[i].step === "dup") continue;
      mark(i, { step: "sending", error: undefined, detail: undefined });
      const body = new FormData();
      body.append("file", rows[i].file);
      body.append("upload", String(b.id));
      const a = await api.post<{ batch_id: string; dup?: Dup; detail?: string }>("/scans", body);
      if (a.ok) { taken++; mark(i, { step: "taken", batch_id: a.data.batch_id }); }
      else if (a.status === 409 && a.data.dup) mark(i, { step: "dup", dup: a.data.dup });
      else mark(i, { step: "failed", error: a.data.error ?? `Gagal mengunggah (kode ${a.status}).`, detail: a.data.detail });
    }
    setBusy(false); setDone(true);
    if (taken && taken === rows.length) router.push(`/uploads/${b.id}`);   // all in: the batch's page shows the reading
  }

  const count = (s: Step) => rows.filter((r) => r.step === s).length;
  const mb = rows.reduce((n, r) => n + r.file.size, 0) / 1048576;

  return (
    <>
      <form className="drop" onSubmit={send}>
        <div className="up-who">
          <label>Nama Anda <input required autoComplete="name" value={by} disabled={busy || !!batch}
                                  onChange={(e) => setBy(e.target.value)} placeholder="tulis nama Anda" /></label>
          <label>Tanggal scan <input type="date" required value={day} max={today()} disabled={busy || !!batch}
                                     onChange={(e) => setDay(e.target.value)} /></label>
          <span className="muted small">{batch ? <>Batch <b className="mono">{batch.code}</b> · {batch.uploaded_by} · scan {tgl(batch.doc_date, false)}</>
            : "Nomor batch dibuat otomatis saat Anda mengunggah. Semua file di bawah masuk ke satu batch."}</span>
        </div>
        <label className={"drop-zone" + (rows.length ? " has" : "") + (over ? " over" : "")}
               onDragEnter={(e) => { e.preventDefault(); setOver(true); }} onDragOver={(e) => { e.preventDefault(); setOver(true); }}
               onDragLeave={() => setOver(false)} onDrop={drop}>
          <input type="file" name="file" accept="application/pdf" multiple disabled={busy}
                 onChange={(e) => choose(e.target.files)} />
          <b>{rows.length === 0 ? "Pilih file PDF, atau tarik ke sini" : rows.length === 1 ? rows[0].file.name : `${rows.length} file PDF dipilih`}</b>
          <span className="muted small">
            {rows.length ? `${mb.toFixed(1)} MB, siap diunggah` : "Boleh satu file atau banyak sekaligus. Hanya PDF; satu file boleh berisi ratusan halaman."}
            {skipped > 0 && ` · ${skipped} file bukan PDF dilewati`}
          </span>
        </label>
        <div className="drop-act">
          <button type="submit" className="btn primary big" disabled={!rows.length || !by.trim() || busy || (done && !count("failed"))}>
            {busy ? "Mengunggah…" : done && count("failed") ? "Coba lagi yang gagal" : rows.length > 1 ? `Unggah dan proses ${rows.length} file` : "Unggah dan proses"}
          </button>
          {busy && <span className="muted small">Mengunggah satu per satu… jangan tutup halaman ini.</span>}
        </div>
      </form>

      {rows.length === 1 && rows[0].step === "dup" && rows[0].dup && (
        <section className="alert warn">
          <strong>File ini sudah pernah diunggah.</strong> File yang persis sama masuk sebagai{" "}
          <Link href={`/batches/${rows[0].dup.id}`}>{rows[0].dup.file_name}</Link> pada {tgl(rows[0].dup.received_at)}, jadi tidak diproses lagi.
        </section>
      )}
      {rows.length === 1 && rows[0].step === "failed" && (
        <section className="alert bad"><strong>{rows[0].error}</strong>
          {rows[0].detail && <details className="small"><summary>Detail untuk tim IT</summary><code>{rows[0].detail}</code></details>}
        </section>
      )}

      {rows.length > 1 && (
        <section>
          <h2>File yang dipilih</h2>
          {done && (
            <p className="small">
              {batch && <>Batch <Link href={`/uploads/${batch.id}`}><b className="mono">{batch.code}</b></Link>: </>}
              {count("taken")} diterima dan sedang diproses
              {count("dup") > 0 && ` · ${count("dup")} sudah pernah diunggah (tidak diproses lagi)`}
              {count("failed") > 0 && ` · ${count("failed")} gagal`}
              {" · "}<Link href={batch ? `/uploads/${batch.id}` : "/batches"}>buka batch ini</Link>
            </p>
          )}
          <table className="reg">
            <thead><tr><th>File</th><th className="num">Ukuran</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.file.name + i}>
                  <td>{r.file.name}</td>
                  <td className="num muted small">{(r.file.size / 1024).toFixed(0)} KB</td>
                  <td>
                    <span className={SAYS[r.step][1]}>{SAYS[r.step][0]}</span>
                    {r.step === "failed" && (
                      <div className="small">{r.error}
                        {r.detail && <details><summary>Detail untuk tim IT</summary><code>{r.detail}</code></details>}
                      </div>
                    )}
                    {r.step === "dup" && r.dup && (
                      <div className="small muted">masuk sebagai {r.dup.file_name}, {tgl(r.dup.received_at)}</div>
                    )}
                  </td>
                  <td>
                    {r.batch_id && <Link href={`/batches/${r.batch_id}`}>buka scan</Link>}
                    {r.dup && <Link href={`/batches/${r.dup.id}`}>buka scan</Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
