// One page: what it is (a person can change a type the classifier got wrong: Relabel), and (once read) the paper
// beside its fields, where a value is corrected by clicking it on the paper (PageFixer).
import Link from "next/link";
import { getWords, need, one, type SearchParams } from "@/lib/api";
import type { PageDetail } from "@/lib/types";
import AutoRefresh from "@/components/AutoRefresh";
import PageFixer from "./PageFixer";
import Relabel from "./Relabel";

export async function generateMetadata({ params }: { params: Promise<{ n: string }> }) {
  return { title: `Halaman ${(await params).n}` };
}

const OUT_TONE: Record<string, string> = { clear: "ok", waiting_ai: "wait", needs_person: "need", held_unsure: "need" };

/** Where to return after a fix: only a path on this app (never another site). */
function safeBack(b?: string) {
  return b && b.startsWith("/") && !b.startsWith("//") ? b : undefined;
}

export default async function PageView({ params, searchParams }: { params: Promise<{ id: string; n: string }>; searchParams: SearchParams }) {
  const { id, n } = await params;
  const sp = await searchParams;
  const pageNo = Number(n);
  const [d, w] = await Promise.all([need<PageDetail>(`/scans/${id}/pages/${pageNo}`), getWords()]);
  const { scan: b, page: p, fix, upload: up } = d;
  const t = p.doc_type;

  return (
    <>
      <section className="head pg-head">
        <p className="crumbs"><Link href="/">Batch</Link>{up && <> / <Link href={`/uploads/${up.id}`}>{up.code}</Link></>} / <Link href={`/batches/${id}`}>{b.file_name}</Link> / halaman {pageNo}</p>
        <div className="pg-title">
          <h1>Halaman {pageNo} <span className="muted">dari {b.page_total}</span>{" "}
            {t ? <span className={`tchip t-${t} big`}>{w.DOC[t] ?? t}</span>
              : p.type_status === "unsure" ? <span className="tchip t-unsure big">Jenis belum pasti</span> : null}</h1>
          <p className="pager">
            {pageNo > 1 && <Link href={`/batches/${id}/pages/${pageNo - 1}`}>Halaman sebelumnya</Link>}
            {pageNo < b.page_total && <Link href={`/batches/${id}/pages/${pageNo + 1}`}>Halaman berikutnya</Link>}
          </p>
        </div>
        <p className="pg-state">
          {p.outcome && <span className={`chip ${OUT_TONE[p.outcome] ?? ""}`}>{w.OUTCOME[p.outcome] ?? p.outcome}</span>}
          {(p.quality_flags ?? []).map((f) => <span key={f} className={`badge ${f}`}>{w.FLAG[f] ?? f}</span>)}
          {p.qr_text && <span className="badge qr">ada kode QR</span>}
        </p>
        {(t || d.label) && <Relabel batch={id} d={d} />}
        <AutoRefresh every={4000} active={p.status === "queued"} />
      </section>

      {!p.upright_path ? (
        <>
          <section className="alert">Halaman ini belum dibaca sistem ({w.BATCH[p.status] ?? p.status}). Tunggu sebentar; halaman
            ini akan terisi sendiri.
            {p.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{p.error}</code></details>}
          </section>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <section className="img"><img style={{ maxWidth: 520, width: "100%" }} src={`/img/${p.original_path}`} alt={`halaman ${pageNo} seperti di-scan`} /></section>
        </>
      ) : fix ? (
        <PageFixer key={`${id}/${pageNo}`} batch={id} page={pageNo} fix={fix} start={one(sp.fix)} back={safeBack(one(sp.back))}
                   fixed={one(sp.fixed)} />
      ) : (
        <section className="pg-plain">
          {p.type_status === "unsure" ? (
            <div className="pg-plain-msg"><b>Sistem belum yakin ini dokumen apa.</b> Tentukan jenisnya dulu; setelah itu isinya bisa
              dibaca dan diperiksa di sini.{" "}
              <Link className="btn primary" href={`/label?batch=${id}&page=${pageNo}`}>Tentukan jenis halaman ini</Link></div>
          ) : p.outcome === "waiting_ai" || p.status !== "read" ? (
            <div className="pg-plain-msg"><b>AI belum selesai membaca halaman ini.</b> Halaman ini akan terisi sendiri; tidak perlu tindakan.</div>
          ) : (
            <div className="pg-plain-msg">Halaman ini tidak punya daftar isian untuk diperiksa ({t ? w.DOC[t] ?? t : "jenis tidak diketahui"}).</div>
          )}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="pg-plain-img" src={`/img/${p.upright_path}`} alt={`halaman ${pageNo}`} />
        </section>
      )}

      <p className="tech-link"><a href={`/teknis/halaman/${id}/${pageNo}`}>Detail teknis</a> <small className="muted">(untuk tim
        pengembang: hasil scan, teks Tesseract, klasifikasi, bacaan AI, pengukuran)</small></p>
    </>
  );
}
