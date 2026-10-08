"""What each page of a batch is doing now, for its step 1 (the user, 2026-10-08: "the user lacks what is processing
in each step, and what am i waiting for"; the mentor flagged step 1 saying "1 dari 4 halaman dibaca" while two files
said "Selesai dibaca"). One state per page, from the page and its trace (common/trace.py), so a step's count and its
list can never disagree:

  splitting  its file is being split into pages (no page row yet: said per file)
  queued     waiting for a free page worker; `ahead` = pages before it in the queue, over every batch
  reading    a page worker has it; `stage` = what it is doing (worker/vf.py's trace.stage names), `since` = start
  waiting    stopped until a limit ends or a model is set (vf.blocked); it goes on by itself
  waiting_ai read; the AI still has to look again at a few values (an order asked for it, or its own look-again)
  failed     stuck (api/stuck.py): a person may try it again
  idle       never sent to be read (a clone's page, whose stand-in image no worker reads)
  done       read

`again` marks a page read before and being read again (a person changed its type, an order asked a look-again)."""
import statistics

from api import stuck

STAGE = {"prepare": "Menyiapkan gambar halaman", "read": "AI membaca isi halaman",
         "classify": "Menentukan jenis dokumen", "knowledge": "Memakai pengetahuan tentang pelanggan ini",
         "project": "Menyusun isian sesuai jenis dokumennya", "wait_tesseract": "Menunggu Tesseract selesai membaca",
         "boxes": "Menandai letak setiap nilai di halaman",
         "tesseract": "Mencocokkan dengan teks yang tercetak", "check": "Memeriksa angka",
         "look_again": "AI melihat ulang bagian yang belum pasti", "store": "AI mencari nama toko",
         "save": "Menyimpan hasil"}
DEFAULT_PAGE_S = 90              # a page's usual time before anything was measured (2026-10-08: 70–100 s, 3 workers)
DEFAULT_WORKERS = 3

PAGES = f"""
SELECT p.batch_id, p.page_no, s.file_name, s.status::text AS scan_status, p.status::text AS status, p.extract_status,
       p.extract_error, p.error,
       p.outcome, p.type_status, p.doc_type::text AS doc_type, p.fields_all IS NOT NULL AS was_read, p.read_at,
       p.second_look->>'waiting' AS waits, p.thumb_upright_path, p.thumb_path,
       l.page_no IS NOT NULL AS labelled, {stuck.STUCK} AS stuck, {stuck.PUBLISHED} AS published,
       run.at AS reading_since, st.kind AS stage, st.at AS stage_since,
       q.at AS queued_at, q.detail->>'why' AS queued_why, pk.at AS parked_at, pk.detail->>'why' AS parked_why,
       last.ms AS last_ms
  FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
  LEFT JOIN staging.type_label l ON l.batch_id = p.batch_id AND l.page_no = p.page_no
  LEFT JOIN LATERAL (SELECT at FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                        AND t.kind = 'page' AND t.status = 'running' ORDER BY at DESC LIMIT 1) run ON true
  LEFT JOIN LATERAL (SELECT kind, at FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                        AND t.kind LIKE 'page.%%' AND t.status = 'running'
                      ORDER BY (t.kind = 'page.tesseract'), at DESC LIMIT 1) st ON true   -- Tesseract runs beside
  LEFT JOIN LATERAL (SELECT at, detail FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                        AND t.kind = 'page.queued' ORDER BY at DESC LIMIT 1) q ON true
  LEFT JOIN LATERAL (SELECT at, detail FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                        AND t.kind = 'page.parked' ORDER BY at DESC LIMIT 1) pk ON true
  LEFT JOIN LATERAL (SELECT ms FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                        AND t.kind = 'page' AND t.status <> 'running' AND t.status <> 'skip'
                      ORDER BY at DESC LIMIT 1) last ON true
 WHERE s.upload_id = %s
 ORDER BY s.file_name, p.page_no"""

# every page waiting for a worker, over all batches, in the order it was sent: a page's place in the queue
QUEUE = """
SELECT p.batch_id, p.page_no,
       coalesce((SELECT max(at) FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                    AND t.kind = 'page.queued'), s.received_at) AS since
  FROM staging.page p JOIN staging.scan_batch s ON s.id = p.batch_id
 WHERE (p.status = 'queued' OR (p.status = 'rendered' AND s.status IN ('received', 'splitting')))
   AND NOT EXISTS (SELECT 1 FROM staging.trace t WHERE t.batch_id = p.batch_id AND t.page_no = p.page_no
                      AND t.kind = 'page' AND t.status = 'running')
 ORDER BY since, p.batch_id, p.page_no"""

