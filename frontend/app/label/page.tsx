// Jenis halaman: the pages whose type the system couldn't decide, one at a time; a person says what each is. Opened from
// a batch's step 2 (`upload`), it walks that batch's pages and leads back to the batch when none is left.
import Link from "next/link";
import { need, one, type SearchParams } from "@/lib/api";
import type { LabelData } from "@/lib/types";
import LabelForm from "./LabelForm";

export const metadata = { title: "Jenis halaman" };

export default async function Label({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const d = await need<LabelData>("/labels", { batch: one(sp.batch), page: one(sp.page), after: one(sp.after), upload: one(sp.upload) });
  const saved = one(sp.saved);
  const up = d.upload ?? null;
  const crumbs = up && <p className="crumbs"><Link href="/">Batch</Link> / <Link href={`/uploads/${up.id}?step=jenis`}>{up.code}</Link> / Jenis halaman</p>;
  if (!d.batch) {
    return (
      <>
        <section className="head">{crumbs}<h1>Jenis halaman: ini dokumen apa?</h1></section>
        <section className="alert">{up ? "Batch ini belum punya halaman." : <>Belum ada scan. <Link href="/upload">Unggah scan</Link> dulu.</>}</section>
      </>
    );
  }
  const prog = d.prog!;
  return (
    <>
      <section className="head">{crumbs}<h1>Jenis halaman: ini dokumen apa?</h1></section>
      <section className="progress">
        {up ? (
          <div>{d.left ? <><strong>{d.left}</strong> halaman di batch <b className="mono">{up.code}</b> masih belum pasti jenisnya</>
            : <>Semua halaman di batch <b className="mono">{up.code}</b> sudah ditentukan jenisnya</>}</div>
        ) : (
          <>
            <div>{prog.unsure ? <><strong>{prog.unsure_done}</strong> dari <strong>{prog.unsure}</strong> halaman yang belum pasti sudah ditentukan jenisnya</>
              : "Di scan ini tidak ada halaman yang jenisnya belum pasti."}</div>
            <div className="bar"><i style={{ width: `${prog.unsure ? Math.round((100 * prog.unsure_done) / prog.unsure) : 100}%` }} /></div>
          </>
        )}
        <div className="muted small">semua jawaban di scan ini: {prog.labelled} (latihan {prog.practice}, ujian {prog.exam}).{" "}
          <a href={`/labels?batch=${d.batch}`}>lihat semua jawaban</a></div>
        {saved && <div className="savedmsg">✓ Halaman {saved} tersimpan. AI membaca ulang halaman itu sesuai jenisnya.</div>}
      </section>
      {!d.p ? (
        <section className="checks pass"><h2>✓ Semua halaman sudah ditentukan jenisnya</h2>
          {up ? <p>Lanjutkan ke langkah berikutnya: <Link className="btn primary" href={`/uploads/${up.id}?step=cocokkan`}>Cocokkan ke order</Link>{" "}
                atau <Link href={`/uploads/${up.id}`}>kembali ke batch</Link>.</p>
            : <p>Tidak ada lagi yang perlu Anda tentukan di scan ini. Jawaban yang sudah ada bisa diubah dari{" "}
                <a href={`/labels?batch=${d.batch}`}>daftar jawaban</a>. <Link href="/">Kembali ke Batch</Link>.</p>}</section>
      ) : <LabelForm key={`${d.batch}/${d.page}`} d={d} />}
    </>
  );
}
