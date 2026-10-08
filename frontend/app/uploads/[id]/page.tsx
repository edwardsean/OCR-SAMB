// One upload batch as a workspace (2026-10-05; the user: "when we click the batch, the steps for each comes up, but it
// should be ordered correctly so that the user isnt confused"): its number, who and when, its five steps in the order
// the work depends on (components/Steps.tsx), and the open step's panel. Every step can be opened (ordered, not
// locked); the one primary button goes to the first step that needs a person.
import Link from "next/link";
import { cache } from "react";
import { get, one, type SearchParams } from "@/lib/api";
import { notFound } from "next/navigation";
import { tgl } from "@/lib/format";
import type { StepKey, UploadDetail } from "@/lib/types";
import AutoRefresh from "@/components/AutoRefresh";
import { NAME, ORDER, Stepper, count } from "@/components/Steps";
import Baca from "./Baca";
import Jenis from "./Jenis";
import Cocokkan from "./Cocokkan";
import Periksa from "./Periksa";
import Kirim from "./Kirim";

const load = cache((id: string) => get<UploadDetail>(`/uploads/${id}`));      // once per request: title and page

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  return { title: (await load((await params).id))?.upload.code ?? "Batch" };   // a missing batch: the page says 404
}

export default async function UploadPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: SearchParams }) {
  const { id } = await params;
  const sp = await searchParams;
  const d = await load(id);
  if (!d) notFound();
  const u = d.upload;
  const asked = one(sp.step) as StepKey | undefined;
  // the step asked for; else the first that needs a person; else where the system is working
  const at: StepKey = asked && ORDER.includes(asked) ? asked
    : d.next ?? d.steps.find((s) => s.state === "sys")?.key ?? (d.finished ? "kirim" : "periksa");
  // asked again every few seconds only while a page is moving (an order waiting days for SAP never keeps it busy)
  const live = d.activity.splitting.length > 0
    || d.activity.pages.some((p) => ["queued", "reading", "waiting", "waiting_ai"].includes(p.state));
  const next = d.next ? d.steps.find((s) => s.key === d.next)! : null;
  const href = `/uploads/${u.id}`;
  return (
    <>
      <AutoRefresh every={4000} active={live} />
      <section className="head">
        <p className="crumbs"><Link href="/">Batch</Link> / {u.code}</p>
        <div className="head-row">
          <div>
            <h1 className="mono">{u.code}</h1>
            <p className="batchhead">
              <span>Diunggah oleh <b>{u.uploaded_by}</b></span>
              <span>Tanggal scan <b>{tgl(u.doc_date, false)}</b></span>
              <span>Diunggah {tgl(u.created_at)}</span>
              <span><b>{u.files}</b> file, <b>{u.pages}</b> halaman, <b>{u.orders}</b> order</span>
            </p>
            {u.note && <p className="muted small">{u.note}</p>}
          </div>
          {next && next.key !== at ? (
            <Link className="btn primary" href={`${href}?step=${next.key}`}>Lanjutkan: {NAME[next.key]} ({count(next)})</Link>
          ) : d.finished ? <span className="cap hijau besar">Selesai</span> : null}
        </div>
      </section>

      <Stepper steps={d.steps} at={at} href={href} blockers={d.blockers} />

      {at === "baca" && <Baca d={d} />}
      {at === "jenis" && <Jenis d={d} />}
      {at === "cocokkan" && <Cocokkan d={d} />}
      {at === "periksa" && <Periksa d={d} />}
      {at === "kirim" && <Kirim d={d} just={one(sp.just) ?? ""} />}
    </>
  );
}
