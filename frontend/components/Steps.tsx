// A batch's five steps (2026-10-05; the user: "when we click the batch, the steps for each comes up, but it should be
// ordered correctly so that the user isnt confused"). The counts come from the API (services/api/steps.py); the words
// are said here. Ink, never tiles (DESIGN.md): red = needs you, amber = the system works, green = done, pencil = none.
import Link from "next/link";
import type { Step, StepKey } from "@/lib/types";

export const ORDER: StepKey[] = ["baca", "jenis", "cocokkan", "periksa", "kirim"];
export const NAME: Record<StepKey, string> = {
  baca: "Dibaca AI", jenis: "Jenis halaman", cocokkan: "Cocokkan ke order", periksa: "Periksa order", kirim: "Kirim ke Satellite",
};
const n = (k: StepKey) => ORDER.indexOf(k) + 1;

/** "langkah 3", "langkah 1–3", "langkah 1 dan 3": the steps an order waits for. */
export function langkah(keys: StepKey[]): string {
  const ns = keys.map(n).sort((a, b) => a - b);
  if (!ns.length) return "langkah sebelumnya";
  if (ns.length === 1) return `langkah ${ns[0]}`;
  return ns[ns.length - 1] - ns[0] === ns.length - 1 ? `langkah ${ns[0]}–${ns[ns.length - 1]}`
    : `langkah ${ns.slice(0, -1).join(", ")} dan ${ns[ns.length - 1]}`;
}

/** What a step says: [the line in its ink, the line under it]. A step that can't finish before an earlier one says
 * which (`after`), never ✓ (the mentor, 2026-10-08: "jenis halaman selesai" while pages were still being read). */
export function words(s: Step, blockers: StepKey[] = []): [string, string] {
  const wait = s.after ? `Menunggu langkah ${n(s.after)}` : null;
  switch (s.key) {
    case "baca":
      if (s.state === "need") return [`${s.failed} gagal dibaca`, `${s.done} dari ${s.pages} halaman selesai`];
      if (s.state === "sys") {
        if (!s.pages) return ["Menyiapkan file", "dipecah menjadi halaman"];
        if (s.busy) return ["Sedang dibaca", `${s.done} dari ${s.pages} halaman selesai`];
        if (s.waiting_ai) return ["AI melihat ulang", `${s.waiting_ai} halaman · berjalan sendiri`];
        return [`${s.unscheduled} belum dijadwalkan`, `${s.done} dari ${s.pages} halaman selesai`];
      }
      return s.state === "done" ? ["Selesai", `${s.pages} halaman dibaca`] : ["Belum mulai", "file sedang disiapkan"];
    case "jenis":
      if (s.state === "need") return [`${s.unsure} perlu Anda`, "jenisnya belum pasti"];
      if (s.state === "done") return ["Selesai", s.answered ? `${s.answered} dipilih Anda` : "semua jenis sudah pasti"];
      return [wait ?? "Belum mulai", s.pending ? `${s.pending} halaman belum dibaca` : "menunggu pembacaan"];
    case "cocokkan": {
      const fpj = s.later ? `${s.later} Faktur Pajak menunggu SAP` : "";
      if (s.state === "need") return [`${s.block} perlu Anda`, "nomornya belum pasti"];
      if (s.state === "sys") return [`${s.wait} menunggu sistem`, "AI atau SAP · berjalan sendiri"];
      if (s.state === "done") return ["Selesai", fpj || "semua dokumen punya order"];
      return [wait ?? "Belum mulai", fpj || "dokumen muncul setelah dibaca"];
    }
    case "periksa":
      if (s.state === "need") return [`${s.need} perlu Anda`, `dari ${s.orders} order`];
      if (s.state === "sys") return [s.depends ? `${s.depends} menunggu ${langkah(blockers)}` : `${s.waiting} menunggu sistem`,
        `dari ${s.orders} order`];
      if (s.state === "later") return [`${s.outside} menunggu batch lain`, "dokumennya belum diunggah"];
      if (s.state === "done") return ["Selesai", `${s.orders} order`];
      return [wait ?? "Belum ada order", s.orders ? `${s.orders} order sejauh ini` : "order muncul setelah dibaca"];
    case "kirim":
      if (s.state === "need") return [`${s.ready} siap dikirim`, s.published ? `${s.published} sudah terkirim` : "tinggal dikirim"];
      if (s.state === "done") return [`${s.published} terkirim`, "tidak ada yang menunggu"];
      return [wait ?? "Belum ada", s.published ? `${s.published} terkirim sejauh ini` : "order siap muncul di sini"];
  }
}

/** The count a step's mark and button show: what needs you there. */
export function count(s: Step): number {
  switch (s.key) {
    case "baca": return s.failed;
    case "jenis": return s.unsure;
    case "cocokkan": return s.block;
    case "periksa": return s.need;
    case "kirim": return s.ready;
  }
}

/** Five small marks for the batch register: the count in red, ✓, ◔ (the system works), – (none yet). */
export function StepMarks({ steps, href }: { steps: Step[]; href: string }) {
  return (
    <span className="ws-marks">
      {steps.map((s, i) => (
        <Link key={s.key} href={`${href}?step=${s.key}`} className={`ws-mk ${s.state}`}
              title={`${i + 1}. ${NAME[s.key]}: ${words(s).join(", ")}`}>
          {s.state === "need" ? count(s) : s.state === "done" ? "✓" : s.state === "sys" ? "◔" : "–"}
        </Link>
      ))}
    </span>
  );
}

/** The workspace's stepper: one link per step, the open one underlined. */
export function Stepper({ steps, at, href, blockers }: { steps: Step[]; at: StepKey; href: string; blockers: StepKey[] }) {
  return (
    <nav className="ws-steps" aria-label="Langkah batch ini">
      {steps.map((s, i) => {
        const [head, sub] = words(s, blockers);
        return (
          <Link key={s.key} href={`${href}?step=${s.key}`} className={`ws-st ${s.state}${s.key === at ? " on" : ""}`}
                aria-current={s.key === at ? "step" : undefined}>
            <span className="ws-n">{i + 1} · {i === 0 ? "sistem" : "Anda"}</span>
            <b className="ws-name">{NAME[s.key]}</b>
            <span className="ws-head">{head}</span>
            <span className="ws-sub">{sub}</span>
          </Link>
        );
      })}
    </nav>
  );
}
