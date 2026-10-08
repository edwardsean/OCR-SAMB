-- Many pages at once (2026-10-08, the throughput work): every AI call first counts today's calls of its model under a
-- lock (worker/vf.py reserve, the daily cap). With thousands of calls a day that count needs its index, or every call
-- of every page waits on a scan of the whole ledger.
CREATE INDEX IF NOT EXISTS model_call_model_day ON staging.model_call (model, pacific_day);
