-- read-then-map (2026-10-01): the status bar after a fix (the user: "a status bar so the user knows what process is
-- going on after the fix"). A lesson is 'teaching' while the teacher writes its tip; a tip's test and its apply to
-- the stored pages report their progress on its knowledge_page row. Nothing here decides anything: the learning
-- runs the same with or without it (worker/learn.py writes progress best-effort).
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'staging.extract_example'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%lesson_status%' LOOP
    EXECUTE format('ALTER TABLE staging.extract_example DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;
ALTER TABLE staging.extract_example ADD CONSTRAINT extract_example_lesson_status_check CHECK (lesson_status IN
    ('waiting', 'teaching', 'proposed', 'learned', 'already_right', 'no_change', 'needs_pages', 'failed'));

ALTER TABLE staging.knowledge_page
  ADD COLUMN IF NOT EXISTS progress jsonb;   -- {step: testing | tested | applying | applied, done, of, changed, at}
COMMENT ON COLUMN staging.knowledge_page.progress IS
  'what the teacher is doing with this version now: testing it on stored pages, or applying it (for the status bar)';
