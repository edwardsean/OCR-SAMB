// Jenis halaman: the pages whose type the system couldn't decide, one at a time; a person says what each is.
import Link from "next/link";
import { need, one, type SearchParams } from "@/lib/api";
import type { LabelData } from "@/lib/types";
import LabelForm from "./LabelForm";

export const metadata = { title: "Jenis halaman" };

export default async function Label({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const d = await need<LabelData>("/labels", { batch: one(sp.batch), page: one(sp.page), after: one(sp.after) });
  const saved = one(sp.saved);
  if (!d.batch) {
    return (
      <>
        <section className="head"><h1>Jenis halaman: ini dokumen apa?</h1></section>
        <section className="alert">Belum ada scan. <Link href="/upload">Unggah scan</Link> dulu.</section>
      </>
    );
  }
  const prog = d.prog!;
  return (
    <>
      <section className="head"><h1>Jenis halaman: ini dokumen apa?</h1></section>
      <section className="progress">
        <div>{prog.unsure ? <><strong>{prog.unsure_done}</strong> dari <strong>{prog.unsure}</strong> halaman yang belum pasti sudah ditentukan jenisnya</>
          : "Di scan ini tidak ada halaman yang jenisnya belum pasti."}</div>
        <div className="bar"><i style={{ width: `${prog.unsure ? Math.round((100 * prog.unsure_done) / prog.unsure) : 100}%` }} /></div>
        <div className="muted small">semua jawaban di scan ini: {prog.labelled} (latihan {prog.practice}, ujian {prog.exam}).{" "}
          <a href={`/labels?batch=${d.batch}`}>lihat semua jawaban</a></div>
        {saved && <div className="savedmsg">✓ Halaman {saved} tersimpan</div>}
      </section>
      {!d.p ? (
        <section className="checks pass"><h2>✓ Semua halaman sudah ditentukan jenisnya</h2>
          <p>Tidak ada lagi yang perlu Anda tentukan di scan ini. Jawaban yang sudah ada bisa diubah dari{" "}
            <a href={`/labels?batch=${d.batch}`}>daftar jawaban</a>. <Link href="/">Kembali ke Beranda</Link>.</p></section>
      ) : <LabelForm key={`${d.batch}/${d.page}`} d={d} />}
    </>
  );
}
