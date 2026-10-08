// Step 5, Kirim ke Satellite: the batch's finished orders (every check passed, or approved) with the one button that
// sends them, then what this batch has already sent, the ones just sent on top.
import { need } from "@/lib/api";
import type { OrderList, PubRow, UploadDetail } from "@/lib/types";
import Row from "@/components/OrderRow";
import { PublishForm } from "@/app/review/ListParts";
import PublishedItem from "@/app/published/PublishedItem";

export default async function Kirim({ d, just }: { d: UploadDetail; just: string }) {
  const id = d.upload.id;
  const fresh = just.split(",").filter(Boolean);
  const [o, p] = await Promise.all([
    need<OrderList>("/orders", { upload: id }),
    need<{ rows: PubRow[] }>("/published", { upload: id, just }),
  ]);
  const ready = o.rows.filter((r) => r.step === "ready");
  return (
    <section className="ws-panel">
      <h2>Kirim ke Satellite</h2>
      <p className={`ws-now ${ready.length ? "need" : "wait"}`}>{ready.length
        ? `${ready.length} order siap dikirim: data tiap dokumen dan satu PDF per SOR.`
        : "Belum ada order yang siap dikirim. Order yang sesuai atau Anda setujui di langkah 4 muncul di sini."}</p>
      {just && <div className="pl-new">{fresh.length ? `${fresh.length} order baru saja dikirim ke Satellite.` : "Tidak ada order yang terkirim: cek ulangnya belum lolos."}</div>}
      {ready.length ? (
        <>
          <PublishForm batch="" upload={id} n={ready.length} back={`/uploads/${id}?step=kirim`} />
          <table className="reg orders">
            <tbody>{ready.map((r) => <Row key={r.sor_no} r={r} batch={o.batch ?? ""} back={id} here={id} />)}</tbody>
          </table>
        </>
      ) : null}

      <h3 className="ws-grp">Sudah terkirim <span>({p.rows.length})</span></h3>
      {p.rows.length ? (
        <div className="pl">
          <div className="pl-cols" aria-hidden="true"><span /><span>Pelanggan · SOR</span><span>Total faktur</span>
            <span>Dokumen</span><span>Dikirim</span><span /></div>
          {p.rows.map((r) => <PublishedItem key={r.sor_no} r={r} isNew={fresh.includes(r.sor_no)} initial={null} />)}
        </div>
      ) : <p className="muted small">Belum ada order dari batch ini yang terkirim.</p>}
    </section>
  );
}
