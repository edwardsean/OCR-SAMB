"use client";
// The decisions a person makes on an order, each a small form that posts to the REST API and signs with the name
// typed once at the top. After an answer the order is checked again on the server, so the page is asked for again.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, why } from "@/lib/client";
import { cx } from "@/lib/format";
import type { FieldEntry, PairRow, PairRun, SoLine } from "@/lib/types";
import { useSpot } from "@/components/Spread";
import { useWords } from "@/components/Words";

type Review = {
  batch: string; sor: string; name: string; lines: SoLine[]; acceptReasons: string[]; noneReasons: string[];
  needName(): void; onFixed(page: number, field: string): void;
  /** An order page (numbered within the order) as its own scan and page: an order can span several scans. */
  at(page: number): { batch: string; page: number };
  /** How a page is named for people: "hal. 3", with its file when the order spans several scans. */
  pageName(page: number): string;
  /** the batch the order was opened from: its step 3 is where a held document's number is confirmed */
  upload: number | null;
};
export const ReviewCtx = createContext<Review | null>(null);
export function useReview(): Review {
  const r = useContext(ReviewCtx);
  if (!r) throw new Error("outside ReviewCtx");
  return r;
}

/** Post one decision: refused without a name (the name box asks for it), else sent, then the page asked again. */
export function useAct() {
  const r = useReview();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(path: string, body: Record<string, unknown>, then?: () => void) {
    if (!r.name.trim()) { r.needName(); return false; }
    setBusy(true); setError(null);
    // a decision about a page goes to that page's own scan (an order can span several)
    const where = typeof body.page === "number" ? r.at(body.page) : { batch: r.batch, page: undefined };
    const a = await api.post(`/orders/${r.sor}${path}`, {
      batch: where.batch, by: r.name.trim(), ...body, ...(where.page !== undefined ? { page: where.page } : {}),
    });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return false; }
    then?.();
    router.refresh();
    return true;
  }
  return { run, busy, error };
}

export function Err({ text }: { text: string | null }) {
  return text ? <p className="salah">{text}</p> : null;
}

function pickHref(r: Review, page: number, field: string) {
  const a = r.at(page);
  // back to the order from the page's own scan: the page it names after the fix is that scan's page
  return `/batches/${a.batch}/pages/${a.page}?fix=${encodeURIComponent(field)}&back=${encodeURIComponent(`/review/${r.sor}?batch=${a.batch}`)}`;
}

function Crop({ src }: { src: string }) {
  const [gone, setGone] = useState(false);
  // eslint-disable-next-line @next/next/no-img-element
  return gone ? null : <img className="crop" src={src} alt="" loading="lazy" onError={() => setGone(true)} />;
}

/** A value as printed, corrected or confirmed: a card's fix ("fix"), or a page that holds the order ("page"). */
export function FieldFix({ f, page, mode }: { f: FieldEntry; page: number; mode: "fix" | "page" }) {
  const r = useReview();
  const { run, busy, error } = useAct();
  const [value, setValue] = useState("");
  const sp = useSpot({ id: `f${page}:${f.name}`, page, box: f.box, approx: f.approx, label: f.label });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    run("/confirmations", { page, field: f.name, value, shown: f.value ?? "" }, () => { setValue(""); r.onFixed(page, f.name); });
  };
  return (
    <form {...sp} className={cx("pd-spot", sp.className)} onSubmit={submit}>
      {mode === "fix" && <div className="fine">{r.pageName(page)} · {f.label} · terbaca <b className="mono">{f.value ?? "kosong"}</b></div>}
      <Crop src={`/crop/${r.at(page).batch}/${r.at(page).page}/${f.name}`} />
      <div className="fixrow">
        <span className="lab">{f.label}, seperti tercetak</span>
        <input placeholder="ketik, atau pilih di halaman" required value={value} onChange={(e) => setValue(e.target.value)} />
        {mode === "page" && f.value !== null && <button type="button" className="btn suggest" onClick={() => setValue(f.value!)}>{f.value} (bacaan AI)</button>}
        {f.suggest.map(([val, w]) => <button key={val} type="button" className="btn" title={w} onClick={() => setValue(val)}>{val}</button>)}
        <button type="button" className="btn" onClick={() => setValue("(not printed)")}>{mode === "page" ? "Tidak tercetak" : "Tidak tercetak di halaman ini"}</button>
        <button className="btn primary" disabled={busy}>Simpan</button>
        <Link className="btn pick" href={pickHref(r, page, f.name)}>Pilih di halaman</Link>
      </div>
      <Err text={error} />
    </form>
  );
}

