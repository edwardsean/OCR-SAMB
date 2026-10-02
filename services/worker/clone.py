"""vlm-first: copy a batch's pages from v1's database into this one, WITHOUT anything v1 computed.

Copied: the batch row, each page's identity and the key of its original render (the same image v1 read), and
people's labels with their pile. Not copied: rotation, Tesseract text, type, fields, checks. vlm-first prepares,
reads, classifies and checks every page itself. v1's database is opened read-only.

  python -m worker.clone <batch_id> <pages, e.g. 1-31,48,52,57>
Safe to run twice (nothing is overwritten). It does not queue anything.
"""
import sys

import psycopg
from psycopg.rows import dict_row

from common import config, db


def pages_arg(spec):
    out = []
    for part in [x.strip() for x in spec.split(",") if x.strip()]:
        a, _, z = part.partition("-")
        out += list(range(int(a), int(z or a) + 1))
    return sorted(set(out))


def main_db():
    return psycopg.connect(config.required("MAIN_DATABASE_URL"), row_factory=dict_row)


def clone(batch_id, pages):
    with main_db() as m:
        b = m.execute("""SELECT id, file_name, file_path, sha256, scanned_day, page_total
                         FROM staging.scan_batch WHERE id=%s""", (batch_id,)).fetchone()
        if not b:
            raise SystemExit(f"no batch {batch_id} in v1's database")
        rows = m.execute("""SELECT page_no, image_path, original_path, thumb_path FROM staging.page
                            WHERE batch_id=%s AND page_no = ANY(%s) ORDER BY page_no""", (batch_id, pages)).fetchall()
        labels = m.execute("""SELECT page_no, label, customer, note, labelled_by, labelled_at, pile FROM staging.type_label
                              WHERE batch_id=%s AND page_no = ANY(%s)""", (batch_id, pages)).fetchall()
    with db.connect() as c:
        c.execute("""INSERT INTO staging.scan_batch (id, file_name, file_path, sha256, scanned_day, page_total, status, run)
                     VALUES (%(id)s, %(file_name)s, %(file_path)s, %(sha256)s, %(scanned_day)s, %(page_total)s, 'split', 1)
                     ON CONFLICT (id) DO NOTHING""", b)
        new_pages = 0
        for r in rows:
            new_pages += c.execute("""INSERT INTO staging.page (batch_id, page_no, image_path, original_path, thumb_path, status)
                                      VALUES (%s, %s, %s, %s, %s, 'rendered') ON CONFLICT (batch_id, page_no) DO NOTHING""",
                                   (batch_id, r["page_no"], r["image_path"], r["original_path"], r["thumb_path"])).rowcount
        new_labels = 0
        for l in labels:           # labels after pages: type_label references the page
            new_labels += c.execute("""INSERT INTO staging.type_label (batch_id, page_no, label, customer, note, labelled_by,
                                                                    labelled_at, pile)
                                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (batch_id, page_no) DO NOTHING""",
                                    (batch_id, l["page_no"], l["label"], l["customer"], l["note"], l["labelled_by"],
                                     l["labelled_at"], l["pile"])).rowcount
        c.execute("UPDATE staging.scan_batch SET pages_rendered=(SELECT count(*) FROM staging.page WHERE batch_id=%s) "
                  "WHERE id=%s", (batch_id, batch_id))
    return {"pages": len(rows), "new_pages": new_pages, "labels": len(labels), "new_labels": new_labels}


if __name__ == "__main__":
    print(clone(sys.argv[1], pages_arg(sys.argv[2])))
