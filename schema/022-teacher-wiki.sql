-- read-then-map, Stage 3 (2026-09-30): the teacher writes the wiki. Each practice-pile example is a lesson: the teacher
-- (Z.ai GLM, text, on vf-teacher) reads it with the page's copy and the customer's section and proposes one claim; the
-- replay gate decides. The example row is the lesson's state (a wake-up message is only a wake-up). Exam-pile examples
-- are never lessons (lesson_status stays NULL).
ALTER TABLE staging.extract_example
  ADD COLUMN IF NOT EXISTS lesson_status text CHECK (lesson_status IN
      ('waiting', 'proposed', 'learned', 'already_right', 'no_change', 'needs_pages', 'failed')),
  ADD COLUMN IF NOT EXISTS lesson       jsonb,      -- the teacher's answers, the gates' reasons, tries
  ADD COLUMN IF NOT EXISTS lesson_doc   text,       -- the proposal it made: knowledge_page (lesson_doc, lesson_version)
  ADD COLUMN IF NOT EXISTS lesson_version int,
  ADD COLUMN IF NOT EXISTS lesson_at    timestamptz;
UPDATE staging.extract_example SET lesson_status = 'waiting'
 WHERE pile = 'practice' AND status = 'active' AND lesson_status IS NULL;
CREATE INDEX IF NOT EXISTS extract_example_lessons ON staging.extract_example (made_at) WHERE lesson_status = 'waiting';

-- the lint takes a contradicted claim out by itself (a page's version with source 'lint', active at once)
ALTER TABLE staging.knowledge_page DROP CONSTRAINT IF EXISTS knowledge_page_source_check;
ALTER TABLE staging.knowledge_page ADD CONSTRAINT knowledge_page_source_check
  CHECK (source IN ('marked', 'typed', 'person', 'teacher', 'lint'));