/** "Terima selisih ini, karena: …": accept a difference with a reason, in one click. */
export function AcceptForm({ check, print, receipt }: { check: string; print: string; receipt: boolean }) {
  const r = useReview();
  const w = useWords();
  const { run, busy, error } = useAct();
  const [other, setOther] = useState(false);
  const [note, setNote] = useState("");
  const send = (reason: string) => run("/acceptances", { check, input_print: print, reason, note });
  return (
    <form className="acts" onSubmit={(e) => { e.preventDefault(); send("other (say in the note)"); }}>
      <span className="lbl">{receipt ? "Atau terima apa adanya, karena:" : "Terima selisih ini, karena:"}</span>
      {r.acceptReasons.filter((x) => x !== "other (say in the note)").map((x) => (
        <button key={x} type="button" className="btn" disabled={busy} onClick={() => send(x)}>{w.REASON[x] ?? x}</button>
      ))}
      {!other ? <button type="button" className="btn" onClick={() => setOther(true)}>Lainnya…</button> : (
        <>
          <input className="note-in shown" placeholder="tulis alasannya" value={note} onChange={(e) => setNote(e.target.value)} autoFocus required />
          <button className="btn primary note-in shown" disabled={busy}>Terima</button>
        </>
      )}
      <Err text={error} />
    </form>
  );
}

/** The quantity received on one receipt row, as written: with its unit. */
export function QtyForm({ page, rowKey, shown, carton, want }: { page: number; rowKey: string; shown: string; carton: number | null; want: number | null }) {
  const { run, busy, error } = useAct();
  const r = useReview();
  const [value, setValue] = useState("");
  const field = `lines[${rowKey}].qty`;
  return (
    <form className="fixrow" onSubmit={(e) => { e.preventDefault(); run("/confirmations", { page, field, value, row_key: rowKey, shown }, () => r.onFixed(page, field)); }}>
      <span className="lab">Apa yang tertulis di kolom <b>diterima</b> pada baris ini? Tulis dengan satuannya.</span>
      <input placeholder="mis. 2 CTN atau 48 PCS" required value={value} onChange={(e) => setValue(e.target.value)} />
      {carton !== null && <button type="button" className="btn" title="yang dicatat Satellite" onClick={() => setValue(`${carton} CTN`)}>{carton} CTN (sesuai Satellite)</button>}
      <button type="button" className="btn" onClick={() => setValue("0")}>0, tidak ada yang diterima{want === 0 ? " (sesuai Satellite)" : ""}</button>
      <button className="btn primary" disabled={busy}>Simpan</button>
      <Link className="btn pick" href={pickHref(r, page, field)}>Pilih di halaman</Link>
      <Err text={error} />
    </form>
  );
}

/** "Barang ini yang mana di order SAMB?": pair a receipt row with an SO line, or with none. */
export function PairForm({ page, row }: { page: number; row: number }) {
  const r = useReview();
  const { run, busy, error } = useAct();
  const [line, setLine] = useState("");
  return (
    <form className="fixrow" onSubmit={(e) => { e.preventDefault(); run("/pairings", { page, row, line_no: line, note: line === "none" ? "not in SAMB's order" : "" }); }}>
      <span className="lab">Barang ini yang mana di order SAMB?</span>
      <select className="btn" required value={line} onChange={(e) => setLine(e.target.value)}>
        <option value="" disabled>pilih…</option>
        {r.lines.map((s) => <option key={s.line_no} value={s.line_no}>{s.line_no} · {s.description}</option>)}
        <option value="none">tidak ada: bukan barang order SAMB</option>
      </select>
      <button className="btn primary" disabled={busy}>Pasangkan</button>
      <Err text={error} />
    </form>
  );
}

