-- Phase 7d: the Review screen. Safe to re-run.
--
-- A person's confirmation of a line cell names its row by the row's own key (the customer's code, or SAMB's on an FP;
-- "#2" for a second row with the same code), not by its position: a later reading may list the rows in another order.
-- The field is then "lines[<key>].<column>" (e.g. lines[3078035].qty). `shown` keeps what the screen showed beside the
-- form (the AI's reading), so it is known what the person looked at.
ALTER TABLE staging.field_confirmation
  ADD COLUMN IF NOT EXISTS row_key text,
  ADD COLUMN IF NOT EXISTS shown   text;

-- A difference a person accepted on Review, with its reason ("rounding", "tolakan confirmed", ...). It holds only
-- while the check says exactly what it said when accepted (input_print): new readings or new Satellite data drop it.
CREATE TABLE IF NOT EXISTS staging.bundle_decision (
  sor_no       text NOT NULL,
  check_name   text NOT NULL,                 -- fp_po_total, received, ... (grouper/crosscheck.py)
  input_print  text NOT NULL,                 -- the check's result when it was accepted
  reason       text NOT NULL,
  note         text,
  decided_by   text NOT NULL,
  decided_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (sor_no, check_name)
);
