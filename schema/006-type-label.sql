-- People's answers to "what is this page?" — the learning-from-corrections loop starts here.
-- Each label is put in the practice pile or the exam pile ONCE, at random, when first saved, and never moves:
--   practice (80%): the AI that proposes better Jev descriptions may study these
--   exam     (20%): locked away; only used to check that an approved change really works on unseen pages
CREATE TABLE IF NOT EXISTS staging.type_label (
  batch_id    text NOT NULL,
  page_no     integer NOT NULL,
  label       doc_type NOT NULL,
  customer    text,
  note        text,
  labelled_by text,
  labelled_at timestamptz NOT NULL DEFAULT now(),
  pile        text NOT NULL CHECK (pile IN ('practice', 'exam')),
  PRIMARY KEY (batch_id, page_no),
  FOREIGN KEY (batch_id, page_no) REFERENCES staging.page (batch_id, page_no)
);
COMMENT ON COLUMN staging.type_label.pile IS
  'Assigned once at random (20% exam). Relabelling a page changes the label, never the pile.';
