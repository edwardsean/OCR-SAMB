"use client";
// What publishing wrote to Satellite for one SOR (/api/v1/published/<sor>), for Data terkirim and a published order's
// page. Left: each document's values as Satellite stores them, read like a form, each with what backed it. Right: the
// scan page it came from, following the document being read and boxing a value pointed at. The database tables
// themselves, every column as stored, are under "teknis".
import { cx } from "@/lib/format";
import type { PubDoc, PubField, PubTable, Published } from "@/lib/types";
import { SpreadProvider, SpreadView, useFollow, useSpot, type SpreadPage } from "./Spread";
import { useWords } from "./Words";

const NUM = ["amount", "qty", "pct"];

function DbTable({ tb }: { tb: PubTable }) {
  return (
    <div className="pt">
      <div className="pt-head"><code>{tb.name}</code> <span className="muted small">{tb.rows.length} baris</span></div>
      {tb.rows.length ? (
        <div className="pt-scroll"><table className="grid small pt-table">
          <thead><tr>{tb.cols.map(([k, label]) => <th key={k}><code>{k}</code><small>{label}</small></th>)}</tr></thead>
          <tbody>{tb.rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => (
              <td key={j} className={cx(NUM.includes(tb.cols[j][2] ?? "") && "pt-num")}>
                {v === null ? <span className="null" title="NULL: kolom ini kosong di database">—</span> : v}</td>
            ))}</tr>
          ))}</tbody>
        </table></div>
      ) : <p className="small muted pt-none">tidak ada baris: dokumen ini tidak ada di order ini</p>}
    </div>
  );
}

function Field({ f, doc }: { f: PubField; doc: PubDoc }) {
  const w = useWords();
  const sp = useSpot({ id: `${doc.id}:${f.name}`, page: f.page, box: f.box, approx: f.approx, label: f.label });
  return (
    <div {...sp} className={cx("pd-f", f.state, sp.className)} tabIndex={0}>
      <dt>{f.label}</dt>
      <dd>{f.value ?? "—"}</dd>
      <span className={`st ${f.state}`}>{w.PUB_STATE[f.state]}</span>
    </div>
  );
}

function Row({ r, doc }: { r: PubDoc["rows"][number]; doc: PubDoc }) {
  const sp = useSpot({ id: `${doc.id}:row${r.line}`, page: r.page, box: r.box, label: `Baris ${r.line}` });
  return (
    <tr {...sp} className={cx("pd-row", sp.className)} tabIndex={0}>
      <td className="muted">{r.line}</td>
      {r.cells.map((v, k) => <td key={k} className={cx(["amount", "qty"].includes(doc.cols[k][2] ?? "") && "pt-num")}>{v ?? "—"}</td>)}
    </tr>
  );
}

function Doc({ d }: { d: PubDoc }) {
  const ref = useFollow<HTMLDivElement>(d.pages[0]?.n);
  return (
    <div className="pd-doc" ref={ref}>
      <header className="pd-dh">
        <div><h3>{d.name}</h3>
          <p className="muted small">{d.pages.length ? `Halaman ${d.pages.map((p) => p.n).join(", ")} di scan, ` : ""}halaman{" "}
            {d.page_ref.join(", ")} di PDF.{d.linked_by ? ` Masuk ke order ini ${d.linked_by}.` : ""}</p></div>
        <span className={cx("pd-score", d.to_check ? "warn" : "ok")} title="nilai yang didukung cetakan, data Satellite, atau orang">
          {d.backed} dari {d.filled} nilai pasti</span>
      </header>
      <dl className="pd-fields">{d.fields.map((f) => <Field key={f.name} f={f} doc={d} />)}</dl>
      {d.rows.length > 0 && (
        <div className="pd-lines">
          <h4>Barang <span className="muted">· {d.rows.length} baris</span></h4>
          <div className="pd-lscroll"><table className="grid small">
            <thead><tr><th>No</th>{d.cols.map(([k, label, kind]) => <th key={k} className={cx(["amount", "qty"].includes(kind ?? "") && "pt-num")}>{label}</th>)}</tr></thead>
            <tbody>{d.rows.map((r) => <Row key={r.line} r={r} doc={d} />)}</tbody>
          </table></div>
        </div>
      )}
    </div>
  );
}

export default function PublishedData({ pv }: { pv: Published }) {
  const w = useWords();
  const toCheck = pv.docs.reduce((n, d) => n + d.to_check, 0);
  const pages: SpreadPage[] = pv.docs.flatMap((d) => d.pages.map((p, k) => ({
    n: p.n, img: p.img, alt: `${d.name}, halaman ${p.n} di scan`,
    tab: <><span className={`tchip t-${d.type}`}>{w.DOC_SHORT[d.type] ?? d.type}</span>{d.pages.length > 1 ? ` (${k + 1})` : ""}<small>hal. {p.n}</small></>,
  })));
  return (
    <SpreadProvider pages={pages}>
      <div className="pd">
        {toCheck ? <p className="pd-help pd-warn">{toCheck} nilai belum didukung cetakan (<span className="st ai">cek di kertas</span>): cocokkan dengan halamannya.</p>
          : <p className="pd-help pd-ok">Semua nilai didukung cetakan, data Satellite, atau orang.</p>}
        <div className="pd-grid">
          <div className="pd-data">
            {pv.docs.map((d) => <Doc key={d.id + d.type} d={d} />)}
            <details className="tech pd-db"><summary>Tabel database Satellite <small>(teknis): setiap kolom persis seperti tersimpan,
              judul kolom = nama kolom di database. <span className="null">—</span> = kosong (NULL).</small></summary>
              <div className="pubdata">
                <h3>PDF order</h3>
                <DbTable tb={pv.record} />
                {pv.tables.map((tb) => !tb.lines ? (
                  <div key={tb.name}><h3>{w.DOC[tb.type!] ?? tb.type}</h3><DbTable tb={tb} /></div>
                ) : pv.counts[tb.type!]?.[0] ? <DbTable key={tb.name} tb={tb} /> : null)}
              </div>
            </details>
          </div>
          <SpreadView pages={pages} label="Halaman asli" hint="Arahkan kursor ke sebuah nilai untuk menandainya di halaman">
            {!pages.length && <p className="muted small pd-nopage">Gambar halamannya tidak ada di sistem ini.</p>}
          </SpreadView>
        </div>
      </div>
    </SpreadProvider>
  );
}
