// Step 4, Periksa order: the batch's orders (each whole, from every batch its documents came in), grouped by where
// they stand: needs you, waits for an earlier step of this batch (its missing document may be there), waits for a
// document from another batch, waits for the system, done.
import { need } from "@/lib/api";
import type { OrderList, OrderRow, OrderStep, UploadDetail } from "@/lib/types";
import Row from "@/components/OrderRow";
import { NAME, langkah } from "@/components/Steps";
import { NoticesSeen } from "@/app/review/ListParts";

export default async function Periksa({ d }: { d: UploadDetail }) {
  const id = d.upload.id;
  const o = await need<OrderList>("/orders", { upload: id });
  const by = (...s: OrderStep[]) => o.rows.filter((r) => s.includes(r.step ?? "need"));
  const at = langkah(d.blockers);
  const groups: [string, string, OrderRow[], string?][] = [
    ["Perlu Anda", "buka order untuk memutuskan", by("need")],
    [`Menunggu ${at}`, "dokumen yang kurang mungkin masih di sana", by("depends"),
     `Dokumen yang kurang mungkin masih di ${at} (${d.blockers.map((k) => NAME[k]).join(", ")}). Selesaikan itu dulu.`],
    ["Menunggu dokumen dari batch lain", "fakturnya belum diunggah", by("outside")],
    ["Menunggu sistem", "tidak perlu tindakan", by("waiting")],
    ["Selesai", "siap dikirim atau sudah terkirim (langkah 5)", by("ready", "published")],
  ];
  return (
    <section className="ws-panel">
      <NoticesSeen />
      <h2>Periksa order</h2>
      {(() => {
        const s = d.steps[3];
        if (s.key !== "periksa") return null;
        const [cls, text] = s.need ? ["need", `${s.need} order perlu keputusan Anda. Buka order untuk memutuskan; yang disetujui pindah ke langkah 5.`]
          : s.depends || s.waiting ? ["sys", "Order masih menunggu langkah sebelumnya atau sistem. Tidak perlu tindakan."]
          : s.state === "done" ? ["ok", "Semua order sudah diperiksa."]
          : s.outside ? ["wait", `${s.outside} order menunggu dokumen dari batch lain.`]
          : ["wait", "Menunggu langkah sebelumnya: order muncul setelah halamannya dibaca."];
        return <p className={`ws-now ${cls}`}>{text}</p>;
      })()}
      {o.rows.length ? (
        <table className="reg orders">
          <thead><tr><th /><th>Pelanggan</th><th>Dokumen</th><th>Yang perlu dicek</th><th /></tr></thead>
          {groups.filter(([, , rows], i) => rows.length || i === 0).map(([title, note, rows, dep]) => (
            <tbody key={title}>
              <tr className="grp"><th colSpan={5}>{title} <small>{rows.length} order · {note}</small></th></tr>
              {rows.map((r) => <Row key={r.sor_no} r={r} batch={o.batch ?? ""} back={id} note={dep} here={id} />)}
              {!rows.length && <tr><td colSpan={5} className="o-kosong">✓ Tidak ada order yang perlu Anda putuskan sekarang.</td></tr>}
            </tbody>
          ))}
        </table>
      ) : null}
    </section>
  );
}
