// Step 1, Dibaca AI: the system's own step. How far reading has come; what is stuck (services/api/stuck.py), each
// with why in plain words and "Coba lagi" where a person may try it (never for an order already sent to Satellite);
// a file that couldn't be split; why nothing can run right now (a model not set, a limit); and the batch's files.
import Link from "next/link";
import { pct } from "@/lib/format";
import type { StuckPage, UploadDetail } from "@/lib/types";
import ScanTable from "@/components/ScanTable";
import Retry from "./Retry";

const enc = encodeURIComponent;

/** What a person can do about one stuck page. */
function Action({ p, notNow }: { p: StuckPage; notNow: string | null }) {
  if (p.published) return <span className="small muted">Ordernya sudah dikirim ke Satellite: tidak dibaca ulang.</span>;
  if (notNow) return <span className="small muted">Lanjut sendiri.</span>;
  return (
    <>
      {p.cause === "setting" && <p className="small"><a href="/settings">Buka Model &amp; kunci API</a></p>}
      <Retry path={`/scans/${enc(p.batch_id)}/pages/${p.page_no}/retry`} />
    </>
  );
}

export default function Baca({ d }: { d: UploadDetail }) {
  const s = d.steps[0];
  if (s.key !== "baca") return null;
  const retryable = d.failed.filter((p) => p.can_retry).length + d.failed_files.length;
  return (
    <section className="ws-panel">
      <h2>Dibaca AI</h2>
      <p className="lede">Langkah sistem: AI membaca setiap halaman sendiri. Anda hanya perlu bertindak bila ada halaman yang gagal dibaca.</p>
      <div className="prog" style={{ maxWidth: 520 }}>
        <span>{s.read} dari {s.pages} halaman dibaca{s.busy > 0 && <> <em className="live">sedang dibaca</em></>}</span>
        <div className="bar"><i style={{ width: `${pct(s.read, s.pages)}%` }} /></div>
      </div>
      {d.not_now && <p className="small"><span className="cap merah">Menunggu</span> {d.not_now}
        {d.not_now.startsWith("Model belum diatur") && <> <a href="/settings">Atur sekarang</a></>}</p>}
      {s.waiting_ai > 0 && <p className="small">{s.waiting_ai} halaman menunggu AI membaca ulang bagian yang belum pasti. Ini berjalan sendiri.</p>}
      {s.unscheduled > 0 && <p className="small muted">{s.unscheduled} halaman belum dijadwalkan untuk dibaca.</p>}

      {d.failed_files.length > 0 && (
        <>
          <h3 className="ws-grp">File gagal diproses <span>({d.failed_files.length})</span></h3>
          <table className="reg">
            <thead><tr><th>File</th><th>Keadaan</th><th /></tr></thead>
            <tbody>
              {d.failed_files.map((f) => (
                <tr key={f.batch_id}>
                  <td>{f.file_name}</td>
                  <td><span className="cap merah">Gagal</span> <span className="small">File ini tidak bisa dipecah menjadi halaman.</span>
                    {f.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{f.error}</code></details>}</td>
                  <td><Retry path={`/scans/${enc(f.batch_id)}/retry`} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {d.failed.length > 0 && (
        <>
          <h3 className="ws-grp">Gagal dibaca <span>({d.failed.length})</span></h3>
          <table className="reg">
            <thead><tr><th>File</th><th className="num">Hal.</th><th>Keadaan</th><th /></tr></thead>
            <tbody>
              {d.failed.map((p) => (
                <tr key={`${p.batch_id}:${p.page_no}`}>
                  <td><Link href={`/batches/${p.batch_id}/pages/${p.page_no}`}>{p.file_name}</Link></td>
                  <td className="num">{p.page_no}</td>
                  <td><span className="cap merah">Gagal</span> <span className="small">{p.reason}
                    {p.kind === "call_failed" && !p.published ? " Sudah dicoba otomatis; sistem mencoba sekali lagi setiap beberapa jam." : ""}</span>
                    {p.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{p.error}</code></details>}</td>
                  <td><Action p={p} notNow={d.not_now} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Coba lagi meminta AI mengerjakan ulang hanya bagian yang gagal (memakai kuota AI, paling banyak
            sekitar 6 ribu token per halaman). Setelah terbaca, dokumennya bisa masuk ke ordernya.</p>
        </>
      )}
      {retryable > 1 && !d.not_now && (
        <Retry path={`/uploads/${d.upload.id}/retries`} label={`Coba lagi semua (${retryable})`} />
      )}

      <details className="ws-files" open={d.files.length <= 5}>
        <summary className="ws-grp">File di batch ini <span>({d.files.length})</span></summary>
        <ScanTable scans={d.files} />
      </details>
    </section>
  );
}
