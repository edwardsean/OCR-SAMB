// Numbers and dates the Indonesian way: services/api/bahasa.py's angka/rp/qty/tgl/nilai, so the
// API's words and the screens' numbers agree. The API sends amounts as numbers and dates as ISO strings.

const BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const WIB_MS = 7 * 3600 * 1000;

type Num = number | string | null | undefined;

function grouped(x: number, dec: number): string {
  // 1234567.8 -> "1.234.567,80": dot groups thousands, comma marks decimals
  const [whole, frac] = Math.abs(x).toFixed(dec).split(".");
  const g = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return (x < 0 && Number(x.toFixed(dec)) !== 0 ? "-" : "") + g + (frac ? "," + frac : "");
}

function num(x: Num): number | null {
  if (x === null || x === undefined || x === "") return null;
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : null;
}

export function angka(x: Num, dec = 2): string {
  if (x === null || x === undefined || x === "") return "—";
  const n = num(x);
  return n === null ? String(x) : grouped(n, dec);
}

export function rp(x: Num, dec = 2): string {
  return x === null || x === undefined || x === "" ? "—" : "Rp " + angka(x, dec);
}

/** A quantity without trailing zeros: 2.0 -> "2", 0.5 -> "0,5". */
export function qty(x: Num): string {
  if (x === null || x === undefined || x === "") return "—";
  const n = num(x);
  if (n === null) return String(x);
  return grouped(n, 3).replace(/0+$/, "").replace(/,$/, "");
}

/** "%g" for the small numbers Review puts on buttons (2.0 -> "2", 2.5 -> "2.5"). */
export function g(x: number): string {
  return String(Number(x.toPrecision(6)));
}

/** A date (or timestamp) as people write it: "30 Sep 2026" / "30 Sep 2026, 16.26" (WIB). */
export function tgl(v: string | null | undefined, jam = true): string {
  if (!v) return "—";
  const s = String(v).trim();
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (d) return `${+d[3]} ${BULAN[+d[2] - 1]} ${d[1]}`;
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(s);
  if (!m) return s;
  let [y, mo, day, h, mi] = [+m[1], +m[2], +m[3], +m[4], +m[5]];
  if (m[7]) {                                   // a moment in time: shown in WIB
    const ms = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6] ?? "00"}${m[7]}`);
    const w = new Date(ms + WIB_MS);
    [y, mo, day, h, mi] = [w.getUTCFullYear(), w.getUTCMonth() + 1, w.getUTCDate(), w.getUTCHours(), w.getUTCMinutes()];
  }
  const out = `${day} ${BULAN[mo - 1]} ${y}`;
  return jam ? `${out}, ${String(h).padStart(2, "0")}.${String(mi).padStart(2, "0")}` : out;
}

/** A value read from a page: "—" when empty; a person's "(not printed)" said in Indonesian. */
export function nilai(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return v === "(not printed)" ? "(tidak tercetak)" : String(v);
}

export function pct(done: number, total: number): number {
  return total ? Math.round((100 * done) / total) : 0;
}

export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}
