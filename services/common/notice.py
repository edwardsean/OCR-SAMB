"""Notices: bundles that newly need a person (vlm-first, Stage 2; schema/018-notice.sql).

  The scheduler's "notify" job (scheduler/serve.py, every NOTIFY_EVERY_MINUTES) calls record. A bundle needs a person
  when its status is needs_review; it is new when no earlier notice had it with the same fingerprint (a bundle that
  was approved and then changed needs a person again). The web app shows unseen notices as a count on the Periksa
  order tab and a list on its screen, which marks them seen once a browser shows it. Only in the web app for now
  (the user, 2026-09-29).
"""
from psycopg.types.json import Json


def new_items(current, known):
    """Pure. current: [{batch, sor, fingerprint, customer, reasons}] needing a person now; known: {(sor, fingerprint)}
    already in a notice. Returns the ones no notice had yet."""
    return [b for b in current if (b["sor"], b["fingerprint"] or "") not in known]


def text_of(items):
    """One line: '3 bundles need you: AEON EASTVARA TANGERANG (SOR26110264129), …'."""
    names = [f"{b['customer'] or 'Unknown customer'} ({b['sor']})" for b in items]
    head = f"{len(items)} bundle{'' if len(items) == 1 else 's'} need{'s' if len(items) == 1 else ''} you: "
    return head + ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")


def record(c):
    """One notice for the bundles that newly need a person, or None when there are none."""
    current = [dict(r) for r in c.execute("""
        SELECT DISTINCT ON (b.sor_no) d.batch_id AS batch, b.sor_no AS sor, coalesce(b.fingerprint, '') AS fingerprint,
               s.customer_name AS customer, jsonb_array_length(coalesce(b.checks->'reasons', '[]')) AS reasons
          FROM staging.bundle b JOIN staging.bundle_document bd ON bd.bundle_id = b.id
          JOIN staging.document d ON d.id = bd.document_id
          LEFT JOIN satellite.sor s ON s.sor_no = b.sor_no
         WHERE b.status = 'needs_review' AND b.sor_no IS NOT NULL
         ORDER BY b.sor_no, d.page_from""")]
    known = {(r["sor"], r["fingerprint"]) for r in c.execute(
        "SELECT i->>'sor' AS sor, coalesce(i->>'fingerprint', '') AS fingerprint "
        "FROM staging.notice, jsonb_array_elements(items) i")}
    items = new_items(current, known)
    if not items:
        return None
    return c.execute("INSERT INTO staging.notice (items, text) VALUES (%s, %s) RETURNING id, text",
                     (Json(items), text_of(items))).fetchone()


def unseen(c):
    """The bundles in unseen notices that still need a person, newest notice first: [{batch, sor, customer, at}]."""
    return [dict(r) for r in c.execute("""
        SELECT DISTINCT ON (i->>'sor') i->>'batch' AS batch, i->>'sor' AS sor, i->>'customer' AS customer, n.at
          FROM staging.notice n, jsonb_array_elements(n.items) i
          JOIN staging.bundle b ON b.sor_no = i->>'sor' AND b.status = 'needs_review'
         WHERE n.seen_at IS NULL
         ORDER BY i->>'sor', n.at DESC""")]


def mark_seen(c):
    c.execute("UPDATE staging.notice SET seen_at = now() WHERE seen_at IS NULL")
