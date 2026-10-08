// Step 3, Cocokkan ke order: the batch's documents whose linking number (SOR or PO) isn't sure, in three groups: those
// an order waits for (a person confirms the number as printed), those the system or SAP will settle, and those that
// never block sending (a Faktur Pajak). Pages still waiting for steps 1–2 are named, not repeated.
import Link from "next/link";
import { need } from "@/lib/api";
import type { BundleDoc, Bundles, UploadDetail } from "@/lib/types";
import { HeldCard } from "@/app/bundles/parts";

function Group({ title, note, list }: { title: string; note: string; list: BundleDoc[] }) {
  return (
    <>
      <h3 className="ws-grp">{title} <span>({list.length}) {note}</span></h3>
      <div className="bx-held">{list.map((h) => <HeldCard key={`${h.batch_id}:${h.page_from}`} h={h} batch={h.batch_id} />)}</div>
    </>
  );
}

export default async function Cocokkan({ d }: { d: UploadDetail }) {
  const s = d.steps[2];
  if (s.key !== "cocokkan") return null;
  const id = d.upload.id;
  const { view: v } = await need<Bundles>("/bundles", { upload: id });
  const held = v?.held ?? [];
  const by = (g: string) => held.filter((h) => (h.group ?? "block") === g);
  const [block, wait, later] = [by("block"), by("wait"), by("later")];
  const earlier = s.loose_unread + s.loose_unsure;
  return (
    <section className="ws-panel">
      <h2>Cocokkan ke order</h2>
      <p className={`ws-now ${block.length ? "need" : wait.length ? "sys" : s.state === "done" ? "ok" : "wait"}`}>
        {block.length ? `${block.length} dokumen belum tahu ordernya. Pastikan nomornya seperti tercetak; dokumen itu langsung masuk ke ordernya.`
          : wait.length ? `${wait.length} dokumen menunggu sistem (AI atau SAP). Tidak perlu tindakan.`
          : s.state === "done" ? "Semua dokumen sudah masuk ordernya."
          : "Menunggu langkah sebelumnya: dokumen muncul di sini setelah halamannya dibaca dan jenisnya pasti."}</p>
      {earlier > 0 && (
        <p className="ws-dep">
          {s.loose_unsure > 0 && <>{s.loose_unsure} halaman lain menunggu <Link href={`/uploads/${id}?step=jenis`}>langkah 2</Link> (jenisnya belum pasti)</>}
          {s.loose_unsure > 0 && s.loose_unread > 0 && ", "}
          {s.loose_unread > 0 && <>{s.loose_unread} halaman menunggu <Link href={`/uploads/${id}?step=baca`}>langkah 1</Link> (belum terbaca)</>}.
          {" "}Setelah itu, dokumennya muncul di sini atau langsung masuk ke ordernya.
        </p>
      )}

      {block.length > 0 && <Group title="Menghalangi order" note="pastikan nomornya" list={block} />}
      {wait.length > 0 && <Group title="Menunggu sistem" note="AI atau SAP; tidak perlu tindakan" list={wait} />}
      {later.length > 0 && <Group title="Faktur Pajak menunggu SAP" note="nomornya datang dari SAP; tidak menghalangi order" list={later} />}

      {s.loose_other > 0 && v && (
        <details className="small">
          <summary>{s.loose_other} halaman lain belum masuk berkas</summary>
          <div className="bx-docs loose">
            {v.unplaced.map((u) => (
              <Link key={`${u.batch_id}:${u.page}`} className="bx-tile" href={`/batches/${u.batch_id}/pages/${u.page}`} title={`${u.scan}: ${u.why}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <span className="bx-img">{u.thumb && <img src={u.thumb} alt="" loading="lazy" />}</span>
                <b>hal. {u.page}</b><small className="bx-scan">{u.scan}</small><small>{u.why}</small>
              </Link>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
