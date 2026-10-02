// Beranda: the situation in one line, the work waiting for a person (over every scan), and the recent scans.
import Link from "next/link";
import { need, get } from "@/lib/api";
import { pct, tgl } from "@/lib/format";
import type { Home, Session } from "@/lib/types";
import HealthLine from "@/components/HealthLine";

export const metadata = { title: "Beranda" };

export default async function Beranda() {
  const [h, s] = await Promise.all([need<Home>("/home"), get<Session>("/session")]);
  const t = h.todo;
  const tasks: [number, string, string, string, string, string][] = [
    [t.need.n, "need", "order perlu dicek", "Ada selisih atau data yang perlu Anda pastikan sebelum order dikirim ke Satellite.", `/review?batch=${t.need.batch ?? ""}`, "Periksa"],
    [t.unsure.n, "need", "halaman belum jelas jenisnya", "Sistem ragu halaman ini Faktur, Tanda Terima, atau PO. Pilih jenisnya.", `/label?batch=${t.unsure.batch ?? ""}`, "Tentukan jenis"],
    [t.held.n, "need", "dokumen menunggu nomornya dipastikan", "Nomor SOR atau PO-nya belum terbaca pasti, jadi belum masuk ke order mana pun.", `/bundles?batch=${t.held.batch ?? ""}#held`, "Pastikan nomor"],
    [t.ready.n, "ready", "order siap dikirim ke Satellite", "Semua cek sudah sesuai atau disetujui. Kirim agar data dan PDF-nya tercatat di Satellite.", `/review?batch=${t.ready.batch ?? ""}#ready`, "Kirim"],
  ];
  const open = tasks.filter((x) => x[0]);
  const none = tasks.filter((x) => !x[0]);
  return (
    <>
      <section className="head beranda">
        <div className="b-top">
          <span className="muted">{s?.today}</span>
          <span className="health"><HealthLine /></span>
        </div>
        <h1>
          {t.need.n ? `${t.need.n} order menunggu pemeriksaan Anda`
            : open.length ? `Ada ${open.length} pekerjaan untuk Anda` : "Tidak ada yang perlu Anda kerjakan sekarang"}
        </h1>
        {t.need.n ? <p><Link className="btn primary big" href={`/review?batch=${t.need.batch}`}>Mulai periksa</Link></p>
          : !open.length ? <p><Link className="btn primary big" href="/upload">Unggah scan</Link></p> : null}
      </section>

      {open.length > 0 && (
        <section>
          <h2>Pekerjaan Anda</h2>
          <table className="reg tugas">
            <tbody>
              {open.map(([n, tone, what, why, href, button]) => (
                <tr className="row" key={what}>
                  <td className={`tugas-n ${tone}`}>{n}</td>
                  <td><b>{what[0].toUpperCase() + what.slice(1)}</b><br /><span className="muted small">{why}</span></td>
                  <td className="num"><Link className="btn" href={href}>{button}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
          {none.length > 0 && <p className="muted small tugas-nol">Tidak ada {none.map((x) => x[2]).join(", ")}.</p>}
        </section>
      )}

      <section>
        <div className="sec-head"><h2>Scan terbaru</h2><Link href="/batches">Semua scan</Link></div>
        {h.scans.length ? (
          <table className="reg">
            <thead><tr><th>File</th><th>Diterima</th><th>Halaman dibaca</th><th>Order</th><th></th></tr></thead>
            <tbody>
              {h.scans.slice(0, 6).map((sc) => {
                const reading = ["splitting", "queued", "reading"].includes(sc.status) || sc.waiting_ai > 0;
                return (
                  <tr className="row" key={sc.id}>
                    <td><Link href={`/batches/${sc.id}`}><b>{sc.file_name}</b></Link></td>
                    <td className="muted">{tgl(sc.received_at)}</td>
                    <td className="prog"><span>{sc.page_done} dari {sc.page_total}</span>{reading && <> <em className="live">sedang dibaca</em></>}
                      <div className="bar"><i style={{ width: `${pct(sc.page_done, sc.page_total)}%` }} /></div></td>
                    <td>
                      {sc.orders ? (
                        <>
                          {sc.need > 0 && <span className="chip need">{sc.need} perlu dicek</span>}
                          {sc.waiting > 0 && <span className="chip wait">{sc.waiting} menunggu sistem</span>}
                          {sc.ready > 0 && <span className="chip ok">{sc.ready} siap dikirim</span>}
                          {sc.published > 0 && <span className="chip">{sc.published} terkirim</span>}
                        </>
                      ) : <span className="muted small">belum ada</span>}
                      {sc.unsure > 0 && <span className="chip need">{sc.unsure} halaman belum jelas</span>}
                    </td>
                    <td className="num"><Link href={`/batches/${sc.id}`}>Buka</Link></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <>
            <p>Belum ada scan. Mulai dengan mengunggah file PDF hasil scan dokumen pelanggan.</p>
            <p><Link className="btn primary" href="/upload">Unggah scan pertama</Link></p>
          </>
        )}
      </section>

      <details className="howto" id="cara-kerja">
        <summary>Baru memakai sistem ini? Baca cara kerjanya</summary>
        <div className="howto-body">
          <ol className="langkah">
            <li><b>Unggah scan.</b> Satu file PDF berisi tumpukan dokumen pelanggan, tidak perlu disortir.</li>
            <li><b>Sistem membaca.</b> AI membaca tiap halaman dan mengenali jenisnya: Faktur, Tanda Terima, PO.</li>
            <li><b>Disatukan per SOR.</b> Halaman dikelompokkan per order dan dicocokkan dengan data Satellite.</li>
            <li><b>Anda memeriksa.</b> Hanya yang tidak cocok atau belum pasti muncul di <Link href="/review">Periksa order</Link>.</li>
            <li><b>Kirim ke Satellite.</b> Data tiap dokumen dan satu PDF per SOR tersimpan di Satellite.</li>
          </ol>
          <dl className="istilah">
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
