-- 101: hedgeye_rerank (T6) — the Daily ETF Re-Rank conviction list.
-- Source: "Macro ETFs by Rank:" text line (order = rank) plus Keith's
-- Commentary PA moves. The rank TABLE (asset class, sizing bands,
-- position %) ships as a deck image — asset_class / sizing_band_* /
-- position_pct stay NULL until someone transcribes the images; sizes
-- are explicit in text only as commentary deltas (move_bps) and
-- full exits / adds-at-minimum (action).
CREATE TABLE IF NOT EXISTS hedgeye_rerank (
    note_date      date NOT NULL,
    ticker         text NOT NULL,
    rank           int,                -- position in the rank line; NULL for
                                       -- commentary-only rows (e.g. sold all)
    asset_class    text,
    sizing_band_lo numeric,
    sizing_band_hi numeric,
    position_pct   numeric,
    move_bps       int,                -- signed commentary delta
    action         text,               -- 'sold_all' | 'add_min' | NULL
    source_uid     text,
    PRIMARY KEY (note_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_rerank_ticker ON hedgeye_rerank (ticker, note_date);
