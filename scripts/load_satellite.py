"""Load Satellite's sales-order export into satellite.sor and satellite.sor_item (vlm-first database only).

The export is real customer data: it stays on this machine (*.csv is git-ignored) and goes only into the pipeline's
database (PIPELINE_DB).
Three files, from the user (2026-09-25):
  HEADER-1-bulan-kebelakang.csv          one row per SO: customer, invoice amounts, billing no, CGR, status
  ITEM-1-bulan-kebelakang.csv            one row per SO line (joined to HEADER by order_id)
  Header-1-bulan-po-customer-number.csv  SOR -> SG sales-order no -> the customer's PO number (the FP's Nomor CPO)
Re-running replaces every SO it contains and all SO lines. SOs in the PO file but not in HEADER are skipped (no
customer). Afterwards: python -m grouper.group <batch> --recheck.

  docker compose run --rm -v <folder with the CSVs>:/data/so:ro -v ./scripts:/scripts:ro rtm-api \
      python /scripts/load_satellite.py /data/so
"""
import csv
import json
import os
import sys
import time

import psycopg

csv.field_size_limit(sys.maxsize)
DISCOUNTS = ("reg", "dc", "b2b", "dmg", "new", "go", "special", "lsb", "promo", "additional")


def num(s):
    s = (s or "").strip()
    return float(s) if s else None


def day(s):
    s = (s or "").strip()
    return s[:10] if s else None


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def main(folder):
    t0 = time.time()
    po = {r["order_no"].strip(): r for r in rows(os.path.join(folder, "Header-1-bulan-po-customer-number.csv"))}
    sor_of, header = {}, []
    for h in rows(os.path.join(folder, "HEADER-1-bulan-kebelakang.csv")):
        sor = h["order_no"].strip()
        sor_of[h["order_id"]] = sor
        p = po.get(sor) or {}
        header.append((sor, h["customer_id"].strip(), h["customer_name"].strip(), (p.get("po_number") or "").strip() or None,
                       day(h["created_at"]), num(h["inv_grand_total_amount"]), (p.get("sales_order_no") or "").strip() or None,
                       num(h["inv_subtotal_after_discount_amount"]), num(h["inv_vat_amount"]), num(h["vat_percentage"]),
                       h["sap_bill_doc_no"].strip() or None, h["cgr_doc_no"].strip() or None, day(h["cgr_doc_date"]),
                       day(h["posting_date"]), h["document_status"].strip() or None,
                       # the SO as ordered: what its FP printed (a tolakan later changes only the inv_ amounts)
                       num(h["subtotal_after_discount_amount"]), num(h["vat_amount"]), num(h["grand_total_amount"]),
                       h["ship_to_parent_customer_id"].strip() or None))
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        with c.cursor() as cur:
            cur.execute("CREATE TEMP TABLE sor_in (LIKE satellite.sor INCLUDING DEFAULTS) ON COMMIT DROP")
            cols = ("sor_no", "customer_code", "customer_name", "cpo_no", "tgl_so", "total", "so_no", "dpp", "ppn",
                    "vat_pct", "billing_no", "cgr_no", "cgr_date", "posting_date", "status", "order_dpp", "order_ppn",
                    "order_total", "customer_parent")
            with cur.copy(f"COPY sor_in ({', '.join(cols)}) FROM STDIN") as cp:
                for r in header:
                    cp.write_row(r)
            cur.execute(f"""
                INSERT INTO satellite.sor ({', '.join(cols)}, loaded_at)
                SELECT {', '.join(cols)}, now() FROM sor_in
                ON CONFLICT (sor_no) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in cols[1:])},
                                                   loaded_at = now()""")
            cur.execute("TRUNCATE satellite.sor_item")
            items, skipped = {}, 0
            for r in rows(os.path.join(folder, "ITEM-1-bulan-kebelakang.csv")):
                sor = sor_of.get(r["order_id"])
                if not sor:
                    skipped += 1
                    continue
                disc = {k: {"type": r[f"disc_{k}_type"], "value": num(r[f"disc_{k}_value"]),
                            "amount": num(r[f"disc_{k}_amount"])}
                        for k in DISCOUNTS if num(r.get(f"disc_{k}_amount")) or num(r.get(f"disc_{k}_value"))}
                items[(sor, int(r["line_no"]))] = (   # a repeated (SO, line) keeps its last row
                    sor, int(r["line_no"]), r["item_code"].strip(), r["item_description"].strip(),
                    r["ordered_uom_id"].strip(), num(r["conversion_factor"]), num(r["ordered_qty"]),
                    num(r["ordered_base_qty"]), num(r["price_list_amount"]), num(r["base_price_list_amount"]),
                    json.dumps(disc), num(r["total_discount_amount"]), num(r["subtotal_amount"]),
                    num(r["total_amount"]), num(r["vat_amount"]), num(r["invoice_base_qty"]),
                    num(r["invoice_nett_amount"]), num(r["cgr_base_qty"]), num(r["cgr_rejected_base_qty"]),
                    r["cgr_reject_reason"].strip() or None)
            icols = ("sor_no", "line_no", "item_code", "description", "uom", "pcs_per_uom", "qty_uom", "qty_pcs",
                     "price_uom", "price_pcs", "discounts", "discount_total", "gross", "line_amount", "vat",
                     "invoice_qty", "invoice_amount", "cgr_qty", "rejected_qty", "reject_reason")
            with cur.copy(f"COPY satellite.sor_item ({', '.join(icols)}) FROM STDIN") as cp:
                for row in items.values():
                    cp.write_row(row)
            n_item = len(items)
    print(f"{len(header)} SOs ({sum(1 for r in header if r[3])} with a customer PO number) and {n_item} SO lines loaded; "
          f"{skipped} lines without their SO; {len(set(po) - {r[0] for r in header})} SOs only in the PO file "
          f"(skipped) · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1])
