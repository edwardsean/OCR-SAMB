"""What a page or a file is stuck on, why in plain words, and whether a person may try it again (the user, 2026-10-08:
"if a workflow fails, there is no retry button … if the data is published to satellite already, it cant retry").

A page is stuck when nothing will finish it by itself soon:
  crashed      its worker died on it, or the queue gave up (status dead_letter / failed);
  call_failed  a model call kept failing after the automatic tries (status read, and its reading failed, or its
               look-again, store question or learned tips wait on "the call failed"); the sweep tries it once more
               every few hours, and a person may try it now.
A page waiting for a daily limit, or for a model to be set, is never stuck: it waits in the waiting room and goes on
by itself (worker/vf.py blocked). A file is stuck when it couldn't be split into pages (scan_batch.status failed).

A person may try a stuck page again, except
  - when its order is already sent to Satellite: Satellite keeps those values, and reading the page again would leave
    two truths (taking an order back is a developer's `python -m publisher.publish --undo <SOR>`);
  - while the page is queued: the system is already trying it.
A retry only redoes what failed: the AI's copy of a page is kept the moment it is paid for (vf.read_then_map)."""
import re

CRASHED = "p.status IN ('dead_letter', 'failed')"
CALL_FAILED = ("(p.status = 'read' AND (p.extract_status = 'failed' "
               "OR strpos(coalesce(p.second_look->>'waiting', ''), 'the call failed') > 0))")
STUCK = f"({CRASHED} OR {CALL_FAILED})"
PUBLISHED = """EXISTS (SELECT 1 FROM staging.document d JOIN staging.bundle_document bd ON bd.document_id = d.id
                         JOIN staging.bundle b ON b.id = bd.bundle_id
                        WHERE d.batch_id = p.batch_id AND p.page_no BETWEEN d.page_from AND d.page_to
                          AND b.status = 'published')"""
WHERE = "Teknis → Model & kunci API"


def kind(status, extract_status, waits):
    """Pure: 'crashed', 'call_failed' or None (not stuck)."""
    if status in ("dead_letter", "failed"):
        return "crashed"
    if status == "read" and (extract_status == "failed" or "the call failed" in (waits or "")):
        return "call_failed"
    return None


def error_of(row):
    """The raw error a stuck page carries (for IT): the worker's, the reading's, or what the look-again waits on."""
    return row.get("error") or (row.get("extract_error") if row.get("extract_status") == "failed" else None) \
        or row.get("waits")


def cause(error):
    """Pure: (cause, a sentence for people) from a raw error. cause: setting | connection | answer | other."""
    e = error or ""
    if re.search(r"HTTP (400|401|403|404)\b|refused the API key|no API key|NotSet|isn't set|no model", e):
        return "setting", f"Pengaturan model bermasalah (alamat, model atau kunci API). Periksa {WHERE}, lalu coba lagi."
    if re.search(r"connect|ssl|timeout|timed out|eof|HTTP 5\d\d|unavailable after retries", e, re.I):
        return "connection", "Koneksi ke layanan AI terputus atau terlalu lama."
    if re.search(r"JSONDecodeError|no JSON|not an option number|garbled", e):
        return "answer", "Jawaban AI tidak bisa dipakai."
    return "other", "Halaman ini gagal dibaca."


def blocked_text(why):
    """vf.blocked()'s reason for people: no page can be tried now, and pages go on by themselves later."""
    if not why:
        return None
    if why.startswith("NotSet"):
        return f"Model belum diatur: halaman menunggu dan lanjut sendiri setelah diatur di {WHERE}."
    if why.startswith("DailyLimit"):
        m = re.search(r"(\d+) min left", why)
        return "Batas AI dari penyedia sedang berlaku: halaman lanjut sendiri" + (f" dalam ±{m.group(1)} menit." if m else ".")
    if why.startswith("OutOfBudget"):
        return "Kuota AI harian sistem ini sudah habis: halaman lanjut sendiri besok."
    return "AI sedang tidak bisa dipanggil: halaman lanjut sendiri."


def view(row):
    """A stuck page for the screen: {kind, cause, reason, published, can_retry, error} (row: the page's status,
    extract_status, extract_error, error, waits, published)."""
    k = kind(row.get("status"), row.get("extract_status"), row.get("waits"))
    err = error_of(row)
    c, reason = cause(err) if k else (None, None)
    if k == "crashed" and c == "other":
        reason = "Pemrosesan halaman ini berhenti di tengah jalan."
    return {"kind": k, "cause": c, "reason": reason, "published": bool(row.get("published")),
            "can_retry": bool(k) and not row.get("published"), "error": err}
