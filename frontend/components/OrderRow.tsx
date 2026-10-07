// One order in a register (Periksa order, and a batch's step 4): its first page, customer and SOR, its documents, what
// is left, and the button that opens it. `back` = the batch the order page returns to; `here` = the batch whose page
// shows the row: its own tag is left out, and the order's other batches say "juga di".
import Link from "next/link";
import type { OrderRow as Row } from "@/lib/types";
import { BatchTags } from "@/components/Batch";

export default function OrderRow({ r, batch, back, note, here }: {
  r: Row; batch: string; back?: number | null; note?: string; here?: number;
}) {
  const others = here ? r.uploads.filter((u) => u.id !== here) : r.uploads;
  const href = `/review/${r.sor_no}?batch=${encodeURIComponent(r.batch ?? batch)}${back ? `&upload=${back}` : ""}`;
  return (
    <tr className="row">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <td className="o-paper">{r.thumb && <Link href={href} tabIndex={-1}><img className="paper" src={r.thumb} alt="" loading="lazy" /></Link>}</td>
      <td><Link className="o-who" href={href}>{r.customer_name || "Pelanggan belum diketahui"}</Link>
        <span className="o-sor">{r.sor_no}</span> {here && others.length > 0 && <span className="muted small">juga di </span>}
        <BatchTags list={others} /></td>
      <td className="o-docs">{r.docs.join(", ")}{r.scans > 1 && <span className="muted small"> · dari {r.scans} file</span>}</td>
      <td>{r.issues.length ? <ul className="o-isu">{r.issues.map(([label, kind]) => <li key={label} className={kind}>{label}</li>)}</ul>
        : r.status === "reviewed" ? <span className="muted small">disetujui oleh {r.reviewed_by}</span>
        : ["auto_ok", "published"].includes(r.status) ? <span className="muted small">semua cek sesuai</span> : null}
        {note && <p className="ws-dep">{note}</p>}</td>
      <td className="num">{r.status === "published" ? <a href={`/documents/${r.sor_no}.pdf`}>PDF</a>
        : <Link className="btn tiny" href={href}>{r.status === "needs_review" ? "Periksa" : "Buka"}</Link>}</td>
    </tr>
  );
}
