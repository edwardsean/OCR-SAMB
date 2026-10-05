"use client";
// One order's review (Review for anomalies only, verification redesign S5): a header with the customer and one
// stamp; one card per decision, each with the one action it needs, beside the order's pages; then everything else
// under one closed fold; the approve bar at the bottom.
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { cx, g, qty, angka, rp, tgl } from "@/lib/format";
import type { OpenItem, Order, Published, QtyFix, UploadRef } from "@/lib/types";
import Cap from "@/components/Cap";
import { BatchTags } from "@/components/Batch";
import LessonStatus from "@/components/LessonStatus";
import PublishedData from "@/components/PublishedData";
import { SpreadProvider, SpreadView, useFollow, useSpot, type SpreadPage } from "@/components/Spread";
import { useReviewer } from "@/components/useReviewer";
import { useWords } from "@/components/Words";
import {
  AcceptForm, ApproveButton, CalibrateForm, CellConfirm, FieldFix, PairButtons, PairForm, QtyForm, ReviewCtx,
  SmallConfirm, useReview,
} from "./forms";

const ISSUE: Record<QtyFix["issue"], string> = {
  unread: "Jumlah diterima tidak terbaca", pack: "Angka terbaca = isi kemasan", differs: "Jumlah berbeda dari Satellite",
  unpaired: "Barang belum dikenali",
};

function Card({ n, page, wait, id, children }: { n: number; page?: number | null; wait?: boolean; id?: string; children: React.ReactNode }) {
  const ref = useFollow<HTMLDivElement>(page);
  return (
    <div className={cx("kp pd-sec", wait && "wait")} ref={ref} id={id}>
      <div className="num">{n}</div>
      <div>{children}</div>
    </div>
  );
}

function QtyRow({ x }: { x: QtyFix }) {
  const r = useReview();
  const sp = useSpot({ id: `q${x.page}:${x.i}`, page: x.page, box: x.box, label: `Baris ${x.i + 1} di Tanda Terima` });
  const [crop, setCrop] = useState(true);
  return (
    <div {...sp} className={cx("qrow pd-spot", x.pack && "pack", sp.className)}>
      <div className="qhead">
        <div className="what">{x.desc || "sebuah baris"}</div>
        <span className={`qissue ${x.issue}`}>{ISSUE[x.issue]}</span>
      </div>
      <div className="fine">Tanda Terima {r.pageName(x.page)}, baris {x.i + 1}{x.line ? ` · barang no. ${x.line} di order SAMB` : ""}</div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {x.key && crop && <img className="rowcrop" src={`/crop/${r.at(x.page).batch}/${r.at(x.page).page}/row/${encodeURIComponent(x.key)}`} alt="baris ini seperti tercetak"
                             loading="lazy" onError={() => setCrop(false)} />}
      <table className="banding">
        <thead><tr><th /><th>Dibaca dari Tanda Terima</th><th>{x.line ? "Menurut Satellite" : "Di order SAMB"}</th></tr></thead>
        <tbody>
          <tr><th>Jumlah diterima</th>
            <td>{x.qty !== null && x.qty !== "" ? <><b>{x.qty}</b>{x.pieces !== null && <> <span className="fine">= {qty(x.pieces)} pcs</span></>}</>
              : <b className="none">tidak terbaca</b>}</td>
            <td>{x.line ? <><b>{qty(x.want)} pcs</b>{x.per && x.per > 1 && x.want !== null ? <> <span className="fine">= {qty(x.want / x.per)} karton</span></> : null}</>
              : <span className="fine">belum diketahui barang yang mana: pilih di bawah</span>}</td></tr>
          <tr><th>Satuan</th><td>{x.uom || "—"}</td><td>{x.per && x.per > 1 ? `isi ${qty(x.per)} pcs per karton` : ""}</td></tr>
          {x.rejected && <tr><th>Ditolak toko</th><td /><td><b>{qty(x.rejected[0])} pcs</b> <span className="fine">{x.rejected[1]}</span></td></tr>}
        </tbody>
      </table>
      {x.issue === "pack" && <p className="qnote">Angka {x.qty} adalah isi kemasan produk (mis. 20 pada 20X200GR), bukan jumlah: AI
        membaca kolom yang salah.</p>}
      {x.key && x.line ? (
        <QtyForm page={x.page} rowKey={x.key} shown={x.qty ?? ""} want={x.want}
                 carton={x.per && x.per > 1 && x.want ? Number(g(x.want / x.per)) : null} />
      ) : !x.line ? <PairForm page={x.page} row={x.i} /> : null}
    </div>
  );
}

