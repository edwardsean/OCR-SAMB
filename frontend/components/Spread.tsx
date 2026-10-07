"use client";
// The page beside the data (Data terkirim, Periksa order; the user, 2026-10-02: "show the page in the right when
// scrolling, so that the user can reconfirm while looking at the page"). The scan page on the right follows the
// document or card being read on the left (useFollow), and a value or row pointed at (useSpot: hover, keyboard
// focus, or a click that pins it) is boxed where it sits on its page.
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cx } from "@/lib/format";
import type { Box } from "@/lib/types";

export type Spot = { id: string; page: number | null; box: Box | undefined; approx?: boolean; label: string };
export type SpreadPage = { n: number; img: string; alt: string; tab: ReactNode; title?: string };

type State = {
  current: number | null; shown: Spot | null; pinned: Spot | null; zoomed: boolean;
  enter(s: Spot): void; leave(): void; focus(s: Spot): void; click(s: Spot): void; follow(n: number): void;
  tab(n: number): void; zoom(): void;
};
const Ctx = createContext<State | null>(null);

export function SpreadProvider({ pages, children }: { pages: SpreadPage[]; children: ReactNode }) {
  const [current, setCurrent] = useState<number | null>(pages[0]?.n ?? null);
  const [shown, setShown] = useState<Spot | null>(null);
  const [pinned, setPinned] = useState<Spot | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const pointing = useRef(false), pin = useRef<Spot | null>(null), known = useRef(pages);
  known.current = pages;

  // the actions never change, so a section's observer is made once (a new one would report at once, and pull the
  // page back to whatever card is on screen)
  const act = useMemo(() => {
    const has = (n: number | null) => n !== null && known.current.some((p) => p.n === n);
    const show = (s: Spot | null) => { setShown(s); if (s && has(s.page)) setCurrent(s.page); };
    return {
      enter(s: Spot) { pointing.current = true; show(s); },
      leave() { pointing.current = false; show(pin.current); },
      focus(s: Spot) { show(s); },                          // also while typing in a row's answer
      click(s: Spot) {                                      // a click keeps the box until the next click
        pin.current = pin.current?.id === s.id ? null : s;
        setPinned(pin.current);
        show(pin.current ?? s);
      },
      follow(n: number) { if (!pointing.current && !pin.current && has(n)) setCurrent(n); },
      tab(n: number) { pin.current = null; setPinned(null); setShown(null); setCurrent(n); },
      zoom() { setZoomed((z) => !z); },
    };
  }, []);
  const state = useMemo<State>(() => ({ current, shown, pinned, zoomed, ...act }), [current, shown, pinned, zoomed, act]);
  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

function useSpread(): State {
  const s = useContext(Ctx);
  if (!s) throw new Error("outside SpreadProvider");
  return s;
}

/** Props for an element that points at its spot on the page: spread them onto a div, a tr, a form. */
export function useSpot(spot: Spot) {
  const s = useSpread();
  return {
    className: cx(s.shown?.id === spot.id && "pointed", s.pinned?.id === spot.id && "pinned"),
    onMouseEnter: () => s.enter(spot),
    onMouseLeave: () => s.leave(),
    onFocus: () => s.focus(spot),
    onClick: (e: React.MouseEvent) => {
      if ((e.target as Element).closest("button, input, select, textarea, a, label")) return;   // not when answering
      s.click(spot);
    },
  };
}

/** A ref for a section (a document, a card): while it crosses the upper third of the screen, its page shows. */
export function useFollow<T extends Element>(page: number | null | undefined) {
  const s = useSpread();
  const ref = useRef<T>(null);
  const follow = s.follow;
  useEffect(() => {
    const el = ref.current;
    if (!el || !page || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && follow(page)), { rootMargin: "-25% 0px -65% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [page, follow]);
  return ref;
}

export function SpreadView({ pages, label, hint, children }: { pages: SpreadPage[]; label: string; hint: string; children?: ReactNode }) {
  const s = useSpread();
  const sheet = useRef<HTMLDivElement>(null), papers = useRef(new Map<number, HTMLDivElement>());
  const cur = pages.find((p) => p.n === s.current) ?? pages[0];
  const b = s.shown && s.shown.page === cur?.n && s.shown.box && s.shown.box.length === 4 ? s.shown.box : null;
  const where = !s.shown ? "" : !b ? `${s.shown.label.trim()}: letaknya di halaman tidak tercatat`
    : `${s.shown.label.trim()}${s.shown.approx ? ": perkiraan letak (tebakan AI, cocokkan sendiri)" : ": ditandai di halaman"}`;

  useEffect(() => { sheet.current?.scrollTo({ top: 0 }); }, [cur?.n]);
  useEffect(() => {                                        // bring the box into view (zoomed, or a long page)
    const paper = cur && papers.current.get(cur.n), sh = sheet.current;
    if (!b || !paper || !sh) return;
    const y = ((b[0] + b[2]) / 2000) * paper.offsetHeight, x = ((b[1] + b[3]) / 2000) * paper.offsetWidth;
    sh.scrollTo({ top: Math.max(0, y - sh.clientHeight / 2), left: Math.max(0, x - sh.clientWidth / 2), behavior: "smooth" });
  }, [b, cur, s.zoomed]);

  const pad = 6;
  return (
    <aside className={cx("pd-view", s.zoomed && "zoomed", !pages.length && "empty")} aria-label={label}>
      <div className="pd-tabs" role="tablist">
        {pages.map((p) => (
          <button key={p.n} type="button" role="tab" title={p.title} className={cx(p.n === cur?.n && "on")} aria-selected={p.n === cur?.n}
                  onClick={() => s.tab(p.n)}>{p.tab}</button>
        ))}
      </div>
      <div className="pd-sheet" ref={sheet}>
        {pages.map((p) => (
          <div key={p.n} className="pd-pg" hidden={p.n !== cur?.n}>
            <div className="pd-paper" ref={(el) => { if (el) papers.current.set(p.n, el); else papers.current.delete(p.n); }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.img} alt={p.alt} loading="lazy" draggable={false} />
              <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
                {p.n === cur?.n && b ? (
                  <rect className={cx("pd-hi on", s.shown?.approx && "approx")} x={Math.max(0, b[1] - pad)} y={Math.max(0, b[0] - pad)}
                        width={Math.min(1000, b[3] - b[1] + 2 * pad)} height={Math.min(1000, b[2] - b[0] + 2 * pad)} />
                ) : <rect className="pd-hi" x={0} y={0} width={0} height={0} />}
              </svg>
            </div>
          </div>
        ))}
        {children}
      </div>
      <div className="pd-vbar">
        <button type="button" className="btn tiny pd-zoom" onClick={s.zoom}>{s.zoomed ? "Pas lebar" : "Perbesar"}</button>
        <a className="btn tiny pd-open" href={cur?.img ?? "#"} target="_blank" rel="noopener">Buka gambar</a>
        <span className="muted small pd-where">{where || hint}</span>
      </div>
    </aside>
  );
}
