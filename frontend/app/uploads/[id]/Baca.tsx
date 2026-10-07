// Step 1, Dibaca AI: the system's own step. How far reading has come; a page that failed for a technical reason, with
// "Coba lagi" (it calls the AI again, so it says so); and the batch's files.
import Link from "next/link";
import { pct } from "@/lib/format";
import type { UploadDetail } from "@/lib/types";
import ScanTable from "@/components/ScanTable";
import Retry from "./Retry";

/** A failure said for people; the raw error stays in the fold for IT. */
function reason(err: string | null): string {
  if (err && /connect|ssl|timeout|timed out|eof/i.test(err)) return "Koneksi ke layanan AI terputus.";
  return "Halaman ini gagal dibaca.";
}

export default function Baca({ d }: { d: UploadDetail }) {
  const s = d.steps[0];
  if (s.key !== "baca") return null;
  return (
    <section className="ws-panel">
      <h2>Dibaca AI</h2>
      <p className="lede">Langkah sistem: AI membaca setiap halaman sendiri. Anda hanya perlu bertindak bila ada halaman yang gagal dibaca.</p>
      <div className="prog" style={{ maxWidth: 520 }}>
        <span>{s.read} dari {s.pages} halaman dibaca{s.busy > 0 && <> <em className="live">sedang dibaca</em></>}</span>
        <div className="bar"><i style={{ width: `${pct(s.read, s.pages)}%` }} /></div>
      </div>
      {s.waiting_ai > 0 && <p className="small">{s.waiting_ai} halaman menunggu AI membaca ulang bagian yang belum pasti. Ini berjalan sendiri.</p>}
      {s.unscheduled > 0 && <p className="small muted">{s.unscheduled} halaman belum dijadwalkan untuk dibaca.</p>}

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
                  <td><span className="cap merah">Gagal</span> <span className="small">{reason(p.error)} Sudah dicoba dua kali.</span>
                    {p.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{p.error}</code></details>}</td>
                  <td><Retry batch={p.batch_id} page={p.page_no} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Coba lagi meminta AI membaca halaman itu sekali lagi (memakai kuota AI, sekitar 6 ribu token per
            halaman). Setelah terbaca, dokumennya bisa masuk ke ordernya.</p>
        </>
      )}

      <details className="ws-files" open={d.files.length <= 5}>
        <summary className="ws-grp">File di batch ini <span>({d.files.length})</span></summary>
        <ScanTable scans={d.files} />
      </details>
    </section>
  );
}
