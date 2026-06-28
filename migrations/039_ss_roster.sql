-- 039_ss_roster.sql — Priority 0 step 1: canonical Signal Strength roster + history.
-- Design: docs/ss_self_updating_roster.md
--
-- STEP 1 ONLY. Creates + seeds the tables. NOTHING reads them yet (active_slice
-- repoint is step 2), so this is behavior-neutral and reversible:
--   DROP VIEW ss_roster_current; DROP TABLE ss_roster_anchor; DROP TABLE ss_roster_history;
-- Idempotent: IF NOT EXISTS + seed guarded on an empty table, so re-applying is safe.

CREATE TABLE IF NOT EXISTS ss_roster_history (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL,
    added_on      DATE NOT NULL,
    removed_on    DATE,                 -- NULL = currently on the roster
    add_source    TEXT NOT NULL,        -- 'seed' | 'anchor' | 'delta'
    remove_source TEXT,                 -- 'anchor' | 'delta'
    anchor_id     BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One OPEN stint per ticker; also the fast "current roster" read path.
CREATE UNIQUE INDEX IF NOT EXISTS ss_roster_one_open
    ON ss_roster_history (ticker) WHERE removed_on IS NULL;

CREATE TABLE IF NOT EXISTS ss_roster_anchor (
    id            BIGSERIAL PRIMARY KEY,
    anchor_date   DATE NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker_count  INT NOT NULL,
    tickers       JSONB NOT NULL,
    roster_before INT,
    diff_added    JSONB,
    diff_removed  JSONB,
    note          TEXT
);

CREATE OR REPLACE VIEW ss_roster_current AS
    SELECT ticker, added_on, add_source
      FROM ss_roster_history
     WHERE removed_on IS NULL;

-- Seed from the current 80-name roster (effective 2026-06-26). Guarded so re-applying
-- never double-seeds.
INSERT INTO ss_roster_history (ticker, added_on, add_source)
SELECT t, DATE '2026-06-26', 'seed'
  FROM unnest(ARRAY[
    'ADDYY','AFRM','AMAT','APLE','ATRO','AXP','BBWI','BJRI','BRKR','BROS',
    'BRUN','BTI','CAKE','CBRL','CCL','CFG','CHEF','COMP','CRWD','CSX',
    'CZR','DDOG','DHI','DLTR','ETSY','EXP','GLASF','HD','HLT','HST',
    'ILMN','JPM','KDP','KMX','KTB','KXIAY','LAMR','LFST','LLY','LRCX',
    'LYV','MAR','MGM','MNST','MRVL','NVO','OC','OKTA','OMF','OUT',
    'PANW','PCAR','PRMB','RCL','REAL','RH','RHP','RKT','ROK','RRGB',
    'RSI','SBUX','SG','SJM','SN','SNOW','SWBI','SYF','TGT','TOL',
    'TT','TTWO','TXG','UNH','URI','VIK','VSXY','WRBY','WST','XYZ'
  ]) AS t
 WHERE NOT EXISTS (SELECT 1 FROM ss_roster_history);
