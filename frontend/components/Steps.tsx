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

/** What a step says: [the line in its ink, the line under it]. */
export function words(s: Step, blockers: StepKey[] = []): [string, string] {
  switch (s.key) {
    case "baca":
      if (s.state === "need") return [`${s.failed} gagal dibaca`, `${s.read} dari ${s.pages} halaman dibaca`];
      if (s.busy) return ["Sedang dibaca", `${s.read} dari ${s.pages} halaman`];
      if (s.waiting_ai) return [`${s.waiting_ai} menunggu AI`, `${s.read} dari ${s.pages} halaman dibaca`];
      if (s.unscheduled) return [`${s.read} dari ${s.pages} dibaca`, `${s.unscheduled} belum dijadwalkan`];
      return s.pages ? ["Selesai", `${s.pages} halaman dibaca`] : ["Belum ada", "file sedang disiapkan"];
    case "jenis":
      if (s.state === "need") return [`${s.unsure} halaman`, s.answered ? `${s.answered} sudah dijawab` : "jenisnya belum pasti"];
      return s.state === "done" ? ["Selesai", s.answered ? `${s.answered} sudah dijawab` : "semua jenis sudah pasti"]
        : ["Belum ada", "menunggu pembacaan"];
    case "cocokkan": {
      const later = s.later ? `+${s.later} tidak mendesak` : "";
      if (s.state === "need") return [`${s.block} dokumen`, later || "nomornya belum pasti"];
      if (s.state === "sys") return [`${s.wait} menunggu sistem`, later || "AI atau SAP"];
      if (s.state === "later") return [`${s.later} tidak mendesak`, "tidak menghalangi pengiriman"];
      return s.state === "done" ? ["Selesai", "semua dokumen punya order"] : ["Belum ada", "menunggu pembacaan"];
    }
    case "periksa": {
      const dep = s.depends ? `${s.depends} menunggu ${langkah(blockers)}` : "";
      if (s.state === "need") return [`${s.need} order`, dep || (s.waiting ? `${s.waiting} menunggu sistem` : `dari ${s.orders} order`)];
      if (s.state === "sys") return [dep || `${s.waiting} menunggu sistem`, `dari ${s.orders} order`];
      if (s.state === "later") return [`${s.outside} menunggu batch lain`, "dokumennya belum diunggah"];
      return s.state === "done" ? ["Selesai", `${s.orders} order`] : ["Belum ada", "menunggu pembacaan"];
    }
    case "kirim":
      if (s.state === "need") return [`${s.ready} siap dikirim`, s.published ? `${s.published} sudah terkirim` : "tinggal dikirim"];
      return s.state === "done" ? [`${s.published} terkirim`, "tidak ada yang menunggu"] : ["Belum ada", "0 siap dikirim"];
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
