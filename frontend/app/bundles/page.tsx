// Berkas per SOR (the user, 2026-10-02: "just make it easy to read and know how to use for users"; 2026-10-05: "why
// not per SOR?"): what needs a person first (documents whose number isn't sure), then one entry per ORDER with all its
// documents as page tiles, from whichever scan (file) each came in, and how each joined; then pages in no order yet.
// The scan picker only narrows: "Semua scan" (every order) by default.
import Link from "next/link";
import { getWords, need, one, type SearchParams } from "@/lib/api";
import type { Bundles } from "@/lib/types";
import Cap from "@/components/Cap";
import { BatchPicker, BatchTags } from "@/components/Batch";
import { BundleSearch, HeldCard } from "./parts";

export const metadata = { title: "Berkas per SOR" };

/** The part of each file name that differs within one order: its files share the customer's prefix
 * ("02 1100000001 - PT CONTOH … - 2 PO - Purchase Order.pdf" → "2 PO - Purchase Order.pdf"). */
function ownParts(names: string[]): Record<string, string> {
  const uniq = [...new Set(names)];
  let n = 0;
  if (uniq.length > 1) {
    while (uniq.every((x) => x[n] !== undefined && x[n] === uniq[0][n])) n++;
    n = uniq[0].lastIndexOf(" - ", n) >= 0 ? uniq[0].lastIndexOf(" - ", n) + 3 : 0;   // back to the last " - "
  }
  return Object.fromEntries(uniq.map((x) => [x, x.slice(n) || x]));
}

export default async function BundlesPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const [{ batch, upload, uploads, view: d }, w] = await Promise.all([need<Bundles>("/bundles", { batch: one(sp.batch), upload: one(sp.upload) }), getWords()]);
  const waitingFp = d ? d.bundles.filter((b) => b.hold).length : 0;
  const range = (a: number, z: number) => (z !== a ? `${a}–${z}` : `${a}`);
  return (
    <>
      <section className="head">
        <div className="head-row"><h1>Berkas per SOR</h1>
          {uploads.length > 0 && <BatchPicker path="/bundles" value={upload} options={uploads} />}</div>
      </section>

      {!d ? <p className="kosong">Belum ada scan. <Link href="/upload">Unggah scan</Link> dulu.</p> : (
        <>
          <p className="bx-line">{upload || batch ? "Order dengan dokumen di batch ini" : "Semua batch"}: <a href="#berkas"><b>{d.complete}</b> berkas lengkap</a>,{" "}
            <a href="#berkas"><b>{waitingFp}</b> menunggu Faktur</a>,{" "}
            <a href="#held" className={d.held.length ? "need" : undefined}><b>{d.held.length}</b> dokumen menunggu nomor</a>, dan{" "}
            <a href="#lepas" className={d.unplaced.length ? "need" : undefined}><b>{d.unplaced.length}</b> halaman belum masuk berkas</a>.</p>

          {d.held.length > 0 && (
            <>
              <h2 className="sect" id="held">Pastikan nomor dokumen</h2>
              <div className="bx-held">{d.held.map((h) => <HeldCard key={`${h.batch_id}:${h.page_from}`} h={h} batch={h.batch_id} />)}</div>
            </>
          )}

          <BundleSearch n={d.bundles.length}>
            {d.bundles.map((b) => {
              const st = b.hold ? "hold" : b.status;
              const own = ownParts(b.documents.map((x) => x.scan));
              return (
                <article className="bx" key={b.sor} data-q={`${(b.customer ?? "").toLowerCase()} ${b.sor.toLowerCase()}`}>
                  <header className="bx-head">
                    <div className="bx-who"><b>{b.customer || "Pelanggan belum diketahui"}</b><span className="mono">{b.sor}</span>
                      <BatchTags list={b.uploads} full={b.uploads.length === 1} /></div>
                    <Cap status={st}>{b.hold === "fp_missing" ? "Menunggu Faktur" : b.hold ? "Ditahan" : w.BUNDLE[b.status] ?? b.status}</Cap>
                    <div className="bx-acts">
                      {b.status === "published" && <a className="btn tiny" href={`/documents/${b.sor}.pdf`} target="_blank" rel="noopener">PDF</a>}
                      {!b.hold && <Link className="btn tiny" href={`/review/${b.sor}?batch=${b.batch ?? batch ?? ""}`}>{b.status === "needs_review" ? "Periksa" : "Buka order"}</Link>}
                    </div>
                  </header>
                  {b.hold && <p className="bx-hold">{b.why}</p>}
                  <div className="bx-docs">
                    {b.documents.map((doc) => (
                      <Link key={`${doc.batch_id}:${doc.page_from}`} className="bx-tile" href={`/batches/${doc.batch_id}/pages/${doc.page_from}`}
                            title={`${doc.scan}, halaman ${doc.page_from}`}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <span className="bx-img">{doc.thumb && <img src={doc.thumb} alt="" loading="lazy" />}
                          {doc.pages.length > 1 && <em>{doc.pages.length} hal.</em>}</span>
                        <span className={`tchip t-${doc.type}`}>{w.DOC_SHORT[doc.type] ?? doc.type}</span>
                        <span>hal. {range(doc.page_from, doc.page_to)}</span>
                        {b.many_scans && <small className="bx-scan" title={doc.scan}>{own[doc.scan]}</small>}
                        {doc.joined && <small>{doc.joined}</small>}
                      </Link>
                    ))}
                  </div>
                </article>
              );
            })}
          </BundleSearch>

          <h2 className="sect" id="lepas">Halaman belum masuk berkas ({d.unplaced.length})</h2>
          {d.unplaced.length ? (
            <div className="bx-docs loose">
              {d.unplaced.map((u) => (
                <Link key={`${u.batch_id}:${u.page}`} className="bx-tile" href={`/batches/${u.batch_id}/pages/${u.page}`} title={`${u.scan}: ${u.why}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <span className="bx-img">{u.thumb && <img src={u.thumb} alt="" loading="lazy" />}</span>
                  <b>hal. {u.page}</b>{!batch && <small className="bx-scan">{u.upload ? `${u.upload} · ` : ""}{u.scan}</small>}<small>{u.why}</small>
                </Link>
              ))}
            </div>
          ) : <p className="muted small">✓ Semua halaman sudah masuk ke berkas.</p>}

          <details className="tech"><summary>Detail teknis <small>(folder penyimpanan tiap berkas)</small></summary>
            <table className="grid small">
              <thead><tr><th>SOR</th><th>Folder di penyimpanan</th><th>Bergabung lewat (teks sistem)</th></tr></thead>
              <tbody>{d.bundles.map((b) => (
                <tr key={b.sor}><td className="mono">{b.sor}</td><td className="mono">{b.folder || "—"}</td>
                  <td className="small">{b.documents.map((doc) => <span key={`${doc.batch_id}:${doc.page_from}`}>{doc.type} {doc.scan} p{doc.page_from}: {(doc.evidence ?? []).join(" · ")}<br /></span>)}</td></tr>
              ))}</tbody>
            </table>
          </details>
        </>
      )}
    </>
  );
}
