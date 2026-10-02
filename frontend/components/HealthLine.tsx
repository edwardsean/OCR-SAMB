"use client";
// Beranda's one line about the system, asked after the page shows (probing every service takes a moment).
import { useEffect, useState } from "react";
import { api } from "@/lib/client";

export default function HealthLine() {
  const [h, setH] = useState<{ bad: string[]; n: number } | null>(null);
  useEffect(() => { api.get<{ bad: string[]; n: number }>("/health").then((a) => a.ok && setH(a.data)); }, []);
  if (!h) return <span className="hp wait"><i />Memeriksa sistem…</span>;
  return h.bad.length ? (
    <a className="hp bad" href="/status" title={h.bad.join(", ")}><i />{h.bad.length} dari {h.n} layanan bermasalah, hubungi tim IT</a>
  ) : (
    <a className="hp ok" href="/status"><i />Sistem berjalan normal</a>
  );
}
