// The register of scans (Riwayat scan, Unggah scan).
import Link from "next/link";
import { getWords } from "@/lib/api";
import { pct, tgl } from "@/lib/format";
import type { Scan } from "@/lib/types";

export default async function ScanTable({ scans }: { scans: Scan[] }) {
  const w = await getWords();
  return (
    <table className="reg scantable">
      <thead><tr><th>File</th><th>Diterima</th><th>Halaman dibaca</th><th>Status</th><th></th></tr></thead>
      <tbody>
        {scans.map((b) => (
          <tr key={b.id}>
            <td><Link href={`/batches/${b.id}`}><b>{b.file_name}</b></Link></td>
            <td className="muted">{tgl(b.received_at)}</td>
            <td className="prog">
              <div className="bar"><i style={{ width: `${pct(b.page_done, b.page_total)}%` }} /></div>
              <span className="small muted">{b.page_done} dari {b.page_total}</span>
            </td>
            <td><span className={`pill s-${b.status}`}>{w.BATCH[b.status] ?? b.status}</span></td>
            <td className="num"><Link href={`/batches/${b.id}`}>Buka</Link></td>
          </tr>
        ))}
        {!scans.length && (
          <tr><td colSpan={5} className="muted">Belum ada scan. <Link href="/upload">Unggah scan pertama</Link>.</td></tr>
        )}
      </tbody>
    </table>
  );
}
