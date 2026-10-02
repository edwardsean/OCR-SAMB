// One scan: how far it has come (five plain steps), its orders by status, and every page as a thumbnail.
import Link from "next/link";
import { getWords, need, one, type SearchParams } from "@/lib/api";
import { pct, tgl } from "@/lib/format";
import type { ScanDetail } from "@/lib/types";
import AutoRefresh from "@/components/AutoRefresh";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  return { title: (await params).id };
}

const TYPES = ["FP", "TTG", "PO", "SJ", "FPJ", "PEL", "CONTINUATION", "OTHER", "unsure"];
const FLAGS = ["rotated", "skewed", "dark_band", "faint", "poor_quality"];

export default async function ScanPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: SearchParams }) {
  const { id } = await params;
  const flt = one((await searchParams).flag);
  const [d, w] = await Promise.all([need<ScanDetail>(`/scans/${id}`), getWords()]);
  const { scan: b, pages, orders: o, flags: fc } = d;
  const total = b.page_total;
  const split = ["split", "queued", "reading", "read", "grouping", "done"].includes(b.status);
  const read = total > 0 && b.page_done >= total;
  const live = ["received", "splitting", "queued", "reading"].includes(b.status) || o.waiting > 0;
  const nqr = pages.filter((p) => p.qr_text).length;
  const ndead = pages.filter((p) => p.status === "dead_letter").length;
  const shown = pages.filter((p) => {
    const fl = p.quality_flags ?? [];
    const t = p.doc_type || (p.type_status === "unsure" ? "unsure" : "");
    return !flt || fl.includes(flt) || (flt === "qr" && p.qr_text) || (flt === "dead_letter" && p.status === "dead_letter") || flt === `type:${t}`;
  });
  const href = (f?: string) => `/batches/${id}${f ? `?flag=${encodeURIComponent(f)}` : ""}`;
  const step = (done: boolean, now: boolean) => (done ? "step done" : now ? "step now" : "step");

  return (
    <>
      <AutoRefresh every={4000} active={live} />
      <section className="head">
        <p className="crumbs"><Link href="/batches">Riwayat scan</Link> / {b.file_name}</p>
        <h1>{b.file_name}</h1>
        <p className="lede">Diterima {tgl(b.received_at)}, {total} halaman.</p>
      </section>

      <section className="flow five">
        <div className="step done"><span className="n">1</span><strong>Diterima</strong><em>file tersimpan</em></div>
        <div className={step(split, true)}><span className="n">2</span><strong>Dipecah per halaman</strong>
          <em>{b.pages_rendered} dari {total} halaman</em></div>
        <div className={step(read, split)}><span className="n">3</span><strong>Dibaca AI</strong>
          <em>{b.page_done} dari {total} halaman</em>
          {total > 0 && <div className="bar"><i style={{ width: `${pct(b.page_done, total)}%` }} /></div>}</div>
        <div className={step(!!o.total && !o.waiting, !!o.total || b.page_done > 0)}><span className="n">4</span><strong>Disatukan per order</strong>
          <em>{o.total ? <>{o.total} order (SOR){o.waiting ? ` · ${o.waiting} masih diproses` : ""}</> : "belum ada order"}</em></div>
        <div className={o.need ? "step now" : o.total && !o.waiting ? "step done" : "step"}><span className="n">5</span><strong>Diperiksa &amp; dikirim</strong>
          <em>{o.need ? `${o.need} order perlu Anda cek` : o.total && o.published === o.total ? "semua terkirim"
            : o.total ? "tidak ada yang perlu dicek" : "menunggu langkah 4"}</em></div>
      </section>

      {(o.total > 0 || o.held > 0) && (
        <div className="ordersum">
          <div className="chips">
            {o.need > 0 && <span className="chip need">{o.need} perlu dicek</span>}
            {o.waiting > 0 && <span className="chip wait">{o.waiting} menunggu sistem</span>}
            {o.ready > 0 && <span className="chip ok">{o.ready} siap dikirim</span>}
            {o.published > 0 && <span className="chip done">{o.published} terkirim</span>}
            {o.held > 0 && <Link className="chip need" href={`/bundles?batch=${id}#held`}>{o.held} dokumen menunggu nomor</Link>}
          </div>
          <div className="acts">
            <Link className="btn" href={`/bundles?batch=${id}`}>Lihat berkas per SOR</Link>
            <Link className={"btn" + (o.need || o.ready ? " primary" : "")} href={`/review?batch=${id}`}>
              {o.need ? `Periksa ${o.need} order` : "Buka daftar order"}</Link>
          </div>
        </div>
      )}

      <section id="grid">
        <h2>Halaman <span className="count">{pages.length} dari {total}</span>
          <span className="legend">Klik halaman untuk melihat dan memperbaiki isinya</span></h2>
        <div className="filters" role="group" aria-label="Saring halaman">
          <Link href={href()} className={!flt ? "on" : undefined}>Semua</Link>
          {TYPES.filter((t) => fc[`type:${t}`]).map((t) => (
            <Link key={t} href={href(`type:${t}`)} className={flt === `type:${t}` ? "on" : undefined} title={w.DOC[t] ?? t}>
              <span className={`tchip t-${t}`}>{w.DOC_SHORT[t] ?? t}</span> {fc[`type:${t}`]}</Link>
          ))}
          <span className="sep" />
          <span className="muted small filt-l">Kondisi scan:</span>
          {FLAGS.filter((f) => fc[f]).map((f) => (
            <Link key={f} href={href(f)} className={flt === f ? "on" : undefined}><span className={`badge ${f}`}>{w.FLAG[f] ?? f}</span> {fc[f]}</Link>
          ))}
          {nqr > 0 && <Link href={href("qr")} className={flt === "qr" ? "on" : undefined}><span className="badge qr">ada kode QR</span> {nqr}</Link>}
          {ndead > 0 && <Link href={href("dead_letter")} className={flt === "dead_letter" ? "on" : undefined}><span className="badge poor_quality">gagal diproses</span> {ndead}</Link>}
        </div>
        <div className="thumbs">
          {shown.map((p) => {
            const fl = p.quality_flags ?? [];
            const t = p.doc_type || (p.type_status === "unsure" ? "unsure" : "");
            const title = [`Halaman ${p.page_no}`, t && (w.DOC[t] ?? t), ...fl.map((f) => w.FLAG[f] ?? f), p.error].filter(Boolean).join(" · ");
            return (
              <Link key={p.page_no} className={`thumb s-${p.status}`} href={`/batches/${id}/pages/${p.page_no}`} title={title}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img loading="lazy" src={`/img/${p.thumb_upright_path || p.thumb_path}`} alt={`halaman ${p.page_no}`} />
                <span>{p.page_no}</span>
                {t && <em className={`ttag t-${t}`}>{t === "CONTINUATION" ? "LANJUT" : t === "unsure" ? "?" : t}</em>}
                <b className="marks">{fl.includes("rotated") && "↻"}{fl.includes("dark_band") && "▮"}{fl.includes("faint") && "◌"}
                  {fl.includes("poor_quality") && "✕"}{p.qr_text && "▦"}</b>
              </Link>
            );
          })}
        </div>
        <p className="note">Label di pojok kiri: <b>FP</b> Faktur Penjualan, <b>TTG</b> Tanda Terima, <b>PO</b> Purchase Order,
          {" "}<b>LANJUT</b> halaman lanjutan, <b>?</b> jenis belum pasti. Tanda di pojok kanan: ↻ diputar, ▮ ada pita hitam,
          ◌ cetakan pudar, ✕ kualitas buruk, ▦ ada kode QR. Halaman yang pudar belum selesai dibaca.</p>
      </section>

      <p className="tech-link"><a href={`/teknis/scan/${id}`}>Detail teknis</a> <small className="muted">(untuk tim pengembang:
        antrean, jalankan ulang, uji per fase)</small></p>
    </>
  );
}
