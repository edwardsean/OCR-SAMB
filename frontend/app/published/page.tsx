// Data terkirim: every order published to Satellite, newest first; the ones just published (after Kirim on Periksa
// order) on top and open, the others opened on a click (their data loads then).
import { get, need, one, type SearchParams } from "@/lib/api";
import type { Published, PubRow, Upload } from "@/lib/types";
import { BatchPicker } from "@/components/Batch";
import PublishedItem from "./PublishedItem";

export const metadata = { title: "Data terkirim" };

export default async function PublishedPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const batch = one(sp.batch) || null, just = one(sp.just) ?? "", focus = one(sp.sor), upload = one(sp.upload) || null;
  const fresh = just.split(",").filter(Boolean);
  const { rows, uploads } = await need<{ rows: PubRow[]; uploads: Upload[] }>("/published", { batch, just, upload });
  const opened = Object.fromEntries(await Promise.all([...fresh, ...(focus ? [focus] : [])].map(
    async (s) => [s, await get<Published>(`/published/${s}`)] as const)));
  return (
    <div className="pl">
      <div className="pl-top">
        <h1>Data terkirim ke Satellite</h1>
        {uploads.length > 0 && <BatchPicker path="/published" value={upload ? Number(upload) : null} options={uploads} />}
      </div>
      {fresh.length > 0 && <div className="pl-new">{fresh.length} order baru saja dikirim ke Satellite.</div>}
      {rows.length > 0 && (
        <div className="pl-cols" aria-hidden="true"><span /><span>Pelanggan · SOR</span><span>Total faktur</span>
          <span>Dokumen</span><span>Dikirim</span><span /></div>
      )}
      {rows.map((r) => <PublishedItem key={r.sor_no} r={r} isNew={fresh.includes(r.sor_no)} initial={opened[r.sor_no] ?? null} />)}
      {!rows.length && (
        <div className="kosong"><p><b>Belum ada order yang terkirim ke Satellite.</b> Order yang semua ceknya lolos bisa
          dikirim dari <a href="/review">Periksa order</a>.</p></div>
      )}
    </div>
  );
}
