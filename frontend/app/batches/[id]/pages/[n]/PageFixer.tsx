"use client";
// The page viewer (read-then-map, Stage 2a): the paper with a box on every value read, the type's fields beside it.
// Fix = click the value on the paper. A word, number or table cell of the AI OCR's copy is clickable where Tesseract
// (or its closer look, worker/boxes.py) places it on the print; a word placed nowhere has no box (never a guess).
// Hovering shows what a click takes; dragging across words takes them all, in reading order, as the copy writes them
// ("15:16:50"); dragging around text with no box reads it from the paper. The page is fixed at once and the
// correction kept as an example for the knowledge.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { api, why } from "@/lib/client";
import { cx, nilai } from "@/lib/format";
import type { Box, FixView, Unit } from "@/lib/types";
import { useWords } from "@/components/Words";
import { useReviewer } from "@/components/useReviewer";
import LessonStatus from "@/components/LessonStatus";

type Target = { field: string; label: string; desc: string; key: boolean; shown: string; row?: string; hl: string; copied?: string[] };
type Rect = [number, number, number, number];           // y0, x0, y1, x1 on 0-1000, as drawn
type Note = { cls: string; text: string } | null;

const flat = (s: string | null | undefined) => (s || "").toUpperCase().replace(/[^0-9A-Z]/g, "");

function drawn(u: Unit): Rect {                          // a word's box, at least 8 units each way so it can be clicked
  const w = Math.max(u.box[3] - u.box[1], 8), h = Math.max(u.box[2] - u.box[0], 8);
  const x = (u.box[1] + u.box[3] - w) / 2, y = (u.box[0] + u.box[2] - h) / 2;
  return [y, x, y + h, x + w];
}

function union(rs: Rect[]): Rect {
  return [Math.min(...rs.map((r) => r[0])), Math.min(...rs.map((r) => r[1])), Math.max(...rs.map((r) => r[2])),
          Math.max(...rs.map((r) => r[3]))].map(Math.round) as Rect;
}

function inOrder(us: Unit[]): Unit[] {                   // printed lines top to bottom, each left to right
  const it = us.map((u) => { const b = drawn(u); return { u, cy: (b[0] + b[2]) / 2, h: b[2] - b[0], x: b[1] }; });
  it.sort((a, b) => a.cy - b.cy);
  const rows: { cy: number; h: number; a: typeof it }[] = [];
  for (const a of it) {
    const l = rows[rows.length - 1];
    if (l && Math.abs(a.cy - l.cy) <= 0.6 * Math.max(a.h, l.h)) l.a.push(a); else rows.push({ cy: a.cy, h: a.h, a: [a] });
  }
  return rows.flatMap((l) => l.a.sort((p, q) => p.x - q.x).map((a) => a.u));
}

function SvgRect({ b, className, f }: { b: Box | Rect; className: string; f?: string }) {
  if (!b) return null;
  return <rect className={className} data-f={f} x={b[1]} y={b[0]} width={b[3] - b[1]} height={b[2] - b[0]} />;
}