PACE = """
SELECT ms, service, at > now() - interval '2 hours' AS recent FROM staging.trace
 WHERE kind = 'page' AND status = 'ok' AND at > now() - interval '1 day' AND ms IS NOT NULL
 ORDER BY at DESC LIMIT 200"""


def state(p, blocked=None):
    """Pure: one page's state, and the sentence for people. p: a row of PAGES (+ `ahead`); blocked: vf.blocked()."""
    again = bool(p.get("was_read"))
    if p.get("stuck"):
        v = stuck.view(p)
        return {"state": "failed", "text": v["reason"], **{k: v[k] for k in ("kind", "cause", "can_retry", "published",
                                                                              "error")}}
    if p["status"] == "rendered" and p.get("scan_status") not in ("received", "splitting"):   # sent within ms of a split
        return {"state": "idle", "text": "Belum dijadwalkan untuk dibaca."}
    if p["status"] in ("queued", "rendered"):
        if p.get("reading_since"):
            stage = (p.get("stage") or "").removeprefix("page.")
            return {"state": "reading", "again": again, "since": p["reading_since"],
                    "text": STAGE.get(stage, "AI membaca halaman")}
        parked = p.get("parked_at") and (not p.get("queued_at") or p["parked_at"] >= p["queued_at"])
        if blocked or parked:
            return {"state": "waiting", "again": again, "since": p.get("parked_at") or p.get("queued_at"),
                    "text": stuck.blocked_text(blocked or p.get("parked_why")) or "Menunggu AI bisa dipanggil lagi."}
        ahead = p.get("ahead")
        return {"state": "queued", "again": again, "since": p.get("queued_at"), "ahead": ahead,
                "text": ("Antre: halaman berikutnya" if not ahead else f"Antre: {ahead} halaman di depannya")}
    if p["status"] == "read" and p.get("outcome") == "waiting_ai":
        return {"state": "waiting_ai", "text": "Menunggu AI melihat ulang beberapa nilai. Berjalan sendiri."}
    return {"state": "done", "ms": p.get("last_ms")}


def pace(rows, per_worker=1):
    """Pure: (seconds a page usually takes, pages read at once) from the page spans of the last day: the median time,
    and the workers that read a page in the last two hours (each container names itself) times the pages each reads
    at once (WORKER_CONCURRENCY)."""
    ms = [r["ms"] for r in rows if r["ms"]]
    workers = len({r["service"] for r in rows if r.get("service") and r.get("recent")}) or DEFAULT_WORKERS
    return (statistics.median(ms) / 1000 if len(ms) >= 3 else DEFAULT_PAGE_S), workers * max(1, per_worker)


def eta(pages, page_s, workers, now):
    """Pure: seconds until every page of the batch is read, roughly: the pages before its last waiting one go
    first, `workers` at a time, and a page being read has its usual time minus what it has had."""
    waiting = [p for p in pages if p["state"] == "queued"]
    reading = [p for p in pages if p["state"] == "reading"]
    if not waiting and not reading:
        return None
    left = [max(page_s - (now - p["since"]).total_seconds(), 5) for p in reading if p.get("since")]
    longest = max(left, default=0)
    if waiting:
        last = max((p.get("ahead") or 0) for p in waiting) + 1          # pages up to and including its last one
        rounds = -(-last // workers)
        return int(max(longest, rounds * page_s + min(left, default=0)))
    return int(longest)


def of_upload(c, upload_id, blocked=None, now=None):
    """Every page of the batch with its state, the files still being split, and the batch's estimate."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    rows = [dict(r) for r in c.execute(PAGES, (upload_id,))]
    order = {(r["batch_id"], r["page_no"]): i for i, r in enumerate(c.execute(QUEUE))}
    from common import config
    page_s, workers = pace([dict(r) for r in c.execute(PACE)], config.WORKER_CONCURRENCY)
    out = []
    for r in rows:
        r["ahead"] = order.get((r["batch_id"], r["page_no"]))
        s = state(r, blocked)
        out.append({"batch_id": r["batch_id"], "page_no": r["page_no"], "file_name": r["file_name"],
                    "doc_type": r["doc_type"] if r["type_status"] in ("decided", "labelled") else None,
                    "unsure": r["type_status"] == "unsure" and not r["labelled"],
                    "thumb": r["thumb_upright_path"] or r["thumb_path"], **s})
    files = [dict(f) for f in c.execute("""SELECT id AS batch_id, file_name, status::text AS status, page_total, error
                                             FROM staging.scan_batch WHERE upload_id=%s
                                              AND status IN ('received', 'splitting')
                                            ORDER BY file_name""", (upload_id,))]
    return {"pages": out, "splitting": files, "eta_s": eta(out, page_s, workers, now) if not blocked else None,
            "page_s": round(page_s), "workers": workers}
