// Periksa order: every order (SOR) of a scan in one register, grouped by what it needs.
import Link from "next/link";
import { need, one, type SearchParams } from "@/lib/api";
import type { OrderList, OrderRow } from "@/lib/types";
import ScanPicker from "@/components/ScanPicker";
import { NoticesSeen, PublishForm } from "./ListParts";

export const metadata = { title: "Periksa order" };

function Row({ r, batch }: { r: OrderRow; batch: string }) {
  const href = `/review/${r.sor_no}?batch=${encodeURIComponent(batch)}`;
  return (
    <tr className="row">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <td className="o-paper">{r.thumb && <Link href={href} tabIndex={-1}><img className="paper" src={r.thumb} alt="" loading="lazy" /></Link>}</td>
      <td><Link className="o-who" href={href}>{r.customer_name || "Pelanggan belum diketahui"}</Link>
        <span className="o-sor">{r.sor_no}</span></td>
      <td className="o-docs">{r.docs.join(", ")}</td>
      <td>{r.issues.length ? <ul className="o-isu">{r.issues.map(([label, kind]) => <li key={label} className={kind}>{label}</li>)}</ul>
        : r.status === "reviewed" ? <span className="muted small">disetujui oleh {r.reviewed_by}</span>
        : ["auto_ok", "published"].includes(r.status) ? <span className="muted small">semua cek sesuai</span> : null}</td>
      <td className="num">{r.status === "published" ? <a href={`/documents/${r.sor_no}.pdf`}>PDF</a>
        : <Link className="btn tiny" href={href}>{r.status === "needs_review" ? "Periksa" : "Buka"}</Link>}</td>
    </tr>
  );
}

export default async function Review({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const d = await need<OrderList>("/orders", { batch: one(sp.batch) });
  const published = one(sp.published);
  const batch = d.batch ?? "";
  const by = (s: string[]) => d.rows.filter((r) => s.includes(r.status));
  const [needs, wait, ok, pubd] = [by(["needs_review"]), by(["grouping"]), by(["auto_ok", "reviewed"]), by(["published"])];
  return (
    <>
      <section className="head head-row">
        <h1>Periksa order</h1>
        {d.scans.length > 0 && <ScanPicker path="/review" value={d.batch}
          options={d.scans.map((b) => ({ id: b.id, label: b.file_name + (b.need ? `, ${b.need} perlu dicek` : "") }))} />}
      </section>

      {d.fresh.length > 0 && (
        <p className="o-baru"><NoticesSeen /><b>Baru sejak terakhir Anda lihat:</b>{" "}
          {d.fresh.map((f, i) => <span key={f.sor}><Link href={`/review/${f.sor}?batch=${f.batch}`}>{f.customer || f.sor}</Link>{i < d.fresh.length - 1 ? ", " : ""}</span>)}</p>
      )}
      {published !== undefined && <p className="o-terkirim">{published} order terkirim ke Satellite.</p>}

      <nav className="seg" aria-label="Kelompok order">
        <a href="#need" className={needs.length ? "on" : undefined}>Perlu dicek <b>{needs.length}</b></a>
        <a href="#wait">Menunggu sistem <b>{wait.length}</b></a>
        <a href="#ready">Siap dikirim <b>{ok.length}</b></a>
        <a href="#published">Terkirim <b>{pubd.length}</b></a>
      </nav>

      {ok.length > 0 && <PublishForm batch={batch} n={ok.length} />}

      <table className="reg orders">
        <thead><tr><th /><th>Pelanggan</th><th>Dokumen</th><th>Yang perlu dicek</th><th /></tr></thead>
        <tbody id="need">
          <tr className="grp"><th colSpan={5}>Perlu dicek <small>{needs.length} order</small></th></tr>
          {needs.map((r) => <Row key={r.sor_no} r={r} batch={batch} />)}
          {!needs.length && <tr><td colSpan={5} className="o-kosong">Tidak ada yang perlu Anda cek di scan ini.</td></tr>}
        </tbody>
        {wait.length > 0 && <tbody id="wait"><tr className="grp"><th colSpan={5}>Menunggu sistem <small>tidak perlu tindakan</small></th></tr>
          {wait.map((r) => <Row key={r.sor_no} r={r} batch={batch} />)}</tbody>}
        {ok.length > 0 && <tbody id="ready"><tr className="grp"><th colSpan={5}>Siap dikirim ke Satellite</th></tr>
          {ok.map((r) => <Row key={r.sor_no} r={r} batch={batch} />)}</tbody>}
        {pubd.length > 0 && <tbody id="published"><tr className="grp"><th colSpan={5}>Sudah terkirim</th></tr>
          {pubd.map((r) => <Row key={r.sor_no} r={r} batch={batch} />)}</tbody>}
      </table>
    </>
  );
}
