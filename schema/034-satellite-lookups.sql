-- Satellite's orders read with indexed queries, not kept in every process's memory (2026-10-17: the mentor's 8 GB
-- server held 418K orders; every page worker, the grouper and the API kept its own ~4.8 GB copy, and the kernel killed
-- them). common/satellite.py DbLookup asks only what a page needs: the order with this SOR, the orders with this
-- Nomor CPO, the ones one character away, the ones whose printed total could be this amount, an order's lines.
--
-- Keys are compared as satellite.flat() makes them (uppercase, letters and digits only); FLAT below is the same
-- expression, written exactly as satellite.FLAT_SQL builds it, so the planner uses these indexes.
-- "One character away" without a scan: two strings at most one edit apart share their first n//2 characters or their
-- last n - n//2 (the edit sits in one half), so a prefix LIKE and a reversed-suffix LIKE find every candidate.
-- Indexes only: no column changes (tables.py, the feed's columns and a bundle's fingerprint stay as they are).
-- Safe to run again. On a server, run it while no batch is reading: each index takes seconds on 418K orders.

CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- contrib, in the postgres image: the step-3 "closest orders" list

CREATE INDEX IF NOT EXISTS sor_cpo_flat
    ON satellite.sor ((regexp_replace(upper(coalesce(cpo_no, '')), '[^0-9A-Z]', '', 'g')) text_pattern_ops);
CREATE INDEX IF NOT EXISTS sor_cpo_flat_rev
    ON satellite.sor ((reverse(regexp_replace(upper(coalesce(cpo_no, '')), '[^0-9A-Z]', '', 'g'))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS sor_cpo_flat_trgm
    ON satellite.sor USING gin ((regexp_replace(upper(coalesce(cpo_no, '')), '[^0-9A-Z]', '', 'g')) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS sor_sor_flat
    ON satellite.sor ((regexp_replace(upper(coalesce(sor_no, '')), '[^0-9A-Z]', '', 'g')) text_pattern_ops);
CREATE INDEX IF NOT EXISTS sor_sor_flat_rev
    ON satellite.sor ((reverse(regexp_replace(upper(coalesce(sor_no, '')), '[^0-9A-Z]', '', 'g'))) text_pattern_ops);
CREATE INDEX IF NOT EXISTS sor_sor_flat_trgm
    ON satellite.sor USING gin ((regexp_replace(upper(coalesce(sor_no, '')), '[^0-9A-Z]', '', 'g')) gin_trgm_ops);

-- the total an FP printed (satellite.paper): order_total when Satellite has it; the few without are found by the
-- invoice total or their lines' sums
CREATE INDEX IF NOT EXISTS sor_order_total ON satellite.sor (order_total);
CREATE INDEX IF NOT EXISTS sor_no_order_total ON satellite.sor (total) WHERE order_total IS NULL;

ANALYZE satellite.sor;
