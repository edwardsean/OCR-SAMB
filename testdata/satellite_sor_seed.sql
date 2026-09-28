-- STAND-IN for the Satellite sales-order export (the user will provide the real one; then replace these rows).
-- Seeded 2026-09-25 at the user's request, from the sample's Faktur Penjualan pages read BY EYE (the same way the
-- answer key was made): Sales Order [SO] #, Kepada [customer code] + name, Nomor CPO, Tgl SO.
-- It holds only what Satellite itself would hold (an SO and the customer PO it was created from), nothing about
-- pages, types or bundles. Pipeline code may read satellite.sor; it must never read this file.
--
-- Uncertain: SOR26110256810 (page 8, very faint): the Nomor CPO's first digit is broken into dots. 5213349 is what
-- its TTG (page 9) prints; the other Hari Hari pair (pages 6-7) has FP CPO = TTG PO, but pages 1-2 do not.
--
-- Apply to vlm-first only:  docker exec -i samb-ocr-postgres-1 psql -U ocr -d ocr_vf -f - < testdata/satellite_sor_seed.sql
INSERT INTO satellite.sor (sor_no, customer_code, customer_name, cpo_no, tgl_so) VALUES
  ('SOR26110245292', '1400001602', 'HARI HARI BINTARO TANGSEL',         '5190721',    '2026-08-26'),  -- p1
  ('SOR26110255837', '1400000454', 'BOOTS HARAPAN INDAH AVENUE',        '4505832724', '2026-09-04'),  -- p3
  ('SOR26110256585', '1400001602', 'HARI HARI BINTARO TANGSEL',         '5213310',    '2026-09-04'),  -- p6
  ('SOR26110256810', '1400001602', 'HARI HARI BINTARO TANGSEL',         '5213349',    '2026-09-05'),  -- p8 (CPO uncertain)
  ('SOR26110257250', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58423526',   '2026-09-07'),  -- p10
  ('SOR26110257253', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58423525',   '2026-09-07'),  -- p13
  ('SOR26110257255', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58415558',   '2026-09-07'),  -- p17
  ('SOR26110257256', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58424054',   '2026-09-07'),  -- p20
  ('SOR26110257257', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58415552',   '2026-09-07'),  -- p23
  ('SOR26110257258', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58415556',   '2026-09-07'),  -- p26
  ('SOR26110257259', '1400001889', 'HERO DC PBF [320] CIBITUNG BEKASI', '58415554',   '2026-09-07')   -- p29
ON CONFLICT (sor_no) DO UPDATE SET customer_code = EXCLUDED.customer_code, customer_name = EXCLUDED.customer_name,
                                   cpo_no = EXCLUDED.cpo_no, tgl_so = EXCLUDED.tgl_so;
