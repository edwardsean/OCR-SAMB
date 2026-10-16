"""Deleting an uploaded batch, so the same PDF can be uploaded again (the user, 2026-10-16: "add a delete batch button
so that i can retry to upload the same document again; think when can we delete, when can we not").

A batch (staging.upload) is deleted whole: its files, pages, documents, readings, and the people's work done ON its
pages (type labels, confirmed values, row pairings, Review accepts stay: they are keyed by order and check, not page).
Its PDFs and page images leave storage. A file's SHA-256 is what refuses a second upload of it, so once its row is
gone the same file can be uploaded again.

Can't delete:
  - an order with a document of this batch was SENT to Satellite (its rows and PDF were made from these pages), or an
    e-Faktur of this batch is already inside a sent order's PDF;
  - something of it still runs or waits for the AI (being split, pages queued or being read, waiting for a
    look-again): stop it first (common/stopping.py);
  - a stopped file's page still finishes its last AI call (its page lock is held).
Kept, because it isn't the batch's: what was learned from it (the classification context, knowledge tips, the
product-code map, each customer's calibration), Jejak's history and the AI-call ledger (money spent stays spent).
Orders: one made only of this batch's documents goes; one with documents from other batches too loses this batch's
documents and is regrouped (it may then wait for a document again; an approval goes, as with any change)."""
from common import db, stopping, storage

PUBLISHED = """SELECT DISTINCT b.sor_no FROM staging.document d
                 JOIN staging.scan_batch s ON s.id = d.batch_id
                 JOIN staging.bundle_document bd ON bd.document_id = d.id
                 JOIN staging.bundle b ON b.id = bd.bundle_id
                WHERE s.upload_id = %s AND b.status = 'published' ORDER BY 1"""


class Refused(Exception):
    def __init__(self, why, status=409):
        super().__init__(why)
        self.why, self.status = why, status


def why_not(c, upload_id):
    """Why this batch can't be deleted now (for people), else None. Raises Refused(404) when it doesn't exist."""
    u = c.execute("SELECT id FROM staging.upload WHERE id=%s", (upload_id,)).fetchone()
    if not u:
        raise Refused("Batch itu tidak ada.", 404)
    sent = [r["sor_no"] for r in c.execute(PUBLISHED, (upload_id,))]
    if sent:
        return (f"{len(sent)} order sudah dikirim ke Satellite ({', '.join(sent[:3])}{', …' if len(sent) > 3 else ''}): "
                "datanya dibuat dari halaman batch ini.")
    if c.execute("SELECT 1 FROM staging.efaktur WHERE upload_id=%s AND status='attached' LIMIT 1", (upload_id,)).fetchone():
        return "e-Faktur batch ini sudah masuk PDF order yang dikirim."
    for s in c.execute("SELECT * FROM staging.scan_batch WHERE upload_id=%s", (upload_id,)).fetchall():
        if s["status"] == stopping.STOPPED:
            if stopping.finishing(c, s["id"]):
                return "Ada halaman yang masih menyelesaikan panggilan AI terakhirnya. Coba lagi sebentar lagi."
        elif stopping.running(c, s):
            return "Batch ini masih berjalan. Hentikan dulu, lalu hapus."
    return None


def impact(c, upload_id):
    """What deleting would take away, for the confirmation: {files, pages, orders: gone with it, shared: orders that
    only lose documents, approved: approvals undone, labels, confirmations}."""
    r = c.execute(
        """WITH f AS (SELECT id FROM staging.scan_batch WHERE upload_id=%(u)s),
                mine AS (SELECT DISTINCT bd.bundle_id FROM staging.document d
                           JOIN staging.bundle_document bd ON bd.document_id = d.id WHERE d.batch_id IN (SELECT id FROM f)),
                shared AS (SELECT DISTINCT bd.bundle_id FROM staging.bundle_document bd JOIN staging.document d
                             ON d.id = bd.document_id
                            WHERE bd.bundle_id IN (SELECT bundle_id FROM mine) AND d.batch_id NOT IN (SELECT id FROM f))
           SELECT (SELECT count(*) FROM f) AS files,
                  (SELECT count(*) FROM staging.page WHERE batch_id IN (SELECT id FROM f)) AS pages,
                  (SELECT count(*) FROM mine WHERE bundle_id NOT IN (SELECT bundle_id FROM shared)) AS orders,
                  (SELECT count(*) FROM shared) AS shared,
                  (SELECT count(*) FROM staging.bundle WHERE id IN (SELECT bundle_id FROM mine) AND status='reviewed') AS approved,
                  (SELECT count(*) FROM staging.type_label WHERE batch_id IN (SELECT id FROM f)) AS labels,
                  (SELECT count(*) FROM staging.field_confirmation WHERE batch_id IN (SELECT id FROM f)) AS confirmations,
                  (SELECT count(*) FROM staging.efaktur WHERE upload_id=%(u)s) AS efaktur""",
        {"u": upload_id}).fetchone()
    return dict(r)


