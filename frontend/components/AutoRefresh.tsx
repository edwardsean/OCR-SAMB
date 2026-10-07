"use client";
// Asks the server for the page again every few seconds while something is still moving (a scan being read).
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function AutoRefresh({ every, active = true }: { every: number; active?: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), every);
    return () => clearInterval(t);
  }, [router, every, active]);
  return null;
}