/** "Barang yang belum dikenali" (2026-10-06): the customer's rows no rule paired with a line of SAMB's order. The AI
 * can suggest which line each is (in the background, a few seconds to a minute); a person's "Benar" or own choice
 * pairs the row, and that pair is saved as the customer's product code, so the product pairs by itself next time. */
export function PairingBox({ rows }: { rows: PairRow[] }) {
  const r = useReview();
  const router = useRouter();
  const [job, setJob] = useState<PairRun>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const unasked = rows.filter((p) => p.ai === null).length;
  useEffect(() => {                                     // a run already going (the page was reloaded)
    api.get<PairRun>(`/orders/${r.sor}/pair-proposals`).then((a) => { if (a.ok) setJob(a.data); });
  }, [r.sor]);
  useEffect(() => {                                     // while it runs: ask every few seconds, then draw the page again
    if (job.state !== "running") return;
    const t = setInterval(async () => {
      const a = await api.get<PairRun>(`/orders/${r.sor}/pair-proposals`);
      if (a.ok) { setJob(a.data); if (a.data.state !== "running") router.refresh(); }
    }, 3000);
    return () => clearInterval(t);
  }, [job.state, r.sor, router]);
  async function ask() {
    if (!r.name.trim()) { r.needName(); return; }
    setError(null);
    const a = await api.post<PairRun>(`/orders/${r.sor}/pair-proposals`, { by: r.name.trim() });
    if (a.ok || a.status === 409) setJob(a.data); else setError(why(a));
  }
  if (!rows.length) return null;
  const running = job.state === "running";
  return (
    <section className="rv-pair" id="pairing">
      <h3>Barang yang belum dikenali <span className="count">{rows.length}</span></h3>
      <p className="fine">Baris di PO atau Tanda Terima pelanggan yang belum diketahui barang mana di order SAMB. Pasangan yang
        Anda pastikan disimpan sebagai kode produk pelanggan ini, jadi barang yang sama dikenali sendiri di order berikutnya.</p>
      {unasked > 0 && (
        <div className="acts">
          <button className="btn" onClick={ask} disabled={running}>{running ? "AI sedang memasangkan…" : `Minta saran AI (${unasked} baris)`}</button>
          <span className="fine">{running ? "biasanya kurang dari satu menit" : "memakai kuota AI"}</span>
        </div>
      )}
      {job.state === "done" && <p className="fine">AI memberi {job.proposed} saran untuk {job.rows} baris
        {(job.proposed ?? 0) < (job.rows ?? 0) ? "; sisanya AI tidak yakin: pilih sendiri" : ""}.</p>}
      {job.state === "failed" && <p className="salah">AI tidak bisa dihubungi: {job.error}</p>}
      <Err text={error} />
      {rows.map((p) => (
        <div className="prow" key={`${p.page}:${p.i}`}>
          <div><b>{p.desc || "sebuah baris"}</b>{" "}
            <span className="fine">{p.type === "PO" ? "PO" : "Tanda Terima"} {r.pageName(p.page)}, baris {p.i + 1}
              {p.code ? ` · kode ${p.code}` : ""}{p.qty ? ` · ${p.qty} ${p.uom ?? ""}` : ""}</span></div>
          {p.ai !== null && <AiPair page={p.page} row={p.i} line={p.ai} note={p.why} />}
          <PairForm page={p.page} row={p.i} />
        </div>
      ))}
    </section>
  );
}

/** The AI's suggested SAMB line for one row, confirmed in one click. */
function AiPair({ page, row, line, note }: { page: number; row: number; line: number; note: string | null }) {
  const r = useReview();
  const { run, busy, error } = useAct();
  const s = r.lines.find((x) => x.line_no === line);
  return (
    <div className="ai-pair">
      <span>Saran AI: <b>no. {line} · {s?.description ?? ""}</b>{note && <span className="fine"> · {note}</span>}</span>
      <button className="btn primary" disabled={busy} onClick={() => run("/pairings", { page, row, line_no: String(line) })}>Benar, pasangkan</button>
      <Err text={error} />
    </div>
  );
}

