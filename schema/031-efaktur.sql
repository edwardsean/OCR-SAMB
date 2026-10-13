-- e-Faktur PDFs (2026-10-12, the user: "they are e-faktur PDFs, actually when faktur pajak is there you dont need to send
-- more data, just attach the faktur pajak in the document"). A Faktur Pajak arrives after its order was posted in SAP,
-- as the tax office's PDF with its text inside, so it is never read by the AI: its text says which order it belongs to
-- ("7000363545/SOR26110267792", billing number / SOR), and it is attached to that order's PDF. Nothing else is sent.
--
-- status: waiting  the SOR or its billing number isn't in Satellite yet (tried again by itself)
--         held     Satellite's billing number for that SOR is another one: a person looks
--         linked   its order is known; it goes into the order's PDF when the order is sent
--         attached in the order's sent PDF (attached_version = that PDF's version)
CREATE TABLE IF NOT EXISTS staging.efaktur (
  id               bigserial PRIMARY KEY,
  sha256           text NOT NULL UNIQUE,
  upload_id        bigint REFERENCES staging.upload (id),
  file_name        text NOT NULL,
  file_path        text NOT NULL,                   -- the PDF as it came, in storage
  pages            integer NOT NULL,
  received_at      timestamptz NOT NULL DEFAULT now(),
  billing_no       text NOT NULL,
  sor_no           text NOT NULL,
  kode_seri        text,
  buyer            text,
  dpp              numeric(18,2),
  ppn              numeric(18,2),
  status           text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'held', 'linked', 'attached')),
  why              text,
  checked_at       timestamptz,
  attached_version integer,
  attached_at      timestamptz
);
CREATE INDEX IF NOT EXISTS efaktur_sor ON staging.efaktur (sor_no);
CREATE INDEX IF NOT EXISTS efaktur_open ON staging.efaktur (status) WHERE status IN ('waiting', 'held');
CREATE INDEX IF NOT EXISTS efaktur_upload ON staging.efaktur (upload_id);
