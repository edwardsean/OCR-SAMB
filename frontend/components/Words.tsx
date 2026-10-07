"use client";
// The display words (services/api/bahasa.py via /api/v1/words), for client components. Server components read them
// with getWords() from lib/api.
import { createContext, useContext, type ReactNode } from "react";
import type { Words } from "@/lib/types";

const Ctx = createContext<Words | null>(null);

export function WordsProvider({ words, children }: { words: Words; children: ReactNode }) {
  return <Ctx.Provider value={words}>{children}</Ctx.Provider>;
}

export function useWords(): Words {
  const w = useContext(Ctx);
  if (!w) throw new Error("useWords outside WordsProvider");
  return w;
}

/** A field's name for people (bahasa.field): by document type, else the plain name; a line cell by its column. */
export function fieldName(w: Words, name: string, t?: string | null): string {
  const m = /^lines\[.*\]\.(\w+)$/.exec(name);
  if (m) return w.COL[m[1]] ?? m[1].replace(/_/g, " ");
  return (t && w.FIELD_BY_TYPE[t]?.[name]) || w.FIELD[name] || name.replace(/_/g, " ");
}
