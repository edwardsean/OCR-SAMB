-- Satuan on PO and receipt rows (2026-10-09; the user: "satuan is how many pcs is for 1 kemasan or box"): how many
-- pieces one pack holds, as printed (CTN/72 → 72, CTN12 → 12, 1x6 → 6). Next to uom, which still says what the
-- quantity counts (CTN, PCS). Kept as read; empty when not printed. 008 can't add it: it refuses tables with rows.
ALTER TABLE satellite.doc_po_line ADD COLUMN IF NOT EXISTS satuan numeric(12,3);
ALTER TABLE satellite.doc_ttg_line ADD COLUMN IF NOT EXISTS satuan numeric(12,3);
COMMENT ON COLUMN satellite.doc_po_line.satuan IS '§6.1: Satuan';
COMMENT ON COLUMN satellite.doc_ttg_line.satuan IS '§6.1: Satuan';
