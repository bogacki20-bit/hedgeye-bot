-- 054_wrapper_links.sql — underlying-linkage table + no-mapping set for wrapper ETFs.
--
-- Inverse/levered single-name & index ETFs (METD->META, SQQQ->QQQ, …) map to their
-- underlying so screens/flip-watch read the underlying's signal (inverted where flagged).
-- Populated ONLY via operator CONFIRM (the unmapped-wrapper detector proposes; nothing
-- auto-writes). Rejected candidates go to wrapper_no_mapping so they stop nagging.

CREATE TABLE IF NOT EXISTS wrapper_links (
    wrapper            text PRIMARY KEY,         -- the fund ticker (METD)
    underlying         text NOT NULL,            -- the tracked name/index (META)
    inverse            boolean NOT NULL DEFAULT false,
    leverage           numeric,                  -- magnitude: 1, 1.5, 2, 3 (NULL = unknown)
    fund_class         text,                     -- equity | index | currency | crypto
    note               text,
    source_description text,                     -- the Fidelity description it was confirmed from
    confirmed_at       timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wrapper_no_mapping (
    ticker       text PRIMARY KEY,               -- dismissed: "own exposure", basket, etc.
    reason       text,
    dismissed_at timestamptz DEFAULT now()
);
