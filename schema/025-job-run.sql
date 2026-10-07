-- The scheduler (services/scheduler/serve.py) replaces n8n's schedules (the mentor, 2026-10-02: n8n struggles with
-- thousands of records and several workers). One row per periodic job: a scheduler takes a job's turn with one UPDATE
-- (last_started older than the job's interval), so it runs once per interval however many schedulers run, and a
-- restart never runs it again early. The Status page shows the last run of each.
CREATE TABLE IF NOT EXISTS staging.job_run (
  name          text PRIMARY KEY,                 -- intake, notify, sweep, lint
  last_started  timestamptz,
  last_finished timestamptz,
  runs          integer NOT NULL DEFAULT 0,
  last_result   jsonb,                            -- what the job did (sent, new notice, …)
  last_error    text                              -- why the last run failed; NULL when it didn't
);
