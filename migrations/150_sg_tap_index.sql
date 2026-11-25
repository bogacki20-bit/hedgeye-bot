-- 11/24/2026 (Kris: "I want to capture all the data in all the products ... a robust data set that we can clean up"):
-- tools/sg_tap.js keeps every answer a SpotGamma page loads, whole, in data/sg_tap/<date>/sg_tap_*.jsonl.gz.
-- This is the index of those raw files: one row per kept answer. tools/sg_tap.py creates it too.
CREATE TABLE IF NOT EXISTS sg_tap_index (
    captured_at timestamptz NOT NULL, host text NOT NULL, path text NOT NULL, q text NOT NULL DEFAULT '',
    sym text, page text, status int, ctype text, b64 boolean, bytes int, file text NOT NULL, line int NOT NULL,
    PRIMARY KEY (file, line)
);
CREATE INDEX IF NOT EXISTS sg_tap_index_path_time ON sg_tap_index (path, captured_at);
CREATE INDEX IF NOT EXISTS sg_tap_index_sym_time ON sg_tap_index (sym, captured_at);
