-- 061_report_snapshots.sql — structured state per REPORT build (v4 Δ-header).
--
-- Each v4 REPORT stores a small JSONB state blob (⚠ flag set, sector rp map,
-- SS drops touching the book) so the NEXT report can print "Δ since last:
-- 2 new ⚠ · 1 SS drop affects book · XLV flow -0.31". Report infrastructure
-- (like report_rows) — NOT a signal table; nothing downstream reads it.

BEGIN;

CREATE TABLE IF NOT EXISTS report_snapshots (
    id          BIGSERIAL   PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT        NOT NULL,          -- 'on-demand' | 'eod'
    state       JSONB       NOT NULL           -- {flags:[], sector_rp:{}, ss_book_drops:[]}
);

CREATE INDEX IF NOT EXISTS ix_report_snap_created ON report_snapshots (created_at);

COMMIT;
