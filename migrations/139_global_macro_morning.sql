-- 139: morning global macro read, one row per day (Kris 2026-11-15). Written by tools/global_macro_morning.py.
BEGIN;
CREATE TABLE IF NOT EXISTS global_macro_morning (
    report_date date PRIMARY KEY,
    headline    text,
    body        text,
    created_at  timestamptz DEFAULT now()
);
COMMIT;
