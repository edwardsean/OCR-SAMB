// Step 2, Jenis halaman: the batch's pages whose type the system couldn't decide. Each opens the label screen, which
// walks this batch's pages one after another and comes back here when none is left.
import Link from "next/link";
import type { UploadDetail } from "@/lib/types";

export default function Jenis({ d }: { d: UploadDetail }) {
  const s = d.steps[1];
  if (s.key !== "jenis") return null;
  const id = d.upload.id;
  return (
    <section className="ws-panel">
      <h2>Jenis halaman</h2>
      {d.unsure.length ? (
        <p className="ws-now need">AI belum yakin jenis {d.unsure.length} halaman ini. Pilih jenisnya: jenis menentukan nomor
          yang dicari untuk menemukan ordernya.</p>
      ) : s.state !== "done" ? (
        <p className="ws-now wait">Menunggu langkah 1: {s.pending} halaman belum dibaca. Halaman yang jenisnya belum pasti
          akan muncul di sini.</p>
      ) : null}
      {d.unsure.length ? (
        <>
          <p><Link className="btn primary" href={`/label?upload=${id}`}>Mulai tentukan jenis ({d.unsure.length} halaman)</Link></p>
          <div className="bx-docs loose">
            {d.unsure.map((p) => (
              <Link key={`${p.batch_id}:${p.page_no}`} className="bx-tile" title={p.file_name}
                    href={`/label?upload=${id}&batch=${encodeURIComponent(p.batch_id)}&page=${p.page_no}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <span className="bx-img">{p.thumb && <img src={p.thumb} alt="" loading="lazy" />}</span>
                <b>hal. {p.page_no}</b><small className="bx-scan">{p.file_name}</small>
              </Link>
            ))}
          </div>
        </>
      ) : s.state === "done" ? (
        <p className="ws-now ok">Semua jenis halaman sudah pasti{s.answered ? `; ${s.answered} dipilih oleh Anda.` : "."}</p>
      ) : null}
    </section>
  );
}
