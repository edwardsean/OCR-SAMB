// Step 1, Dibaca AI: what every page is doing now (services/api/activity.py: one state per page, the same the step's
// count uses, so the two never disagree), what the batch waits for, and when it should be done. Pages still at work
// first, each with what is happening and for how long; a stuck one with why and "Coba lagi" (never for an order
// already sent to Satellite); finished pages folded.
import Link from "next/link";
import { getWords } from "@/lib/api";
import { kira, lama, pct, sejak } from "@/lib/format";
import type { PageNow, UploadDetail } from "@/lib/types";
import Retry from "./Retry";

const enc = encodeURIComponent;
const SHOW_QUEUED = 12;                  // a long queue: the first few, then how many more

/** The page's one-line state, in its ink. */
function State({ p }: { p: PageNow }) {
  const again = p.again && p.state !== "failed" ? "Dibaca ulang · " : "";
  switch (p.state) {
    case "reading":
      return <span className="pn sys">◔ {again}{p.text}</span>;
    case "queued":
      return <span className="pn wait">{again}{p.text}</span>;
    case "waiting":
      return <span className="pn wait">{again}Menunggu · {p.text}</span>;
    case "waiting_ai":
      return <span className="pn sys">◔ {p.text}</span>;
    case "failed":
      return <span className="pn need">Gagal · {p.text}</span>;
    case "idle":
      return <span className="pn">{p.text}</span>;
    default:
      return <span className="pn ok">✓ Selesai{p.ms ? ` dalam ${lama(p.ms)}` : ""}</span>;
  }
}

function Action({ p, notNow }: { p: PageNow; notNow: string | null }) {
  if (p.state !== "failed") return null;
  if (p.published) return <span className="small muted">Ordernya sudah dikirim ke Satellite.</span>;
  // the AI is refused right now (a limit, a used-up quota, no model): shown, greyed, with why (the user, 2026-10-08:
  // "where is the coba lagi button?")
  if (notNow) return <button className="btn tiny" disabled title={notNow}>Coba lagi</button>;
  return (
    <>
      {p.cause === "setting" && <p className="small"><a href="/settings">Buka Model &amp; kunci API</a></p>}
      <Retry path={`/scans/${enc(p.batch_id)}/pages/${p.page_no}/retry`} />
    </>
  );
}

