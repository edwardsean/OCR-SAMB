"use client";
// Talking to the REST API from the browser: through this app's own address (/api/v1 is rewritten to FastAPI).

export type Answer<T = unknown> = { ok: boolean; status: number; data: T & { error?: string } };

async function send<T>(method: string, path: string, body?: unknown): Promise<Answer<T>> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  try {
    const r = await fetch(`/api/v1${path}`, init);
    const text = await r.text();
    let data: unknown = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text.slice(0, 300) }; }
    return { ok: r.ok, status: r.status, data: data as T & { error?: string } };
  } catch (e) {
    return { ok: false, status: 0, data: { error: `Sistem tidak bisa dihubungi: ${(e as Error).message}` } as T & { error?: string } };
  }
}

export const api = {
  get: <T,>(path: string) => send<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => send<T>("POST", path, body),
};

/** A refusal said for people: the API's own reason when it gives one. */
export function why(a: Answer): string {
  return a.data?.error || (a.status === 422 ? "Isian belum lengkap." : `Gagal menyimpan (kode ${a.status}).`);
}
