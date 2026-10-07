"use client";
// An upload batch (2026-10-05, the user: "I was confused on when and who and what batch is this document"): its number
// as a tag, with who uploaded it and the scan date beside or on hover; and the picker that narrows a screen to one.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { tgl } from "@/lib/format";
import type { Upload, UploadRef } from "@/lib/types";

const who = (u: UploadRef) => `${u.uploaded_by}, scan ${tgl(u.doc_date, false)}`;

/** BATCH-20261005-01 as a link to its batch page; "full" adds who and the date after it. */
export function BatchTag({ u, full }: { u: UploadRef | null | undefined; full?: boolean }) {
  if (!u) return null;
  return (
    <span className="batchtag">
      <Link href={`/uploads/${u.id}`} title={`Batch ${u.code}: diunggah ${who(u)}`} onClick={(e) => e.stopPropagation()}>{u.code}</Link>
      {full && <span className="muted"> · {who(u)}</span>}
    </span>
  );
}

/** The batches an order's documents came in: one tag each. */
export function BatchTags({ list, full }: { list: UploadRef[] | undefined; full?: boolean }) {
  if (!list?.length) return null;
  return <span className="batchtags">{list.map((u) => <BatchTag key={u.code} u={u} full={full} />)}</span>;
}

/** "Batch [Semua batch ▾]": narrows the screen to the orders with a document in one upload batch. */
export function BatchPicker({ path, value, options, extra }: {
  path: string; value: number | null; options: Upload[]; extra?: string;
}) {
  const router = useRouter();
  return (
    <label className="muted small">Batch{" "}
      <select className="scanpick" value={value ?? ""} onChange={(e) => router.push(e.target.value ? `${path}?upload=${e.target.value}${extra ?? ""}` : path)}>
        <option value="">Semua batch</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>{o.code} · {o.uploaded_by} · scan {tgl(o.doc_date, false)} · {o.files} file{o.need ? ` · ${o.need} perlu dicek` : ""}</option>
        ))}
      </select>
    </label>
  );
}
