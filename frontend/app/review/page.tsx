// Periksa order over every batch (under Teknis since 2026-10-05: a batch's own step 4 is where the work is done):
// every order (SOR) once, grouped by what it needs. The batch picker only narrows to the orders with a document in it.
import Link from "next/link";
import { need, one, type SearchParams } from "@/lib/api";
import type { OrderList } from "@/lib/types";
import { BatchPicker } from "@/components/Batch";
import Row from "@/components/OrderRow";
import { NoticesSeen, PublishForm } from "./ListParts";

export const metadata = { title: "Periksa order" };

export default async function Review({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const d = await need<OrderList>("/orders", { batch: one(sp.batch), upload: one(sp.upload) });
  const published = one(sp.published);
  const batch = d.batch ?? "";
  const by = (s: string[]) => d.rows.filter((r) => s.includes(r.status));
  const [needs, wait, ok, pubd] = [by(["needs_review"]), by(["grouping"]), by(["auto_ok", "reviewed"]), by(["published"])];
  return (
    <>
      <section className="head head-row">
        <h1>Periksa order</h1>
        {d.uploads.length > 0 && <BatchPicker path="/review" value={d.upload} options={d.uploads} />}
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

      {ok.length > 0 && <PublishForm batch={batch} upload={d.upload} n={ok.length} />}

      <table className="reg orders">
        <thead><tr><th /><th>Pelanggan</th><th>Dokumen</th><th>Yang perlu dicek</th><th /></tr></thead>
        <tbody id="need">
          <tr className="grp"><th colSpan={5}>Perlu dicek <small>{needs.length} order</small></th></tr>
          {needs.map((r) => <Row key={r.sor_no} r={r} batch={batch} />)}
          {!needs.length && <tr><td colSpan={5} className="o-kosong">Tidak ada yang perlu Anda cek{d.upload || d.batch ? " di batch ini" : ""}.</td></tr>}
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
