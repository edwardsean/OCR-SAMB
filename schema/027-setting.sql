-- read-then-map (2026-10-06): settings saved from the Teknis screen "Model & kunci API". The mentor asked for the
-- models (image OCR, text model, teachers) and their API keys to be set from the UI. A row here overrides the same
-- name in .env; no row = the .env value. Every service reads this table again every few seconds (common/settings.py).
-- API keys are stored as typed (like .env keeps them in a file); screens show only their last 4 characters.
CREATE TABLE IF NOT EXISTS staging.setting (
  name        text PRIMARY KEY,               -- the .env name it overrides, e.g. VF_AI_MAP, DASHSCOPE_API_KEY
  value       text NOT NULL,
  updated_by  text NOT NULL,                  -- who saved it, as typed
  updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE staging.setting IS 'model and API-key settings saved from the UI; each overrides the same name in .env';