function OddRow({ x }: { x: NonNullable<OpenItem["odd_rows"]>[number] }) {
  const sp = useSpot({ id: `o${x.page}:${x.i}`, page: x.page, box: x.box, label: x.desc });
  return (
    <div {...sp} className={cx("row pd-spot", sp.className)}>
      <span>{x.desc} <span className="fine">· {x.qty ?? ""} {x.uom ?? ""}</span></span>
      <span><span className="amt">{angka(x.amount)}</span> <span className="why">· tidak ada di order SAMB</span></span>
    </div>
  );
}

function CheckCard({ it, n }: { it: OpenItem; n: number }) {
  const r = useReview();
  const [open, setOpen] = useState(!!it.suspect);
  const pg = it.qty_fix?.[0]?.page ?? it.fix?.[0]?.page ?? it.page;
  const good = (it.qty_lines?.length ?? 0) - (it.qty_bad?.length ?? 0);
  return (
    <Card n={n} page={pg} wait={!it.accept}>
      <h2>{it.plain || it.title}</h2>

      {it.pair && (
        <table className="rekon"><tbody>
          {it.pair.map(([label, val]) => <tr key={label}><td>{label}</td><td>{rp(val)}</td></tr>)}
          {it.gap !== null && it.gap !== undefined && <>
            <tr className="selisih"><td>Selisih</td><td>{rp(it.gap)}</td></tr>
            <tr className="batas"><td>Batas pembulatan pelanggan ini</td><td>{rp(it.allow || 5)}</td></tr>
          </>}
        </tbody></table>
      )}

      {it.qty_lines && (
        <>
          <div className="qrows">
            {it.qty_fix?.map((x) => <QtyRow key={`${x.page}:${x.i}`} x={x} />)}
            {it.qty_missing?.map((y) => (
              <div className="qrow" key={y.line_no}><div className="what">{y.desc}<span className="fine"> · barang order SAMB no. {y.line_no}</span></div>
                <div className="nums"><span>diterima menurut Satellite <b className="mono">{qty(y.satellite)} pcs</b></span><span className="flag">tidak ada di Tanda Terima</span></div></div>
            ))}
          </div>
          {good > 0 && <p className="fine">✓ {good} barang lain sesuai dengan yang diterima menurut Satellite.</p>}
        </>
      )}

      {it.suspect && (
        <div className="sebab"><div className="row"><span><strong>Angka ini tampaknya bukan jumlah yang sama.</strong> Kemungkinan AI
          membaca angka yang salah di halaman: periksa di bawah.</span></div></div>
      )}

      {it.odd_rows?.length ? (
        <>
          <div className="sebab"><p className="sebab-h">Penyebabnya:</p>
            {it.odd_rows.map((x) => <OddRow key={`${x.page}:${x.i}`} x={x} />)}
            {it.missing_lines?.map((s) => (
              <div className="row" key={s.line_no}><span className="fine">Barang order SAMB no. {s.line_no} · {s.description}</span>
                <span className="why">tidak ada di PO</span></div>
            ))}
          </div>
          <p className="fine">✓ {it.ok_rows} baris lain sesuai dengan order SAMB.</p>
        </>
      ) : it.tolakan_rows?.length ? (
        <div className="sebab"><p className="sebab-h">Ditolak toko, menurut Satellite:</p>
          {it.tolakan_rows.map((t) => <div className="row" key={t.line}><span>Barang no. {t.line}: <b>{qty(t.pcs)} pcs ditolak toko</b>{" "}
            <span className="fine">({t.why})</span></span></div>)}</div>
      ) : it.tolakan?.length && !it.qty_fix?.length ? (
        <div className="sebab"><div className="row"><span>Ditolak di toko (menurut Satellite): {it.tolakan.join("; ")}</span></div></div>
      ) : null}

      {it.held_link && <p className="sub">Jika dokumennya ada di batch ini, ia menunggu nomornya dipastikan di{" "}
        {r.upload ? <Link href={`/uploads/${r.upload}?step=cocokkan`}>langkah 3, Cocokkan ke order</Link>
          : <Link href={it.held_link}>Berkas per SOR</Link>}.</p>}

      {it.accept ? <AcceptForm check={it.key!} print={it.print ?? ""} receipt={!!it.qty_fix?.length} />
        : <p className="sub">Belum ada yang perlu dilakukan: {it.status !== "waiting" ? "AI membaca ulang dulu" : "menunggu data dari Satellite"}.</p>}

      {it.fix && it.fix.length > 0 && (
        <>
          <button type="button" className="linkbtn" onClick={() => setOpen((o) => !o)}>{it.suspect ? "Cek halamannya" : "Ada angka yang salah baca? Perbaiki"}</button>
          <div className={cx("fix", open && "open")}>
            {it.fix.map((f) => <FieldFix key={`${f.page}:${f.name}`} f={f} page={f.page!} mode="fix" />)}
          </div>
        </>
      )}
    </Card>
  );
}

