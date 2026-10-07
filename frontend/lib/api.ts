// Reading the REST API from the server (server components). The browser never calls this: it goes through
// lib/client.ts and the /api/v1 rewrite.
import "server-only";
import { cache } from "react";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import type { Words } from "./types";

const API = (process.env.API_URL ?? "").replace(/\/+$/, "");   // where FastAPI answers (.env.example)

type Params = Record<string, string | number | null | undefined>;

export class ApiDown extends Error {}

/** GET /api/v1<path>: its JSON, or null when the API answers 404. */
export async function get<T>(path: string, params: Params = {}): Promise<T | null> {
  await connection();                               // live data: read when a person asks, never at build time
  if (!API) throw new ApiDown("API_URL is not set: where the API answers (see frontend/.env.example)");
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const url = `${API}/api/v1${path}${q.size ? "?" + q : ""}`;
  let r: Response;
  try {
    r = await fetch(url, { cache: "no-store" });
  } catch (e) {
    throw new ApiDown(`Sistem tidak bisa dihubungi (${url}): ${(e as Error).message}`);
  }
  if (r.status === 404) return null;
  if (!r.ok) throw new ApiDown(`Sistem menjawab ${r.status} untuk ${path}: ${(await r.text()).slice(0, 300)}`);
  return (await r.json()) as T;
}

/** The same, for a page that can't exist without it: 404 page when the API says not found. */
export async function need<T>(path: string, params: Params = {}): Promise<T> {
  const d = await get<T>(path, params);
  if (d === null) notFound();
  return d;
}

/** The display words (bahasa.py), once per request. */
export const getWords = cache(async (): Promise<Words> => (await get<Words>("/words"))!);

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}
