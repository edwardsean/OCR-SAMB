"use client";
// A screen that couldn't be drawn (the API refused or is down): say so, and offer to try again.
export default function Failed({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section className="alert bad">
      <strong>Halaman ini belum bisa ditampilkan.</strong> Coba lagi sebentar; bila tetap begini, hubungi tim IT.
      <p><button type="button" className="btn" onClick={reset}>Coba lagi</button></p>
      <details className="small"><summary>Detail untuk tim IT</summary><code>{error.message}{error.digest ? ` (${error.digest})` : ""}</code></details>
    </section>
  );
}