export default function OrderReview({ v, pub, fixed, back }: {
  v: Order; pub: Published | null; fixed: { page: number; field: string } | null; back?: UploadRef | null;
}) {
  const w = useWords();
  const [name, setName] = useReviewer();
  const [warn, setWarn] = useState(false);
  const [lastFix, setLastFix] = useState(fixed);
  const me = useRef<HTMLInputElement>(null);
  const published = v.bundle.status === "published";
  const cal = v.calibration;
  const nTodo = v.open_items.filter((i) => i.kind !== "wait").length + (cal?.asks.length ?? 0);
  const cust = (v.so?.customer_name ?? "").split(" ").slice(0, 2).join(" ");

  const ctx = useMemo(() => ({
    batch: v.batch, sor: v.sor, name, lines: v.lines, acceptReasons: v.accept_reasons, noneReasons: v.none_reasons,
    at(page: number) { const x = v.where?.[String(page)]; return x ? { batch: x.batch, page: x.page } : { batch: v.batch, page }; },
    pageName(page: number) {
      const x = v.where?.[String(page)];
      return x ? `hal. ${x.page}${v.multi ? ` · ${x.scan}` : ""}` : `hal. ${page}`;
    },
    needName() { setWarn(true); me.current?.scrollIntoView({ block: "center" }); me.current?.focus(); },
    onFixed(page: number, field: string) { setLastFix({ page, field }); },
    upload: back?.id ?? null,
  }), [v, name, back]);

  const pages: SpreadPage[] = v.strip.filter((s) => s.img).map((s) => ({
    n: s.page, img: s.img!, alt: `${w.DOC[s.type] ?? s.kind}, ${ctx.pageName(s.page)}`, title: s.flag ? "ada yang perlu dicek di halaman ini" : undefined,
    tab: <>{w.DOC_SHORT[s.type] ?? s.kind}<small>{ctx.pageName(s.page)}</small>{s.flag && <span className="tab-flag">!</span>}</>,
  }));
  let k = 0;

  return (
    <ReviewCtx.Provider value={ctx}>
      <div className="rv">
        <p className="crumbs">{back ? <><Link href="/">Batch</Link> / <Link href={`/uploads/${back.id}?step=periksa`}>{back.code}</Link></>
          : <Link href={`/review?batch=${v.batch}`}>Periksa order</Link>} / {v.sor}</p>
        <div className="rv-top">
          <div>
            <h1>{v.so?.customer_name || "Pelanggan belum diketahui"}</h1>
            <p className="rv-sor">{v.sor}</p>
            {v.uploads?.length > 0 && <p className="batchhead">Dari batch <BatchTags list={v.uploads} full /></p>}
            <p className="rv-status">
              {published ? <><Cap status="published" big>Terkirim</Cap> <span>Terkirim ke Satellite</span></>
                : v.can_approve ? <><Cap status="auto_ok" big>Sesuai</Cap> <span>Siap disetujui</span></>
                : nTodo ? <><Cap status="needs_review" big>Perlu dicek</Cap> <span>Perlu Anda: {nTodo} keputusan</span></>
                : <><Cap status="grouping" big>Menunggu</Cap> <span>Menunggu sistem, belum ada yang perlu dilakukan</span></>}
            </p>
          </div>
          {!published && (
            <div className="rv-me-wrap">
              <label className="rv-me">Nama Anda <input ref={me} placeholder="tulis nama Anda" autoComplete="name" value={name}
                style={warn && !name.trim() ? { outline: "2px solid var(--bad)" } : undefined}
                onChange={(e) => { setName(e.target.value); setWarn(false); }} /></label>
              {warn && !name.trim() && <div className="me-warn">Tulis nama Anda dulu: setiap keputusan dicatat atas nama Anda.</div>}
            </div>
          )}
        </div>
        {lastFix && (
          <div className="fx-saved">✓ Jawaban Anda untuk {ctx.pageName(lastFix.page)} tersimpan. Halaman dan order ini sudah dicek ulang.
            <LessonStatus batch={ctx.at(lastFix.page).batch} page={ctx.at(lastFix.page).page} field={lastFix.field} />
          </div>
        )}

        {published ? (
          <>
            <div className="rv-strip">
              {v.strip.map((s) => (
                <Link key={s.page} className={cx("rv-thumb", s.flag && "flag")} href={`/batches/${ctx.at(s.page).batch}/pages/${ctx.at(s.page).page}`} title={`Buka ${ctx.pageName(s.page)}`}>
                  <div className="img" style={{ backgroundImage: `url('${s.thumb ?? ""}')` }}>{s.flag && <span className="dot">!</span>}</div>
                  <b>{w.DOC_SHORT[s.type] ?? s.kind}</b>{ctx.pageName(s.page)}
                </Link>
              ))}
            </div>
            <div className="rv-steps"><div className="kp kp-1"><div style={{ minWidth: 0 }}>
              <h2>Terkirim ke Satellite{v.bundle.published_at ? ` pada ${tgl(v.bundle.published_at)}` : ""}</h2>
              <p className="sub">Data sudah ditulis ke Satellite, beserta satu PDF untuk order ini. Order yang sudah terkirim tidak dicek lagi.</p>
              <div className="acts"><a className="btn primary" href={`/documents/${v.sor}.pdf`}>Buka {v.sor}.pdf</a></div>
              <div className="passed">{v.bundle.documents.map((d, i) => (
                <span className="chip" key={i}>{w.DOC_SHORT[d.type] ?? d.type} · {d.lines} baris · {d.confidence !== null ? Math.round(d.confidence * 100) : "—"}% terverifikasi</span>
              ))}</div>
              <details className="pub-rows" open><summary><b>Data yang ditulis ke Satellite</b>{" "}
                <Link className="small" href={`/published?sor=${v.sor}#${v.sor}`}>· lihat di Data terkirim</Link></summary>
                {pub ? <PublishedData pv={pub} /> : <p className="muted small">Order ini belum terkirim ke Satellite.</p>}
              </details>
            </div></div></div>
          </>
        ) : (
          <SpreadProvider pages={pages}>
            <div className="pd rv-pd">
              <div className="pd-grid rv-grid">
                <div className="rv-left">
                  <div className="rv-steps">
                    {v.open_items.map((it) => {
                      k += 1;
                      if (it.kind === "check") return <CheckCard key={`c${it.key}`} it={it} n={k} />;
                      if (it.kind === "page") return (
                        <Card key={`p${it.page}`} n={k} page={it.page}>
                          <h2>{it.page !== undefined ? ctx.pageName(it.page).replace(/^hal\./, "Halaman") : "Halaman"}: pastikan {it.fields?.map((f) => f.label).join(", ")}</h2>
                          <p className="sub">AI sudah membacanya, tetapi belum ada yang bisa memastikannya. Pilih di halaman, atau ketik seperti tercetak.</p>
                          {it.fields?.map((f) => <FieldFix key={f.name} f={f} page={it.page!} mode="page" />)}
                        </Card>
                      );
                      if (it.kind === "label") return (
                        <Card key={`l${it.page}`} n={k} page={it.page}>
                          <h2>{it.title}</h2>
                          <div className="acts"><Link className="btn primary" href={`/label?batch=${it.page !== undefined ? ctx.at(it.page).batch : v.batch}`}>Buka layar Jenis halaman</Link></div>
                        </Card>
                      );
                      return (
                        <Card key={`w${it.page}`} n={k} page={it.page} wait>
                          <h2>{it.title}</h2>
                          <p className="sub">Tidak perlu tindakan: berlanjut otomatis.</p>
                        </Card>
                      );
                    })}

                    {cal?.asks.map((a) => {
                      k += 1;
                      return (
                        <Card key={a.what} n={k} id="calibration">
                          {a.what === "allowance" ? (
                            <>
                              <h2>Pelanggan baru ({cust}): berapa selisih pembulatan yang wajar?</h2>
                              <p className="sub">Hanya ditanyakan sekali untuk pelanggan ini. Selisih berikutnya sampai batas ini lolos tanpa perlu Anda cek.
                                {cal.suggest === null && <><br />Selisih order ini lebih besar dari pembulatan: putuskan dulu yang di atas, lalu pilih batasnya.</>}</p>
                              <CalibrateForm chain={cal.chain} name={cal.name}>
                                {(send, busy) => cal.steps.map((s) => (
                                  <button key={s} type="button" disabled={busy} className={cx("btn", s === cal.suggest && "suggest")}
                                          onClick={() => send({ allowance: String(s) })}>Rp {s}</button>
                                ))}
                              </CalibrateForm>
                            </>
                          ) : (
                            <>
                              <h2>Jika ada barang ditolak, apa yang tertulis di Tanda Terima {cust}?</h2>
                              <p className="sub">Satellite mencatat ada barang yang ditolak toko pada order ini. Hanya ditanyakan sekali untuk pelanggan ini.</p>
                              <CalibrateForm chain={cal.chain} name={cal.name}>
                                {(send, busy) => <>
                                  <button type="button" disabled={busy} className={cx("btn", a.suggest === "received" && "suggest")}
                                          onClick={() => send({ receipt_shows: "received" })}>Hanya yang diterima</button>
                                  <button type="button" disabled={busy} className="btn" onClick={() => send({ receipt_shows: "ordered" })}>Seluruh pesanan</button>
                                </>}
                              </CalibrateForm>
                            </>
                          )}
                        </Card>
                      );
                    })}

                    {!v.open_items.length && !cal && (
                      <div className="kp kp-1"><div>
                        {v.can_approve ? <><h2>Semua sudah sesuai.</h2><p className="sub">Setujui di bawah, lalu order ini bisa dikirim ke Satellite.</p></>
                          : <><h2>Belum ada yang perlu dilakukan</h2><p className="sub">{v.left.join(" ")}</p></>}
                      </div></div>
                    )}
                  </div>
                  {v.passed.length > 0 && <p className="passed"><b>Sudah sesuai:</b> {v.passed.join(", ")}.</p>}
                </div>
                <SpreadView pages={pages} label="Halaman asli order ini" hint="Arahkan kursor ke sebuah baris untuk melihat letaknya di halaman" />
              </div>
            </div>
          </SpreadProvider>
        )}

        <AllValues v={v} name={ctx.pageName} at={ctx.at} />
      </div>

      {!published && (
        <div className="rv-bar">
          {v.can_approve ? <><span className="left">✓ Tidak ada yang tersisa.</span><ApproveButton /></> : (
            <>
              <span className="left">{nTodo} keputusan lagi</span>
              <button className="btn primary" disabled style={{ opacity: 0.45, cursor: "not-allowed" }} title="Selesaikan dulu semua keputusan di atas">Setujui order ini</button>
            </>
          )}
        </div>
      )}
    </ReviewCtx.Provider>
  );
}

