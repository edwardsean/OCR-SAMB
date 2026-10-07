"use client";
// One published order in the register: its line, and (opened) what was written, loaded the first time it opens.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/client";
import { rp, tgl } from "@/lib/format";
import type { Published, PubRow } from "@/lib/types";
import PublishedData from "@/components/PublishedData";
import { BatchTags } from "@/components/Batch";

export default function PublishedItem({ r, isNew, initial }: { r: PubRow; isNew: boolean; initial: Published | null }) {
  const [open, setOpen] = useState(!!initial);
  const [pv, setPv] = useState<Published | null>(initial);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (!open || pv) return;
    api.get<Published>(`/published/${r.sor_no}`).then((a) => (a.ok ? setPv(a.data) : setFailed(true)));
  }, [open, pv, r.sor_no]);
  useEffect(() => { if (initial && !isNew) ref.current?.scrollIntoView({ block: "start" }); }, [initial, isNew]);

  return (
    <details ref={ref} className={"pl-item" + (isNew ? " new" : "")} id={r.sor_no} open={open}
             onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span className="pl-chev" aria-hidden="true" />
        <span className="pl-who"><b>{r.customer_name || "Pelanggan tidak diketahui"}</b>
          <span className="sor">{r.sor_no}</span> <BatchTags list={r.uploads} />{isNew && <> <span className="chip ok">baru dikirim</span></>}</span>
        <span className="pl-total">{rp(r.total)}</span>
        <span className="pl-docs">Faktur{r.pos ? `, ${r.pos} PO` : ""}{r.ttgs ? `, ${r.ttgs} Tanda Terima` : ""}
          <small>{r.page_count} halaman{r.version > 1 ? `, versi ${r.version}` : ""}</small></span>
        <span className="pl-when">{tgl(r.updated_at)}</span>
        <span className="pl-acts">
          <a className="btn tiny" href={`/documents/${r.sor_no}.pdf`} target="_blank" rel="noopener" onClick={(e) => e.stopPropagation()}
             title="PDF yang dikirim ke Satellite">PDF</a>
          <Link className="btn tiny" href={`/review/${r.sor_no}?batch=${encodeURIComponent(r.source_batch.split(",")[0])}`} onClick={(e) => e.stopPropagation()}
                title="Halaman order di Periksa order">Order</Link>
        </span>
      </summary>
      <div className="pl-body">
        {pv ? <PublishedData pv={pv} /> : failed ? <p className="muted small">Order ini belum terkirim ke Satellite.</p>
          : open ? <p className="muted small">memuat…</p> : null}
      </div>
    </details>
  );
}
