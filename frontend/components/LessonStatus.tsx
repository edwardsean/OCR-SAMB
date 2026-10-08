"use client";
// The status bar after a fix (the user, 2026-10-01): what the fix is doing now, in plain words. It asks again every
// 3 seconds until nothing more will happen by itself (final). A value's fix (field) is a lesson for the knowledge; a
// page's type (type) is a lesson for the classifier's context (services/api/relabel.py).
import { useEffect, useState } from "react";
import { api } from "@/lib/client";
import type { Lesson } from "@/lib/types";

const STATE: Record<string, string> = { done: "selesai", now: "sedang berjalan", todo: "berikutnya", skip: "tidak perlu", stop: "berhenti di sini" };

type Props = { batch: string; page: number } & ({ field: string; type?: never } | { type: true; field?: never });

export default function LessonStatus({ batch, page, field, type }: Props) {
  const [lp, setLp] = useState<Lesson | null>(null);
  useEffect(() => {
    let live = true, t: ReturnType<typeof setTimeout> | undefined;
    const path = type ? `/scans/${encodeURIComponent(batch)}/pages/${page}/type-lesson`
      : `/lessons?${new URLSearchParams({ batch, page: String(page), field: field! })}`;
    const ask = async () => {
      const a = await api.get<Lesson>(path);
      if (!live) return;
      if (a.ok) setLp(a.data);
      if (!a.ok || !a.data.final) t = setTimeout(ask, 3000);
    };
    ask();
    return () => { live = false; if (t) clearTimeout(t); };
  }, [batch, page, field, type]);
  if (!lp) return null;
  return (
    <div className="lesson-bar">
      <div className="lb-head">{!lp.final && <span className="lb-spin" aria-hidden="true" />}{lp.headline}</div>
      {lp.steps.length > 0 && (
        <ol className="lb-steps">
          {lp.steps.map((s) => <li key={s.label} className={s.state} title={STATE[s.state]}>{s.label}</li>)}
        </ol>
      )}
      {lp.why && <div className="lb-tip">Kenapa sistem salah, menurut guru AI: “{lp.why}”</div>}
      {lp.tip && <div className="lb-tip">{type ? "Usulan guru AI" : "Kiat dari guru AI"}: “{lp.tip}”</div>}
      {lp.detail && <details className="small"><summary>Detail untuk tim IT</summary><code>{lp.detail}</code></details>}
    </div>
  );
}