/** Everything else about the order, never needed to finish it: every check, every value read, each row's pairing. */
function AllValues({ v, name, at }: { v: Order; name: (p: number) => string; at: (p: number) => { batch: string; page: number } }) {
  const w = useWords();
  const tone = (s: string) => (["pass", "accepted"].includes(s) ? "ok" : s === "fail" ? "bad" : s === "unknown" ? "warn" : "");
  return (
    <details className="rv-all" id="all"><summary>Tampilkan semua nilai order ini (tidak perlu untuk menyelesaikan)</summary>
      <section className="rv-box" id="checks">
        <h3>Semua cek</h3>
        <table className="grid small"><thead><tr><th>Cek</th><th>Hasil</th><th>Alasan <span className="muted">(teknis)</span></th></tr></thead>
          <tbody>{Object.entries(v.checks).map(([key, c]) => (
            <tr key={key} className={tone(c.status)}><td>{v.labels[key]}</td><td>{w.CHECK_STATUS[c.status] ?? c.status}</td><td>{c.why}</td></tr>
          ))}</tbody></table>
      </section>
      {v.documents.map((d) => (
        <section className="rv-box" id={`p${d.page}`} key={d.page}>
          <h3>{name(d.page).replace(/^hal\./, "Halaman")}{d.pages.length > 1 ? `–${at(d.pages[d.pages.length - 1]).page}` : ""} · {d.kind} <span className="muted">({w.OUTCOME[d.outcome ?? ""] ?? d.outcome})</span>{" "}
            <Link className="small" href={`/batches/${at(d.page).batch}/pages/${at(d.page).page}`}>buka halaman</Link></h3>
          {d.head.map((f) => <SmallConfirm key={f.name} f={f} page={d.page} />)}
          {d.kept.length > 0 && (
            <details><summary className="muted">Disimpan seperti terbaca ({d.kept.length}): tidak menahan order ini</summary>
              {d.kept.map((f) => <SmallConfirm key={f.name} f={f} page={d.page} />)}</details>
          )}
          {d.rows.length > 0 && (
            <table className="grid small rv-lines">
              <thead><tr><th>Baris</th><th>Terbaca</th><th>Pastikan sel</th><th>Barang order SAMB</th></tr></thead>
              <tbody>{d.rows.map((r) => (
                <tr key={r.i} className={r.bonus ? "" : r.match.status === "matched" && !r.todo.length ? "ok" : "warn"}>
                  <td className="mono">{r.i + 1}</td>
                  <td>{r.row.product_description || r.row.material_description || r.row.nama_produk || ""}
                    <div className="mono">{d.type === "FP" ? `${r.row.qty_crt ?? ""} / ${r.row.qty_pcs ?? ""}`
                      : <>{r.row.qty} {r.row.uom ?? ""}{r.row.unit_price ? ` · ${r.row.unit_price}` : ""}</>}</div></td>
                  <td>{r.bonus ? <span className="muted">baris gratis (bonus)</span> : !r.todo.length ? <span className="muted">—</span>
                    : r.todo.map((x) => <CellConfirm key={x.col} page={d.page} rowKey={r.key} col={x.col} label={x.label} read={x.read} hints={x.hints} />)}</td>
                  <td>{r.bonus ? <span className="muted">tidak ada</span>
                    : r.match.status === "matched" && r.line ? <><span className="mono">no. {r.line.line_no}</span> {r.line.description}</>
                    : d.type !== "FP" ? <PairButtons page={d.page} row={r.i} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>
      ))}
    </details>
  );
}
