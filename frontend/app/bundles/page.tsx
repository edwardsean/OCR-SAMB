// Berkas per SOR (the user, 2026-10-02: "just make it easy to read and know how to use for users"): what needs a
// person first (documents whose number isn't sure), then one entry per order with its documents as page tiles and how
// each joined, then pages in no order yet.
import Link from "next/link";
import { getWords, need, one, type SearchParams } from "@/lib/api";
import type { Bundles } from "@/lib/types";
import Cap from "@/components/Cap";
import ScanPicker from "@/components/ScanPicker";
import { BundleSearch, HeldCard } from "./parts";

export const metadata = { title: "Berkas per SOR" };

export default async function BundlesPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const [{ batch, scans, view: d }, w] = await Promise.all([need<Bundles>("/bundles", { batch: one(sp.batch) }), getWords()]);
  const waitingFp = d ? d.bundles.filter((b) => b.hold).length : 0;
  const range = (a: number, z: number) => (z !== a ? `${a}–${z}` : `${a}`);
  return (
    <>
      <section className="head">
        <div className="head-row"><h1>Berkas per SOR</h1>
          {scans.length > 0 && <ScanPicker path="/bundles" value={batch} options={scans.map((s) => ({ id: s.id, label: s.file_name }))} />}</div>
      </section>

      {!d ? <p className="kosong">Belum ada scan. <Link href="/upload">Unggah scan</Link> dulu.</p> : (
        <>
          <p className="bx-line">Di scan ini: <a href="#berkas"><b>{d.complete}</b> berkas lengkap</a>,{" "}
            <a href="#berkas"><b>{waitingFp}</b> menunggu Faktur</a>,{" "}
            <a href="#held" className={d.held.length ? "need" : undefined}><b>{d.held.length}</b> dokumen menunggu nomor</a>, dan{" "}
            <a href="#lepas" className={d.unplaced.length ? "need" : undefined}><b>{d.unplaced.length}</b> halaman belum masuk berkas</a>.</p>

          {d.held.length > 0 && (
            <>
              <h2 className="sect" id="held">Pastikan nomor dokumen</h2>
              <div className="bx-held">{d.held.map((h) => <HeldCard key={h.page_from} h={h} batch={batch!} />)}</div>
            </>
          )}

          <BundleSearch n={d.bundles.length}>
            {d.bundles.map((b) => {
              const st = b.hold ? "hold" : b.status;
              return (
                <article className="bx" key={b.sor} data-q={`${(b.customer ?? "").toLowerCase()} ${b.sor.toLowerCase()}`}>
                  <header className="bx-head">
                    <div className="bx-who"><b>{b.customer || "Pelanggan belum diketahui"}</b><span className="mono">{b.sor}</span></div>
                    <Cap status={st}>{b.hold === "fp_missing" ? "Menunggu Faktur" : b.hold ? "Ditahan" : w.BUNDLE[b.status] ?? b.status}</Cap>
                    <div className="bx-acts">
                      {b.status === "published" && <a className="btn tiny" href={`/documents/${b.sor}.pdf`} target="_blank" rel="noopener">PDF</a>}
                      {!b.hold && <Link className="btn tiny" href={`/review/${b.sor}?batch=${batch}`}>{b.status === "needs_review" ? "Periksa" : "Buka order"}</Link>}
                    </div>
                  </header>
                  {b.hold && <p className="bx-hold">{b.why}</p>}
                  <div className="bx-docs">
                    {b.documents.map((doc) => (
                      <Link key={doc.page_from} className="bx-tile" href={`/batches/${batch}/pages/${doc.page_from}`} title={`Buka halaman ${doc.page_from}`}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <span className="bx-img">{doc.thumb && <img src={doc.thumb} alt="" loading="lazy" />}
                          {doc.pages.length > 1 && <em>{doc.pages.length} hal.</em>}</span>
                        <b>{w.DOC_SHORT[doc.type] ?? doc.type}</b>
                        <span>hal. {range(doc.page_from, doc.page_to)}</span>
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
                <Link key={u.page} className="bx-tile" href={`/batches/${batch}/pages/${u.page}`} title={u.why}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <span className="bx-img">{u.thumb && <img src={u.thumb} alt="" loading="lazy" />}</span>
                  <b>hal. {u.page}</b><small>{u.why}</small>
                </Link>
              ))}
            </div>
          ) : <p className="muted small">✓ Semua halaman sudah masuk ke berkas.</p>}

          <details className="tech"><summary>Detail teknis <small>(folder penyimpanan tiap berkas)</small></summary>
            <table className="grid small">
              <thead><tr><th>SOR</th><th>Folder di penyimpanan</th><th>Bergabung lewat (teks sistem)</th></tr></thead>
              <tbody>{d.bundles.map((b) => (
                <tr key={b.sor}><td className="mono">{b.sor}</td><td className="mono">{b.folder || "—"}</td>
                  <td className="small">{b.documents.map((doc) => <span key={doc.page_from}>{doc.type} p{doc.page_from}: {(doc.evidence ?? []).join(" · ")}<br /></span>)}</td></tr>
              ))}</tbody>
            </table>
          </details>
        </>
      )}
    </>
  );
}
