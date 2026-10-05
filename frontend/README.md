# frontend: the web app

The screens Finance Invoicing works in, as a Next.js 16 app (React 19, TypeScript): **Batch** (every upload batch and
what each needs next), a batch's own page with its five steps in order (Dibaca AI, Jenis halaman, Cocokkan ke order,
Periksa order, Kirim ke Satellite; `services/api/steps.py` counts them), Unggah batch, Cari, an order's review and a
page's viewer. The screens over every batch at once (Periksa order, Jenis halaman, Berkas per SOR, Data terkirim) are
under Teknis. It holds no data and no
rules: everything is read from and written to the API's REST endpoints, `/api/v1` (`services/api/v1.py`, documented
in [docs/api.md](../docs/api.md)).

```
browser ──▶ web app (rtm-web, :3002) ──▶ API (rtm-api, :8002) ──▶ Postgres / MinIO / RabbitMQ
              │ server components: GET $API_URL/api/v1/…
              │ the browser: /api/v1/… on this same address (passed on to the API)
              └ what it has no page for (/img, /crop, /documents, the Teknis screens) is passed on to the API
```

- **Reads:** server components call `lib/api.ts` (`get`, `need`) when a person opens a screen, never at build time.
- **Writes:** a person's decision is a `POST` from the browser (`lib/client.ts`), then `router.refresh()` draws the
  screen again from the API.
- **Look:** `app/globals.css`, following [DESIGN.md](DESIGN.md) (the design contract: read it before changing a screen).
- **Words:** display words come from `/api/v1/words` (`services/api/bahasa.py`); numbers and dates are written the
  Indonesian way by `lib/format.ts`.

## Run it

With Docker (from the repository root; the API must be running too):

```bash
docker compose up -d --build rtm-web          # http://localhost:3002 (WEB_PORT)
```

On this computer, while changing it:

```bash
cd frontend
cp .env.example .env.local                    # API_URL: where the API answers
npm install
PORT=3002 npm run dev                         # http://localhost:3002
npm run typecheck
```

`API_URL` is required: the app refuses to start without it. Next writes its rewrites into the build, so the Docker
image is built with the API's service address (`docker-compose.yml` passes it).

## Where things are

| | |
|---|---|
| `app/` | one folder per screen; a screen's interactive parts sit beside its `page.tsx` |
| `components/Spread.tsx` | the scan page beside the data, following what is read and boxing what is pointed at |
| `app/batches/[id]/pages/[n]/PageFixer.tsx` | the page viewer: correct a value by clicking it on the paper |
| `app/review/[sor]/` | one order's review: a card per decision, and the forms that answer them |
| `lib/types.ts` | the shapes `/api/v1` answers with |
| `../tests/browser/*.mjs` | browser tests of the page viewer, run by hand against this app |
