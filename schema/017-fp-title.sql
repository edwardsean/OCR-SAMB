-- vlm-first (vf database only): the printed title as an FP's third image witness (2026-09-28).
-- An FP needs Jev plus a witness from the image, never from the AI: the SOR QR code, the FP layout, or now the printed
-- title FAKTUR PENJUALAN found by Tesseract in the page's top 35%. Measured on 47 read pages: the title is found on
-- 14 of 15 FPs and on 0 of 32 other pages. Two FPs of 7000363700-03 (pages 1 and 10) have a QR code too faint to
-- decode and a layout score of 0.69 / 0.61, so Jev's FP 1.0 waited for a label.
ALTER TABLE staging.page
  ADD COLUMN IF NOT EXISTS fp_title boolean;   -- NULL = not looked for (only when Jev says FP and no other witness)

COMMENT ON COLUMN staging.page.fp_title IS
  'the printed title FAKTUR PENJUALAN, found by Tesseract in the top 35% (worker/vf.py fp_title); NULL = not looked for';
