// e-Faktur PDFs (services/common/efaktur.py, 2026-10-12): the tax office's file for an order, attached to the order's
// PDF and never read by the AI. What each one's state says, and the list a batch's page and the Order tab show.
import type { EFaktur } from "@/lib/types";

export const EF_SAYS: Record<EFaktur["status"], [string, string]> = {
  attached: ["Terlampir di PDF", "cap hijau"],
  linked: ["Ikut saat order dikirim", "cap biru"],
  waiting: ["Menunggu data Satellite", "cap kuning"],
  held: ["Nomor billing berbeda", "cap merah"],
};

export function EFakturStatus({ e }: { e: Pick<EFaktur, "status" | "attached_version"> }) {
  const [say, ink] = EF_SAYS[e.status];
  return <span className={ink}>{say}{e.status === "attached" && e.attached_version ? ` v${e.attached_version}` : ""}</span>;
}

const RANK: Record<EFaktur["status"], number> = { held: 0, waiting: 1, linked: 2, attached: 3 };
const SHOW = 200;

export function EFakturList({ list, title }: { list: EFaktur[]; title?: string }) {
  if (!list.length) return null;
  const n = (s: EFaktur["status"]) => list.filter((e) => e.status === s).length;
  const rows = [...list].sort((a, b) => RANK[a.status] - RANK[b.status] || a.sor_no.localeCompare(b.sor_no));
  return (
    <section className="efaktur">
      <h2>{title ?? "Faktur Pajak"} <small className="muted">
        {list.length} e-Faktur · {n("attached")} terlampir
        {n("linked") ? ` · ${n("linked")} ikut saat order dikirim` : ""}
        {n("waiting") ? ` · ${n("waiting")} menunggu data Satellite` : ""}
        {n("held") ? ` · ${n("held")} nomor billing berbeda` : ""}</small></h2>
      <table className="reg">
        <thead><tr><th>SOR</th><th>Nomor billing</th><th>Pembeli</th><th>Status</th><th /></tr></thead>
        <tbody>{rows.slice(0, SHOW).map((e) => (
          <tr key={e.id}>
            <td className="mono">{e.sor_no}</td>
            <td className="mono">{e.billing_no}</td>
            <td className="small">{e.buyer}</td>
            <td><EFakturStatus e={e} />{e.why && e.status !== "attached" && <div className="small muted">{e.why}</div>}</td>
            <td>{e.status === "attached" && <a href={`/documents/${e.sor_no}.pdf`} target="_blank" rel="noopener">PDF</a>}</td>
          </tr>))}
        </tbody>
      </table>
      {rows.length > SHOW && <p className="muted small">dan {rows.length - SHOW} lainnya</p>}
    </section>
  );
}
