// Cari: one search over every batch (the Batch tab replaced the screens over every batch): orders by SOR, the
// customer's PO number (Nomor CPO) or customer, batches by number or uploader, files by name.
import Link from "next/link";
import { getWords, need, one, type SearchParams } from "@/lib/api";
import { tgl } from "@/lib/format";
import type { SearchResult } from "@/lib/types";
import Cap from "@/components/Cap";
import { BatchTag, BatchTags } from "@/components/Batch";
import { NAME } from "@/components/Steps";

export const metadata = { title: "Cari" };

export default async function Cari({ searchParams }: { searchParams: SearchParams }) {
  const q = (one((await searchParams).q) ?? "").trim();
  const [r, w] = await Promise.all([need<SearchResult>("/search", { q }), getWords()]);
  const none = !r.orders.length && !r.uploads.length && !r.files.length;
  return (
    <>
      <section className="head">
        <h1>Cari{q && <>: “{q}”</>}</h1>
        <form className="find big" action="/cari" role="search">
          <input type="search" name="q" defaultValue={q} autoFocus placeholder="Nomor SOR, nomor PO, nama pelanggan, nomor batch, atau nama file" aria-label="Cari" />
          <button className="btn">Cari</button>
        </form>
      </section>

      {q.length < 2 ? <p className="muted">Ketik paling sedikit 2 huruf atau angka.</p>
        : none ? <p className="kosong">Tidak ada order, batch, atau file yang cocok dengan “{q}”.</p> : null}

      {r.orders.length > 0 && (
        <section>
          <h2>Order <span className="count">({r.orders.length})</span></h2>
          <table className="reg orders">
            <thead><tr><th /><th>Pelanggan</th><th>Nomor PO pelanggan</th><th>Batch</th><th>Status</th></tr></thead>
            <tbody>
              {r.orders.map((o) => {
                const href = `/review/${o.sor_no}?batch=${encodeURIComponent(o.batch ?? "")}`;
                return (
                  <tr className="row" key={o.sor_no}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <td className="o-paper">{o.thumb && <Link href={href} tabIndex={-1}><img className="paper" src={o.thumb} alt="" loading="lazy" /></Link>}</td>
                    <td><Link className="o-who" href={href}>{o.customer_name || "Pelanggan belum diketahui"}</Link><span className="o-sor">{o.sor_no}</span></td>
                    <td className="mono">{o.cpo_no ?? "—"}</td>
                    <td><BatchTags list={o.uploads} /></td>
                    <td><Cap status={o.status}>{w.BUNDLE[o.status] ?? o.status}</Cap></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {r.uploads.length > 0 && (
        <section>
          <h2>Batch <span className="count">({r.uploads.length})</span></h2>
          <table className="reg">
            <tbody>
              {r.uploads.map((u) => (
                <tr className="row" key={u.id}>
                  <td><Link href={`/uploads/${u.id}`}><b className="mono">{u.code}</b></Link></td>
                  <td>oleh {u.uploaded_by}</td><td>scan {tgl(u.doc_date, false)}</td>
                  <td className="num small">{u.files} file, {u.pages} halaman</td>
                  <td>{u.next ? <span className="chip need">{NAME[u.next]}</span> : <span className="muted small">tidak ada yang menunggu</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {r.files.length > 0 && (
        <section>
          <h2>File <span className="count">({r.files.length})</span></h2>
          <table className="reg">
            <tbody>
              {r.files.map((f) => (
                <tr className="row" key={f.id}>
                  <td><Link href={`/batches/${f.id}`}>{f.file_name}</Link></td>
                  <td className="num small">{f.page_total} halaman</td>
                  <td><BatchTag u={f.upload} full /></td>
                  <td className="muted small">diterima {tgl(f.received_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