export default function PageFixer({ batch, page, fix, start, back, fixed }: {
  batch: string; page: number; fix: FixView; start?: string; back?: string; fixed?: string;
}) {
  const w = useWords();
  const router = useRouter();
  const [by, setBy] = useReviewer();
  const [editName, setEditName] = useState(false);
  const sheet = useRef<HTMLDivElement>(null), view = useRef<HTMLDivElement>(null), valueIn = useRef<HTMLInputElement>(null);
  const zoomRef = useRef(1);
  const [zoomLabel, setZoomLabel] = useState(100);
  const [target, setTarget] = useState<Target | null>(null);
  const [value, setValue] = useState("");
  const [region, setRegion] = useState("");
  const [picked, setPicked] = useState<Unit[]>([]);
  const [live, setLive] = useState<Set<string>>(new Set());
  const [big, setBig] = useState<string | null>(null);
  const [tess, setTess] = useState<Note>(null);
  const [keyNote, setKeyNote] = useState<Note>(null);
  const [stepTwo, setStepTwo] = useState(false);
  const [chip, setChip] = useState<number | null>(null);
  const [mark, setMark] = useState<Rect>([0, 0, 0, 0]);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [hot, setHot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(fixed ?? null);
  const [rowsOpen, setRowsOpen] = useState(!!start?.startsWith("lines["));
  const drag = useRef<{ a: [number, number]; moved: boolean; u: Unit | null; shift: boolean } | null>(null);

  const units = fix.units;
  const byId = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  const boxed = useMemo(() => new Set(units.map((u) => `${u.block}:${u.i}`)), [units]);
  const pickedIds = useMemo(() => new Set(picked.map((u) => u.id)), [picked]);
  const fieldBox = useMemo(() => {
    const m = new Map<string, Box>();
    fix.rows.forEach((r) => m.set(`row${r.i}`, r.box));
    fix.fields.forEach((f) => m.set(f.name, f.box));
    return m;
  }, [fix]);

  // every value a person can fix: the header fields, then each table cell
  const targets = useMemo(() => {
    const m = new Map<string, Target>();
    for (const f of fix.fields) {
      m.set(f.name, { field: f.name, label: f.label, desc: f.desc ?? "", key: f.role === "keys" || f.name === "nomor_cpo",
                      shown: f.value ?? "", hl: f.name });
    }
    for (const r of fix.rows) {
      for (const [col, val] of r.cells) {
        const field = `lines[${r.key}].${col}`;
        m.set(field, { field, label: `Baris ${r.i} · ${w.COL[col] ?? col.replace(/_/g, " ")}`, desc: "", key: false,
                       shown: val ?? "", row: r.key, hl: `row${r.i}`, copied: r.copied });
      }
    }
    return m;
  }, [fix, w]);

  // ---------------------------------------------------------------------------------------------- zoom
  const setZoom = useCallback((z: number, cx?: number, cy?: number) => {
    const s = sheet.current, v = view.current;
    if (!s || !v) return;
    zoomRef.current = Math.max(1, Math.min(6, z));
    s.style.width = `${zoomRef.current * 100}%`;
    setZoomLabel(Math.round(zoomRef.current * 100));
    if (cx !== undefined && cy !== undefined) {           // keep this point (0-1000) in the middle
      v.scrollLeft = (cx / 1000) * s.offsetWidth - v.clientWidth / 2;
      v.scrollTop = (cy / 1000) * s.offsetHeight - v.clientHeight / 2;
    }
  }, []);
  const centre = (): [number, number] => {
    const s = sheet.current!, v = view.current!;
    return [((v.scrollLeft + v.clientWidth / 2) / s.offsetWidth) * 1000, ((v.scrollTop + v.clientHeight / 2) / s.offsetHeight) * 1000];
  };
  useEffect(() => {                                       // Ctrl/⌘ + scroll zooms where the pointer is
    const v = view.current;
    if (!v) return;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const r = sheet.current!.getBoundingClientRect();
      setZoom(zoomRef.current * (e.deltaY < 0 ? 1.2 : 1 / 1.2), ((e.clientX - r.left) / r.width) * 1000, ((e.clientY - r.top) / r.height) * 1000);
    };
    v.addEventListener("wheel", wheel, { passive: false });
    return () => v.removeEventListener("wheel", wheel);
  }, [setZoom]);

  // ---------------------------------------------------------------------------------------------- picking
  const said = useCallback((us: Unit[]) => {
    type G = { t: string } | { b: string; us: Unit[] };
    const groups: G[] = [], byBlock: Record<string, { b: string; us: Unit[] }> = {};
    for (const u of inOrder(us)) {
      const k = u.block;
      if (!k || fix.lines[k] === undefined) { groups.push({ t: u.text }); continue; }
      if (!byBlock[k]) { byBlock[k] = { b: k, us: [] }; groups.push(byBlock[k]); }
      byBlock[k].us.push(u);
    }
    return groups.map((g) => {
      if ("t" in g) return g.t;
      const runs: { s: number; e: number; i: number }[] = [];
      let cur: { s: number; e: number; i: number } | null = null;
      for (const u of [...g.us].sort((p, q) => p.i - q.i)) {
        let gap = true;
        if (cur) { gap = false; for (let k = cur.i + 1; k < u.i; k++) if (boxed.has(`${g.b}:${k}`)) { gap = true; break; } }
        if (cur && !gap) { cur.e = u.e; cur.i = u.i; } else { cur = { s: u.s, e: u.e, i: u.i }; runs.push(cur); }
      }
      return runs.map((x) => fix.lines[g.b].slice(x.s, x.e).replace(/\s+/g, " ").trim()).join(" ");
    }).join(" ");
  }, [fix.lines, boxed]);

  const tessOf = (us: Unit[]) => inOrder(us).map((u) => u.tess).filter((t, k, a) => t && t !== a[k - 1]).join(" ");

  function took(text: string, t: string, reg: Rect | number[], inkOnly: boolean, note?: Note) {
    setValue(text); setRegion(reg.join(",")); setBig(text); setStepTwo(true); setError(null);
    if (note !== undefined) setTess(note);
    else if (inkOnly) setTess({ cls: "muted", text: `Pembaca teks (Tesseract) tidak bisa membaca bagian ini; salinan AI OCR menulis ${text}: cocokkan dengan kertas.` });
    else if (!t) setTess({ cls: "muted", text: "Pembaca teks (Tesseract) tidak membaca apa pun di sini: cocokkan nilainya dengan kertas." });
    else if (flat(t) === flat(text) || t.split(/[\s|]+/).some((x) => flat(x) && flat(x) === flat(text)))
      setTess({ cls: "agree", text: `✓ Pembaca teks (Tesseract) membaca sama: ${t}` });
    else setTess({ cls: "differ", text: `⚠ Pembaca teks (Tesseract) membaca ${t}: lihat kertasnya, dan perbaiki nilainya bila perlu.` });
    setTimeout(() => valueIn.current?.focus(), 0);
  }

  function pick(us: Unit[]) {
    setPicked(us);
    setLive(new Set());
    if (us.length) took(said(us), tessOf(us), union(us.map(drawn)), us.every((u) => u.match === "ink"));
  }

  function begin(t: Target) {
    setTarget(t); setPicked([]); setLive(new Set()); setValue(""); setRegion(""); setBig(null); setTess(null);
    setKeyNote(null); setStepTwo(false); setChip(null); setMark([0, 0, 0, 0]); setError(null);
    const b = fieldBox.get(t.hl);
    if (b) setZoom(2.5, (b[1] + b[3]) / 2, (b[0] + b[2]) / 2); else setZoom(2.5, 500, 250);
  }
  function cancel() {
    setTarget(null); setPicked([]); setLive(new Set()); setMark([0, 0, 0, 0]); setTip(null);
  }
  useEffect(() => {                                       // ?fix=<field>: start fixing it at once
    const t = start ? targets.get(start) : undefined;
    if (t) { document.getElementById("fixer")?.scrollIntoView(); begin(t); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && target) cancel(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [target]);

  // a value that links the page to its order: does Satellite know it? (a warning, never a refusal)
  useEffect(() => {
    if (!target?.key || !value.trim()) { setKeyNote(null); return; }
    const t = setTimeout(async () => {
      const a = await api.get<{ known: boolean | null; says?: string }>(
        `/keycheck?field=${encodeURIComponent(target.field)}&value=${encodeURIComponent(value)}`);
      if (!a.ok || a.data.known === null || a.data.known === undefined) { setKeyNote(null); return; }
      setKeyNote({ cls: a.data.known ? "agree" : "differ", text: (a.data.known ? "✓ Ada di Satellite: " : "⚠ ") + a.data.says });
      setBig((b) => b ?? value);
    }, 250);
    return () => clearTimeout(t);
  }, [target, value]);

  // ---------------------------------------------------------------------------------------------- the pointer
  function at(e: { clientX: number; clientY: number }): [number, number] {
    const r = sheet.current!.getBoundingClientRect();
    return [Math.max(0, Math.min(1000, ((e.clientX - r.left) * 1000) / r.width)), Math.max(0, Math.min(1000, ((e.clientY - r.top) * 1000) / r.height))];
  }
  function inside(a: [number, number], b: [number, number]) {
    const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]), x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
    return units.filter((u) => {
      const q = drawn(u), wd = Math.min(x1, q[3]) - Math.max(x0, q[1]), ht = Math.min(y1, q[2]) - Math.max(y0, q[0]);
      return wd > 0 && ht > 0 && wd * ht >= 0.4 * (q[3] - q[1]) * (q[2] - q[0]);
    });
  }
  const unitAt = (el: EventTarget) => {
    const r = (el as Element).closest?.("rect.u") as SVGRectElement | null;
    return r ? byId.get(r.dataset.u!) ?? null : null;
  };
  const box2 = (a: [number, number], b: [number, number]): Rect =>
    [Math.min(a[1], b[1]), Math.min(a[0], b[0]), Math.max(a[1], b[1]), Math.max(a[0], b[0])];

  function down(e: RPointerEvent<HTMLDivElement>) {
    if (!target || e.button !== 0) return;
    e.preventDefault();
    drag.current = { a: at(e), moved: false, u: unitAt(e.target), shift: e.shiftKey };
    sheet.current!.setPointerCapture(e.pointerId);
  }
  function move(e: RPointerEvent<HTMLDivElement>) {
    const d = drag.current;
    if (!d) {
      const u = unitAt(e.target);
      setTip(u ? { x: e.clientX + 14, y: e.clientY + 18, text: u.text } : null);
      return;
    }
    const b = at(e), s = sheet.current!;
    if (!d.moved && (Math.abs(b[0] - d.a[0]) * s.offsetWidth) / 1000 < 5 && (Math.abs(b[1] - d.a[1]) * s.offsetHeight) / 1000 < 5) return;
    d.moved = true;
    setMark(box2(d.a, b));
    const us = inside(d.a, b);
    setLive(new Set(us.map((u) => u.id)));
    setTip({ x: e.clientX + 14, y: e.clientY + 18, text: us.length ? said(us) : "tidak ada kata di sini: nilainya akan dibaca dari kertas" });
  }
  async function up(e: RPointerEvent<HTMLDivElement>) {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    setTip(null);
    if (!d.moved) {
      if (!d.u) return;
      setChip(null);
      if (d.shift && picked.length) {                     // shift-click adds a word, or takes it out
        const next = pickedIds.has(d.u.id) ? picked.filter((u) => u !== d.u) : [...picked, d.u];
        pick(next);
        if (!next.length) setMark([0, 0, 0, 0]);
      } else { setMark([0, 0, 0, 0]); pick([d.u]); }
      return;
    }
    const end = at(e), us = inside(d.a, end);
    setLive(new Set());
    if (us.length) { setMark([0, 0, 0, 0]); setChip(null); pick(us); return; }
    const box = box2(d.a, end).map(Math.round) as Rect;
    if (box[2] - box[0] < 3 || box[3] - box[1] < 3) return;
    const a = await api.get<{ suggest?: string; words?: string }>(`/scans/${batch}/pages/${page}/region?box=${box.join(",")}`);
    setPicked([]);
    took(a.ok ? a.data.suggest || "" : "", a.ok ? a.data.words || "" : "", box, false);
  }

  // ---------------------------------------------------------------------------------------------- saving
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!target || !value.trim() || !by.trim()) return;
    setBusy(true); setError(null);
    const a = await api.post(`/scans/${batch}/pages/${page}/fixes`, {
      field: target.field, value, by, region, shown: target.shown, row_key: target.row ?? "",
    });
    setBusy(false);
    if (!a.ok) { setError(why(a)); return; }
    if (back) {
      router.push(`${back}${back.includes("?") ? "&" : "?"}fpage=${page}&fixed=${encodeURIComponent(target.field)}`);
      return;
    }
    setSaved(target.field);
    cancel();
    router.refresh();
  }
  function notPrinted() {
    setValue("(not printed)"); setRegion(""); setBig("Tidak tercetak di halaman ini"); setTess(null); setStepTwo(true);
  }

  const named = by.trim() && !editName;
  const keep = target?.hl;
  const rectCls = (base: string, f: string) => cx(base, hot === f && "hot", keep === f && "hot-keep");

  return (
    <section className="fixer" id="fixer">
      <h2>Halaman dan isiannya</h2>
      {saved && (
        <div className="fx-saved">✓ Tersimpan. Halaman sudah dicek ulang dengan jawaban Anda.
          <LessonStatus batch={batch} page={page} field={saved} />
        </div>
      )}
      <div className="fx-grid">
        <div className="fx-paper">
          <div className="fx-zoom">
            <button type="button" className="btn tiny" id="fx-zout" title="Perkecil (Ctrl + scroll)" onClick={() => { const c = centre(); setZoom(zoomRef.current / 1.5, ...c); }}>−</button>
            <span className="small mono" id="fx-zlevel">{zoomLabel}%</span>
            <button type="button" className="btn tiny" id="fx-zin" title="Perbesar (Ctrl + scroll)" onClick={() => { const c = centre(); setZoom(zoomRef.current * 1.5, ...c); }}>+</button>
            <button type="button" className="btn tiny" id="fx-zfit" onClick={() => setZoom(1, 500, 0)}>Pas layar</button>
          </div>
          <div className="fx-view" ref={view}>
            <div className={cx("fx-sheet", target && "picking")} id="fx-sheet" ref={sheet} onPointerDown={down} onPointerMove={move} onPointerUp={up}
                 onPointerLeave={() => { if (!drag.current) setTip(null); }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/img/${fix.image}`} alt={`halaman ${page}`} draggable={false} />
              <svg viewBox="0 0 1000 1000" preserveAspectRatio="none">
                {fix.rows.map((r) => <SvgRect key={`r${r.i}`} b={r.box} f={`row${r.i}`} className={rectCls("fx-row", `row${r.i}`)} />)}
                {fix.fields.map((f) => <SvgRect key={f.name} b={f.box} f={f.name} className={rectCls(cx("fx-f", f.person ? "person" : f.verdict), f.name)} />)}
                {units.map((u) => {
                  const r = drawn(u);
                  return <rect key={u.id} data-u={u.id} data-t={u.text} data-tess={u.tess ?? ""} data-b={u.block ?? ""} data-i={u.i} className={cx("u", pickedIds.has(u.id) && "picked", live.has(u.id) && "live")}
                               x={r[1]} y={r[0]} width={r[3] - r[1]} height={r[2] - r[0]} />;
                })}
                <rect className="fx-mark" x={mark[1]} y={mark[0]} width={mark[3] - mark[1]} height={mark[2] - mark[0]} />
              </svg>
            </div>
          </div>
          {tip && <div className="fx-tip" id="fx-tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>}
        </div>

        <div className="fx-side">
          <div className="fx-meta"><span className={`tchip t-${fix.type}`}>{w.DOC_SHORT[fix.type] ?? fix.type}</span>
            {fix.customer ? <span>{fix.customer} <span className="muted small">(dari ordernya)</span></span>
              : <span className="muted small">pelanggan belum diketahui: halaman ini belum terhubung ke order</span>}</div>

          {target && (
            <form className="fx-form" id="fx-form" onSubmit={save}>
              {/* what is sent, also readable by the browser tests (tests/browser/*.mjs) */}
              <input type="hidden" name="field" value={target.field} /><input type="hidden" name="row_key" value={target.row ?? ""} />
              <input type="hidden" name="region" value={region} /><input type="hidden" name="shown" value={target.shown} />
              <div className="fx-head">
                <div><div className="fx-kicker">Memperbaiki</div><div className="fx-what">{target.label}</div>
                  <div className="fx-desc small">{target.desc}</div>
                  <div className="fx-now">terbaca <span className="mono">{target.shown || "kosong"}</span></div></div>
                <button type="button" className="fx-x" title="Batal (Esc)" onClick={cancel}>✕</button>
              </div>
              <ol className="fx-steps">
                <li className={stepTwo ? "done" : "on"}>Klik nilainya di kertas <span className="muted">· tarik melintasi beberapa kata untuk mengambil semuanya</span></li>
                <li className={stepTwo ? "on" : ""}>Periksa, lalu simpan</li>
              </ol>
              {!!target.copied?.length && (
                <div className="fx-copied">
                  <div className="small">Baris ini seperti disalin AI, dari kiri ke kanan. <b>Klik nilai yang Anda lihat di kolomnya pada kertas:</b></div>
                  <div className="fx-chips" id="fx-chips">
                    {target.copied.map((c, k) => (
                      <button key={k} type="button" className={cx("btn fx-chip mono", chip === k && "on")} onClick={() => {
                        setChip(k); setPicked([]);
                        const b = fieldBox.get(target.hl);
                        took(c, "", b ? [b[0], b[1], b[2], b[3]] : [], false,
                             { cls: "muted", text: "Dari salinan AI untuk baris ini. Cocokkan dengan baris yang diperbesar di kertas." });
                      }}>{c}</button>
                    ))}
                  </div>
                </div>
              )}
              {(big !== null || keyNote) && (
                <div className="fx-pick">
                  <div className="fx-big mono">{big ?? value}</div>
                  {tess && <div className={`fx-tess small ${tess.cls}`}>{tess.text}</div>}
                  {keyNote && <div className={`fx-key small ${keyNote.cls}`}>{keyNote.text}</div>}
                </div>
              )}
              <label className="fx-lab">Nilai seperti tercetak
                <input ref={valueIn} id="fx-value" name="value" className="mono" required autoComplete="off" placeholder="klik di kertas, atau ketik"
                       value={value} onChange={(e) => setValue(e.target.value)} /></label>
              {named ? (
                <div className="fx-byline small">Disimpan atas nama <b>{by}</b> ·{" "}
                  <a href="#" onClick={(e) => { e.preventDefault(); setEditName(true); }}>bukan Anda?</a></div>
              ) : (
                <label className="fx-lab">Nama Anda <input required autoComplete="off" value={by} onChange={(e) => setBy(e.target.value)} autoFocus={editName} /></label>
              )}
              {error && <p className="salah">{error}</p>}
              <div className="fx-buttons">
                <button className="btn primary fx-save" id="fx-save" disabled={!value.trim() || !by.trim() || busy}>{busy ? "Menyimpan…" : "Simpan"}</button>
                <button type="button" className="btn" onClick={notPrinted}>Tidak tercetak di halaman ini</button>
              </div>
              <div className="small muted">Nilainya tidak punya kotak? Tarik kotak di sekelilingnya di kertas: nilainya dibaca dari situ.</div>
            </form>
          )}

          {!fix.ready && <p className="muted small">AI belum selesai membaca halaman ini (masih membaca ulang); perbaikan bisa dilakukan setelah selesai.</p>}
          <div className="fx-list">
            {fix.fields.map((f) => (
              <div className="fx-item" key={f.name} onMouseEnter={() => setHot(f.name)} onMouseLeave={() => setHot(null)}>
                <div>
                  <div className="fx-label">{f.label}{f.role && <> <span className="fx-role" title="nilai ini ikut menentukan: menghubungkan ke order, atau dicek dengan Satellite">penentu</span></>}</div>
                  <div className="mono fx-val">{nilai(f.value)}</div>
                  {f.desc && <div className="small fx-desc">{f.desc}</div>}
                  <div className="muted small">{f.says}</div>
                </div>
                <div className="fx-right">
                  {f.person ? <span className="chip person">diperbaiki orang</span>
                    : f.verdict === "ok" ? <span className="chip ok" title={`dipastikan oleh: ${f.by ? w.BY[f.by] ?? f.by : "cetakan"}`}>✓ {f.by ? w.BY[f.by] ?? f.by : "pasti"}</span>
                    : f.verdict === "empty" ? <span className="chip">tidak terbaca</span>
                    : <span className="chip warn">belum pasti</span>}
                  {fix.ready && <button type="button" className="btn js-fx" data-f={f.name} onClick={() => begin(targets.get(f.name)!)}>Perbaiki</button>}
                </div>
              </div>
            ))}
          </div>

          {fix.rows.length > 0 && (
            <details className="fx-rows" open={rowsOpen} onToggle={(e) => setRowsOpen((e.target as HTMLDetailsElement).open)}>
              <summary>Baris tabel · {fix.rows.length}</summary>
              {fix.rows.map((r) => (
                <div className="fx-rowitem" key={r.i} data-f={`row${r.i}`} onMouseEnter={() => setHot(`row${r.i}`)} onMouseLeave={() => setHot(null)}>
                  <div className="small"><b>Baris {r.i}</b> <span className="muted">{(r.text ?? "").slice(0, 90)}</span></div>
                  <div className="fx-cells">
                    {r.cells.map(([col, val, byPerson]) => (
                      <span key={col} className={cx("fx-cell", byPerson && "person")}>
                        <span className="muted">{w.COL[col] ?? col.replace(/_/g, " ")}</span> <b className="mono">{nilai(val)}</b>
                        {fix.ready && <button type="button" className="btn tiny js-fx" data-f={`lines[${r.key}].${col}`} onClick={() => begin(targets.get(`lines[${r.key}].${col}`)!)}>Perbaiki</button>}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </details>
          )}
        </div>
      </div>
    </section>
  );
}
