"use client";
// Who is deciding: typed once, remembered in this browser, and signed on every decision (the same key the
// server-rendered screens used: "rv-name").
import { useCallback, useEffect, useState } from "react";

const KEY = "rv-name";

export function useReviewer(): [string, (v: string) => void] {
  const [name, setName] = useState("");
  useEffect(() => {
    try { setName(localStorage.getItem(KEY) || ""); } catch { /* private window: typed each time */ }
  }, []);
  const set = useCallback((v: string) => {
    setName(v);
    try { localStorage.setItem(KEY, v); } catch { /* not kept */ }
  }, []);
  return [name, set];
}
