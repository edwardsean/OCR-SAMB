"""Re-decide every page's type with the current rules, reusing the stored Jev answer (no new API calls).
Recomputes the title-word vote from the stored OCR words.  python -m worker.reclassify <batch_id>"""
import sys

from psycopg.types.json import Json

from common import db
from worker import classify

bid = sys.argv[1]
with db.connect() as c:
    rows = c.execute("""SELECT page_no, type_votes, ocr_words, quality_flags, rotation FROM staging.page
                        WHERE batch_id=%s AND type_votes IS NOT NULL""", (bid,)).fetchall()
changed = 0
for r in rows:
    v = dict(r["type_votes"])
    v.pop("second_try", None)
    h = 2480 if r["rotation"] in (90, 270) else 3509
    v["keyword"] = classify.keyword_vote(r["ocr_words"] or [], h)
    status, doc_type, guess, reason = classify.decide(v, r["quality_flags"] or [])
    v["reason"] = reason
    with db.connect() as c:
        old = c.execute("""UPDATE staging.page SET type_status=%s, doc_type=%s, type_guess=%s, type_votes=%s, classify_version=%s
                           WHERE batch_id=%s AND page_no=%s
                           RETURNING (SELECT doc_type FROM staging.page WHERE batch_id=%s AND page_no=%s) AS before""",
                        (status, doc_type, guess, Json(v), classify.CLASSIFY_VERSION, bid, r["page_no"], bid, r["page_no"])).fetchone()
print(f"re-decided {len(rows)} pages with classify v{classify.CLASSIFY_VERSION}")
