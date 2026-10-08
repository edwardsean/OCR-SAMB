// Batch (2026-10-05; the user: "cant we just show a "Batches" tab only?"): every upload batch in one register, what
// each one needs next, and its five steps as small marks. Opening a batch shows its steps in order. This replaced
// Beranda and Riwayat batch; the screens over every batch at once moved under Teknis.
import Link from "next/link";
import { need, get, one, type SearchParams } from "@/lib/api";
import { tgl } from "@/lib/format";
import type { Session, Upload } from "@/lib/types";
import AutoRefresh from "@/components/AutoRefresh";
import HealthLine from "@/components/HealthLine";
import { NAME, StepMarks, words } from "@/components/Steps";

export const metadata = { title: "Batch" };

const SHOW: [string, string, (u: Upload) => boolean][] = [
  ["perlu", "Perlu Anda", (u) => !!u.next],
  ["sistem", "Sistem bekerja", (u) => !u.next && u.steps.some((s) => s.state === "sys")],
  ["selesai", "Selesai", (u) => u.finished],
  ["semua", "Semua", () => true],
];

export default async function Batches({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const [{ uploads }, s] = await Promise.all([need<{ uploads: Upload[] }>("/uploads", { limit: 200 }), get<Session>("/session")]);
  const show = SHOW.find(([k]) => k === one(sp.lihat)) ?? SHOW[3];
  const rows = uploads.filter(show[2]).sort((a, b) => Number(!a.next) - Number(!b.next));   // needs-you first, then newest
  const yours = uploads.filter((u) => u.next).length;
  const moving = uploads.some((u) => u.steps.some((x) => x.state === "sys" && x.key === "baca" && x.busy));
  return (
    <>
      <AutoRefresh every={8000} active={moving} />
      <section className="head beranda">
        <div className="b-top">
          <span className="muted">{s?.today}</span>
          <span className="health"><HealthLine /></span>
        </div>
        <div className="head-row">
          <div>
            <h1>Batch</h1>
            <p className="lede">{uploads.length === 0 ? "Belum ada batch."
              : yours ? `${yours} dari ${uploads.length} batch menunggu Anda. Buka batch dan ikuti langkahnya dari kiri ke kanan.`
              : "Tidak ada yang menunggu Anda sekarang."}</p>
          </div>
          <Link className="btn primary" href="/upload">Unggah batch</Link>
        </div>
      </section>

      {uploads.length > 0 && (
        <>
          <nav className="seg" aria-label="Saring batch">
            {SHOW.map(([k, label, f]) => (
              <Link key={k} href={k === "semua" ? "/" : `/?lihat=${k}`} className={show[0] === k ? "on" : undefined}>
                {label} <b>{uploads.filter(f).length}</b></Link>
            ))}
          </nav>
          <table className="reg ws-list">
            <thead><tr><th>Batch</th><th>Tanggal scan</th><th className="num">Isi</th><th>Langkah 1–5</th><th>Berikutnya untuk Anda</th></tr></thead>
            <tbody>
              {rows.map((u) => {
                const href = `/uploads/${u.id}`;
                const nx = u.next ? u.steps.find((x) => x.key === u.next)! : null;
                return (
                  <tr className="row" key={u.id}>
                    <td><Link href={href}><b className="mono">{u.code}</b></Link>
                      <div className="muted small">oleh {u.uploaded_by}, diunggah {tgl(u.created_at)}</div></td>
                    <td>{tgl(u.doc_date, false)}</td>
                    <td className="num small">{u.files} file<br />{u.pages} halaman<br />{u.orders} order</td>
                    <td><StepMarks steps={u.steps} href={href} /></td>
                    <td>{nx ? <Link className="ws-next" href={`${href}?step=${nx.key}`}><b>{NAME[nx.key]}</b>
                        <span>{words(nx)[0]}</span></Link>
                      : u.finished ? <span className="cap hijau">Selesai</span>
                      : (() => {                     // nothing for you: say what it waits for, never a guess
                        const open = u.steps.find((x) => x.state !== "done");
                        return <span className="muted small">Tidak ada untuk Anda{open ? ` · ${NAME[open.key]}: ${words(open)[0].toLowerCase()}` : ""}</span>;
                      })()}</td>
                  </tr>
                );
              })}
              {!rows.length && <tr><td colSpan={5} className="muted">Tidak ada batch di kelompok ini.</td></tr>}
            </tbody>
          </table>
          <p className="muted small">Lima kotak = lima langkah: angka merah perlu Anda, ✓ selesai, ◔ sistem sedang bekerja,
            – belum ada. Klik kotaknya untuk langsung ke langkah itu.</p>
        </>
      )}
      {uploads.length === 0 && (
        <p><Link className="btn primary big" href="/upload">Unggah batch pertama</Link></p>
      )}

      <details className="howto" id="cara-kerja">
        <summary>Baru memakai sistem ini? Baca cara kerjanya</summary>
        <div className="howto-body">
          <ol className="langkah">
            <li><b>Unggah batch.</b> Tulis nama Anda dan tanggal scan, lalu pilih satu file PDF atau banyak (tidak perlu disortir). Semuanya masuk ke satu batch bernomor, misalnya BATCH-20261005-01.</li>
            <li><b>Dibaca AI.</b> Sistem membaca tiap halaman sendiri. Anda hanya perlu bertindak bila ada halaman yang gagal dibaca.</li>
            <li><b>Jenis halaman.</b> Bila AI ragu sebuah halaman itu Faktur, Tanda Terima, atau PO, Anda yang memilih. Jenisnya menentukan nomor mana yang dicari.</li>
            <li><b>Cocokkan ke order.</b> Bila nomor SOR atau PO di sebuah dokumen belum pasti, Anda memastikannya, dan dokumen masuk ke ordernya.</li>
            <li><b>Periksa order.</b> Hanya order yang tidak cocok dengan Satellite yang perlu Anda putuskan.</li>
            <li><b>Kirim ke Satellite.</b> Data tiap dokumen dan satu PDF per SOR tersimpan di Satellite.</li>
          </ol>
          <dl className="istilah">
            <dt>Batch</dt><dd>Satu kali unggah, berisi satu file atau banyak.</dd>
            <dt>SOR</dt><dd>Nomor Sales Order SAMB. Satu SOR = satu order = satu berkas dokumen.</dd>
            <dt>Faktur Penjualan</dt><dd>Faktur milik SAMB. Mencetak nomor SOR dan kode QR.</dd>
            <dt>Tanda Terima (TTG)</dt><dd>Bukti pelanggan menerima barang: GRN, Receiving Note, Good Receipt, dan lainnya.</dd>
            <dt>PO</dt><dd>Pesanan pelanggan ke SAMB. Nomornya sama dengan Nomor CPO di faktur.</dd>
            <dt>Satellite</dt><dd>Sistem SAMB sebelum SAP: data order, terima barang (CGR), dan tagihan.</dd>
            <dt>Tolakan</dt><dd>Barang yang ditolak toko, sehingga diterima lebih sedikit dari yang dipesan.</dd>
          </dl>
        </div>
      </details>
    </>
  );
}