export default async function Baca({ d }: { d: UploadDetail }) {
  const s = d.steps[0];
  if (s.key !== "baca") return null;
  const w = await getWords();
  const now = Date.now();
  const a = d.activity;
  const pages = a.pages;
  const open = pages.filter((p) => p.state !== "done");
  const done = pages.filter((p) => p.state === "done");
  const reading = open.filter((p) => p.state === "reading").length;
  const queued = open.filter((p) => p.state === "queued");
  const failed = open.filter((p) => p.state === "failed");
  const shown = [...open.filter((p) => p.state !== "queued"), ...queued.slice(0, SHOW_QUEUED)];
  const retryable = failed.filter((p) => p.can_retry).length + d.failed_files.length;
  const eta = kira(a.eta_s);

  const quota = !!d.not_now?.startsWith("Kuota gratis");
  let line: [string, string];
  if (d.not_now)                       // what stops everything comes first: nothing can be tried until it is solved
    line = [quota ? "need" : "wait", (quota ? "" : "Menunggu: ") + d.not_now
      + (failed.length ? ` ${failed.length} halaman gagal: Coba lagi bisa setelah itu.` : "")];
  else if (failed.length || d.failed_files.length)
    line = ["need", `${failed.length + d.failed_files.length} ${failed.length ? "halaman" : "file"} gagal dibaca. Coba lagi di bawah; sisanya lanjut sendiri.`];
  else if (a.splitting.length && !pages.length) line = ["sys", "File sedang dipecah menjadi halaman."];
  else if (reading || queued.length)
    line = ["sys", (reading ? `AI sedang membaca ${reading} halaman${queued.length ? `; ${queued.length} lagi antre` : ""}.`
      : `${queued.length} halaman antre, menunggu giliran dibaca.`)
      + (eta ? ` Perkiraan selesai ${eta}.` : "") + " Tidak perlu tindakan."];
  else if (s.waiting_ai) line = ["sys", `AI melihat ulang beberapa nilai di ${s.waiting_ai} halaman. Tidak perlu tindakan.`];
  else if (s.unscheduled) line = ["wait", `${s.unscheduled} halaman belum dijadwalkan untuk dibaca.`];
  else line = ["ok", pages.length > 1 ? `Semua ${pages.length} halaman selesai dibaca.` : pages.length ? "Halaman ini selesai dibaca."
    : "Belum ada halaman."];

  return (
    <section className="ws-panel">
      <h2>Dibaca AI</h2>
      <p className={`ws-now ${line[0]}`}>{line[1]}
        {(d.not_now?.startsWith("Model belum diatur") || quota) && <> <a href="/settings">Buka Model &amp; kunci API</a></>}</p>
      {s.pages > 0 && (
        <div className="prog" style={{ maxWidth: 520 }}>
          <span>{s.done} dari {s.pages} halaman selesai</span>
          <div className="bar"><i style={{ width: `${pct(s.done, s.pages)}%` }} /></div>
        </div>
      )}

      {a.splitting.length > 0 && (
        <ul className="pn-files small">
          {a.splitting.map((f) => <li key={f.batch_id}>◔ {f.file_name}: dipecah menjadi {f.page_total} halaman</li>)}
        </ul>
      )}

      {d.failed_files.length > 0 && (
        <table className="reg pn-table">
          <thead><tr><th>File</th><th>Sekarang</th><th /></tr></thead>
          <tbody>
            {d.failed_files.map((f) => (
              <tr key={f.batch_id}>
                <td>{f.file_name}</td>
                <td><span className="pn need">Gagal · file ini tidak bisa dipecah menjadi halaman.</span>
                  {f.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{f.error}</code></details>}</td>
                <td><Retry path={`/scans/${enc(f.batch_id)}/retry`} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {shown.length > 0 && (
        <table className="reg pn-table">
          <thead><tr><th>Halaman</th><th>Jenis</th><th>Sekarang</th><th className="num">Lama</th><th /></tr></thead>
          <tbody>
            {shown.map((p) => (
              <tr key={`${p.batch_id}:${p.page_no}`}>
                <td><Link href={`/batches/${p.batch_id}/pages/${p.page_no}`}>{p.file_name}</Link> <span className="muted">· hal. {p.page_no}</span></td>
                <td>{p.doc_type ? <span className={`tchip t-${p.doc_type}`}>{w.DOC_SHORT[p.doc_type] ?? p.doc_type}</span>
                  : p.unsure ? <span className="tchip t-unsure">?</span> : <span className="muted">—</span>}</td>
                <td><State p={p} />
                  {p.state === "failed" && p.error && <details className="small"><summary>Detail untuk tim IT</summary><code>{p.error}</code></details>}</td>
                <td className="num muted">{p.state === "failed" ? "" : lama(sejak(p.since, now))}</td>
                <td><Action p={p} notNow={d.not_now} /></td>
              </tr>
            ))}
            {queued.length > SHOW_QUEUED && (
              <tr><td colSpan={5} className="muted small">dan {queued.length - SHOW_QUEUED} halaman lagi antre</td></tr>
            )}
          </tbody>
        </table>
      )}
      {retryable > 1 && (d.not_now
        ? <button className="btn tiny" disabled title={d.not_now}>Coba lagi semua ({retryable})</button>
        : <Retry path={`/uploads/${d.upload.id}/retries`} label={`Coba lagi semua (${retryable})`} />)}

      {done.length > 0 && (
        <details className="ws-files" open={!open.length && done.length <= 10}>
          <summary className="ws-grp">✓ {done.length} halaman selesai</summary>
          <table className="reg pn-table">
            <tbody>
              {done.map((p) => (
                <tr key={`${p.batch_id}:${p.page_no}`}>
                  <td><Link href={`/batches/${p.batch_id}/pages/${p.page_no}`}>{p.file_name}</Link> <span className="muted">· hal. {p.page_no}</span></td>
                  <td>{p.doc_type ? <span className={`tchip t-${p.doc_type}`}>{w.DOC_SHORT[p.doc_type] ?? p.doc_type}</span>
                    : p.unsure ? <span className="tchip t-unsure">?</span> : <span className="muted">—</span>}</td>
                  <td className="num muted">{p.ms ? lama(p.ms) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}