/** A customer's once-only question: its rounding allowance, or what its receipts print after a rejection. */
export function CalibrateForm({ chain, name, children }: { chain: string; name: string; children: (send: (b: Record<string, string>) => void, busy: boolean) => ReactNode }) {
  const { run, busy, error } = useAct();
  return (
    <div className="acts">
      {children((b) => run("/calibrations", { chain, name, ...b }), busy)}
      <Err text={error} />
    </div>
  );
}

/** In the "every value" fold: a header value confirmed as printed. */
export function SmallConfirm({ f, page }: { f: FieldEntry; page: number }) {
  const r = useReview();
  const { run, busy, error } = useAct();
  const [value, setValue] = useState("");
  const fill = (v: string) => setValue(v);
  return (
    <div className="rv-field">
      <p><strong>{f.label}</strong> · terbaca <span className="mono">{f.value ?? "—"}</span> · <span className="muted">{f.why}</span></p>
      <form className="small" onSubmit={(e) => { e.preventDefault(); run("/confirmations", { page, field: f.name, value, shown: f.value ?? "" }, () => r.onFixed(page, f.name)); }}>
        <span className="rv-sugg">
          {f.suggest.map(([val, w]) => <button key={val} type="button" onClick={() => fill(val)}>{val} · {w}</button>)}
          {f.value !== null && <button type="button" onClick={() => fill(f.value!)}>{f.value} · bacaan AI</button>}
          <button type="button" onClick={() => fill("(not printed)")}>tidak tercetak di halaman ini</button>
        </span>
        <label>seperti tercetak <input className="mono" size={18} required value={value} onChange={(e) => setValue(e.target.value)} /></label>{" "}
        <button type="submit" disabled={busy}>Pastikan</button>
        <Err text={error} />
      </form>
    </div>
  );
}

/** In the fold: one row cell (quantity, price, discount) confirmed as printed. */
export function CellConfirm({ page, rowKey, col, label, read, hints }: {
  page: number; rowKey: string; col: string; label: string; read: string | null; hints: [string, string][];
}) {
  const { run, busy, error } = useAct();
  const [value, setValue] = useState("");
  return (
    <form className="small" onSubmit={(e) => { e.preventDefault(); run("/confirmations", { page, field: `lines[${rowKey}].${col}`, value, row_key: rowKey, shown: read ?? "" }); }}>
      <span className="rv-sugg">{hints.map(([val, w]) => <button key={val} type="button" onClick={() => setValue(val)}>{val} · {w}</button>)}</span>
      <label>{label} <input className="mono" size={10} required value={value} onChange={(e) => setValue(e.target.value)} /></label>{" "}
      <button type="submit" disabled={busy}>Pastikan</button>
      <Err text={error} />
    </form>
  );
}

/** In the fold: a row paired with an SO line, or with none (and why). */
export function PairButtons({ page, row }: { page: number; row: number }) {
  const r = useReview();
  const w = useWords();
  const { run, busy, error } = useAct();
  return (
    <div className="rv-pick">
      {r.lines.map((s) => (
        <button key={s.line_no} type="button" disabled={busy} onClick={() => run("/pairings", { page, row, line_no: String(s.line_no) })}>
          no. {s.line_no} · {s.description}</button>
      ))}
      <span>bukan salah satunya, karena: {r.noneReasons.map((x) => (
        <button key={x} type="button" disabled={busy} onClick={() => run("/pairings", { page, row, line_no: "none", note: x })}>{w.NONE_REASON[x] ?? x}</button>
      ))}</span>
      <Err text={error} />
    </div>
  );
}

export function ApproveButton() {
  const { run, busy, error } = useAct();
  return (
    <>
      <button className="btn primary" disabled={busy} onClick={() => run("/approval", {})}>{busy ? "Menyetujui…" : "Setujui order ini"}</button>
      <Err text={error} />
    </>
  );
}
