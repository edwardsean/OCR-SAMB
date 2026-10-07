"use client";
// The status bar after a fix (the user, 2026-10-01): what the fix is doing now, in plain words. It asks again every
// 3 seconds until nothing more will happen by itself (final).
import { useEffect, useState } from "react";
import { api } from "@/lib/client";
import type { Lesson } from "@/lib/types";

const STATE: Record<string, string> = { done: "selesai", now: "sedang berjalan", todo: "berikutnya", skip: "tidak perlu", stop: "berhenti di sini" };

export default function LessonStatus({ batch, page, field }: { batch: string; page: number; field: string }) {
  const [lp, setLp] = useState<Lesson | null>(null);
  useEffect(() => {
    let live = true, t: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      const q = new URLSearchParams({ batch, page: String(page), field });
      const a = await api.get<Lesson>(`/lessons?${q}`);
      if (!live) return;
      if (a.ok) setLp(a.data);
      if (!a.ok || !a.data.final) t = setTimeout(ask, 3000);
    };
    ask();
    return () => { live = false; if (t) clearTimeout(t); };
  }, [batch, page, field]);
  if (!lp) return null;
  return (
    <div className="lesson-bar">
      <div className="lb-head">{!lp.final && <span className="lb-spin" aria-hidden="true" />}{lp.headline}</div>
      {lp.steps.length > 0 && (
        <ol className="lb-steps">
          {lp.steps.map((s) => <li key={s.label} className={s.state} title={STATE[s.state]}>{s.label}</li>)}
        </ol>
      )}
      {lp.tip && <div className="lb-tip">Kiat dari guru AI: “{lp.tip}”</div>}
    </div>
  );
}
