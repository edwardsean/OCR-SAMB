import Link from "next/link";

export default function NotFound() {
  return (
    <section className="head">
      <h1>Tidak ditemukan</h1>
      <p>Halaman, scan, atau order ini tidak ada. <Link href="/">Kembali ke daftar batch</Link>.</p>
    </section>
  );
}
