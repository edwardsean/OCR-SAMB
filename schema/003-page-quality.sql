-- Phase 2: enhancement + classical OCR results per page.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS upright_path       text,           -- rotated upright + straightened, as scanned otherwise
  ADD COLUMN IF NOT EXISTS clean_path         text,           -- the variant Tesseract read best (1x, for display)
  ADD COLUMN IF NOT EXISTS thumb_upright_path text,
  ADD COLUMN IF NOT EXISTS osd_conf           numeric(6,2),   -- Tesseract orientation confidence
  ADD COLUMN IF NOT EXISTS skew_angle         numeric(5,2),   -- degrees corrected
  ADD COLUMN IF NOT EXISTS dark_band_ratio    numeric(4,3),   -- share of rows that are solid black
  ADD COLUMN IF NOT EXISTS speckle_ratio      numeric(4,3),   -- share of ink blobs that are tiny dots: high = faint/dotted print
  ADD COLUMN IF NOT EXISTS ocr_variant        text,           -- which preprocessing won
  ADD COLUMN IF NOT EXISTS variant_scores     jsonb,          -- every variant's score, so the choice is visible
  ADD COLUMN IF NOT EXISTS ocr_conf           numeric(5,2),   -- mean Tesseract word confidence of the winner
  ADD COLUMN IF NOT EXISTS confident_chars    integer,        -- characters in words read with confidence >= 70
  ADD COLUMN IF NOT EXISTS ocr_words          jsonb,          -- [[text, conf, x, y, w, h], ...] in upright-image pixels
  ADD COLUMN IF NOT EXISTS quality_flags      text[] NOT NULL DEFAULT '{}',  -- rotated · skewed · dark_band · faint · poor_quality
  ADD COLUMN IF NOT EXISTS qr_text            text,           -- decoded QR payload when the page has one
  ADD COLUMN IF NOT EXISTS error              text,
  ADD COLUMN IF NOT EXISTS ms_enhance_ocr     integer;
CREATE INDEX IF NOT EXISTS page_flags ON staging.page USING gin (quality_flags);
