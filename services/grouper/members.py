"""An order's documents from every scan (the user, 2026-10-05: each of a customer's documents can arrive as its own
file, and in real life a receipt comes back days after its invoice, in another scan).

Grouping links each document to its order scan by scan (grouper/group.py plan). Everything that looks at a whole
order (whether its FP is there, its checks, Review, publishing) gathers the order's documents from every scan here.

Within one order its pages are numbered by `keys`: an order whose documents sit in one scan keeps that scan's page
numbers (nothing changes for it); an order spread over several scans numbers the FP's scan's pages as they are, and
each other scan's pages SPAN apart (10003 = page 3 of the second scan). `where` says which scan and page each is.
"""
SPAN = 10000


def order_batches(rows):
    """The scans an order's documents sit in, in a fixed order: the FP's scan first, then by when each scan came in."""
    seen = {}
    for r in rows:
        b = seen.setdefault(r["batch_id"], {"fp": False, "at": r.get("received_at"), "id": r["batch_id"]})
        b["fp"] = b["fp"] or r["t"] == "FP"
    return [b["id"] for b in sorted(seen.values(), key=lambda b: (not b["fp"], str(b["at"] or ""), b["id"]))]


def keys(rows):
    """({(batch, page): key}, {key: {"batch", "page", "scan"}}) for an order's documents ([{batch_id, t, page_from,
    page_to, file_name?}])."""
    order = {b: i for i, b in enumerate(order_batches(rows))}
    to_key, where = {}, {}
    for r in rows:
        for n in range(r["page_from"], r["page_to"] + 1):
            k = order[r["batch_id"]] * SPAN + n
            to_key[(r["batch_id"], n)] = k
            where[k] = {"batch": r["batch_id"], "page": n, "scan": r.get("file_name") or r["batch_id"]}
    return to_key, where


def name(k, where):
    """How a page is named in a reason: its page number, and its scan's file when the order spans several scans."""
    w = where.get(k)
    if not w:
        return str(k)
    many = len({x["batch"] for x in where.values()}) > 1
    return f"{w['page']} ({w['scan']})" if many else str(w["page"])


def of_bundle(c, bundle_id):
    """The bundle's documents from every scan, each with its scan's file name and arrival, in order (the FP's scan
    first, then by arrival, then by page)."""
    rows = [dict(r) for r in c.execute(
        """SELECT d.id, d.batch_id, d.doc_type::text AS t, d.page_from, d.page_to, d.linked_by::text AS linked_by,
                  s.file_name, s.received_at
             FROM staging.bundle_document bd JOIN staging.document d ON d.id = bd.document_id
             JOIN staging.scan_batch s ON s.id = d.batch_id
            WHERE bd.bundle_id = %s""", (bundle_id,))]
    order = {b: i for i, b in enumerate(order_batches(rows))}
    return sorted(rows, key=lambda r: (order[r["batch_id"]], r["page_from"]))


def hold_of(types):
    """The order-level hold, over every scan: no FP among its documents, or more than one."""
    n = sum(1 for t in types if t == "FP")
    return "fp_missing" if n == 0 else "two_fps_one_sor" if n > 1 else None