def view(c, upload_id):
    """For the batch page: {can, why_not, impact, code}."""
    why = why_not(c, upload_id)
    code = c.execute("SELECT code FROM staging.upload WHERE id=%s", (upload_id,)).fetchone()["code"]
    return {"can": why is None, "why_not": why, "impact": impact(c, upload_id), "code": code}


def _keys(c, upload_id):
    """Every storage object of the batch: its PDFs, each page's images, its e-Faktur files."""
    keys = set()
    for s in c.execute("SELECT id, file_path FROM staging.scan_batch WHERE upload_id=%s", (upload_id,)):
        keys.add(s["file_path"])
        for p in c.execute("""SELECT image_path, original_path, upright_path, clean_path, thumb_path, thumb_upright_path
                                FROM staging.page WHERE batch_id=%s""", (s["id"],)):
            keys.update(p.values())
    keys.update(r["file_path"] for r in c.execute("SELECT file_path FROM staging.efaktur WHERE upload_id=%s", (upload_id,)))
    return sorted(k for k in keys if k)


def delete(upload_id, by, code):
    """Delete one batch (see the module). `code` must be the batch's code, typed by the person. Returns {code, files,
    pages, orders, regrouped: other files whose orders changed, storage: objects removed}, or raises Refused."""
    from common import trace
    from grouper import group
    by = (by or "").strip()
    with db.connect() as c:
        u = c.execute("SELECT * FROM staging.upload WHERE id=%s FOR UPDATE", (upload_id,)).fetchone()
        if not u:
            raise Refused("Batch itu tidak ada.", 404)
        if (code or "").strip().upper() != (u["code"] or "").upper():
            raise Refused(f"Ketik kode batch {u['code']} untuk menghapusnya.", 400)
        files = [r["id"] for r in c.execute("SELECT id FROM staging.scan_batch WHERE upload_id=%s FOR UPDATE",
                                            (upload_id,))]
        why = why_not(c, upload_id)
        if why:
            raise Refused(why)
        what = impact(c, upload_id)
        keys = _keys(c, upload_id)
        bundles = [r["bundle_id"] for r in c.execute(
            """SELECT DISTINCT bd.bundle_id FROM staging.document d JOIN staging.bundle_document bd ON bd.document_id = d.id
                WHERE d.batch_id = ANY(%s)""", (files,))]
        others = [r["batch_id"] for r in c.execute(
            """SELECT DISTINCT d.batch_id FROM staging.bundle_document bd JOIN staging.document d ON d.id = bd.document_id
                WHERE bd.bundle_id = ANY(%s) AND NOT (d.batch_id = ANY(%s))""", (bundles, files))]
        c.execute("""DELETE FROM staging.bundle_document bd USING staging.document d
                      WHERE d.id = bd.document_id AND d.batch_id = ANY(%s)""", (files,))
        c.execute("DELETE FROM staging.document WHERE batch_id = ANY(%s)", (files,))
        c.execute("""DELETE FROM staging.bundle b WHERE b.id = ANY(%s) AND b.status <> 'published'
                       AND NOT EXISTS (SELECT 1 FROM staging.bundle_document bd WHERE bd.bundle_id = b.id)""", (bundles,))
        for t in ("line_match", "extract_example", "field_check", "type_label", "lesson", "field_confirmation",
                  "reading_trial", "knowledge_map"):
            c.execute(f"DELETE FROM staging.{t} WHERE batch_id = ANY(%s)", (files,))
        c.execute("DELETE FROM staging.page WHERE batch_id = ANY(%s)", (files,))
        c.execute("DELETE FROM staging.scan_batch WHERE id = ANY(%s)", (files,))
        c.execute("DELETE FROM staging.efaktur WHERE upload_id=%s", (upload_id,))
        c.execute("DELETE FROM staging.upload WHERE id=%s", (upload_id,))
    removed = _remove(keys, files)
    for b in others:                                  # orders that kept documents from other files: grouped again
        group.run(b)
    trace.event("upload.deleted", "ok", who=by, upload=upload_id, code=u["code"], files=len(files), pages=what["pages"],
                orders=what["orders"], shared=what["shared"] or None, storage=removed)
    return {"code": u["code"], "files": len(files), "pages": what["pages"], "orders": what["orders"],
            "regrouped": others, "storage": removed}


def _remove(keys, files):
    """The batch's objects, and its pages' copies in the order folders ({PREFIX}bundles/…/<file>-pNNN-…, <file>.json).
    After the database: an object left behind is harmless, a row pointing at a removed object is not."""
    from common.intake import PREFIX
    c, bucket, n = storage.client(), storage.bucket(), 0
    names = list(keys) + [o.object_name for o in c.list_objects(bucket, prefix=f"{PREFIX}bundles/", recursive=True)
                          if any(f"/{f}-p" in o.object_name or o.object_name.endswith(f"/{f}.json") for f in files)]
    for name in names:
        try:
            c.remove_object(bucket, name)
            n += 1
        except Exception as e:                        # already gone, or storage refused: the rows are gone anyway
            print(f"delete: could not remove {name}: {e}", flush=True)
    return n
