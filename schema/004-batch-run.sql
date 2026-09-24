-- Re-running a batch must not double-process pages whose old tickets are still queued.
-- Every ticket carries the batch's run number; workers drop tickets from an older run.
ALTER TABLE staging.scan_batch ADD COLUMN IF NOT EXISTS run integer NOT NULL DEFAULT 1;
