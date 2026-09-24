"""Re-check every extracted page with the current phase-5 rules, from what is stored (no model calls, no re-reading).
Also recomputes the grouping keys.   python -m worker.reverify <batch_id> [pages, e.g. 1-31]"""
import sys

from psycopg.types.json import Json

from common import db, verify
from common import keys as keymod

bid = sys.argv[1]
sel = []
for part in (sys.argv[2].split(",") if len(sys.argv) > 2 else []):
    a, _, z = part.partition("-")
    sel += list(range(int(a), int(z or a) + 1))
with db.connect() as c:
    rows = c.execute("""SELECT page_no, doc_type::text AS doc_type, fields, classical_text, qr_text FROM staging.page
                        WHERE batch_id=%s AND extract_status='done' AND (%s OR page_no = ANY(%s))""",
                     (bid, not sel, sel)).fetchall()
    for r in rows:
        checks = verify.run(r["doc_type"], r["fields"], r["classical_text"], r["qr_text"])
        k = keymod.derive(r["doc_type"], r["fields"], r["classical_text"], r["qr_text"])
        c.execute("UPDATE staging.page SET verify_version=%s, keys=%s WHERE batch_id=%s AND page_no=%s",
                  (verify.VERIFY_VERSION, Json(k), bid, r["page_no"]))
        verify.store(c, bid, r["page_no"], r["fields"], checks)
print(f"checked {len(rows)} pages with verify v{verify.VERIFY_VERSION}")
