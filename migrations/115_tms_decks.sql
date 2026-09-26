-- 115: The Macro Show slide deck — the WHOLE daily pack (desk 9/26: "its the
-- whole pack"). The deck is posted after the 9:00 show behind the "TODAY'S
-- SLIDES" link in the ~08:30 "Access Show, Slide Deck & Top 3 Things" email
-- (app.hedgeye.com/mu/tms_... -> signed S3 PDF). One row per show date.
--
-- Section order is fixed; lengths vary (Chart Callouts ran 9 pages on 9/25,
-- 24 on 7/24). parser_tms_deck.py splits on the divider pages.
--   top3        Keith's Top 3 Things  — text layer, no vision needed
--   process     Our Process           — p10 "What Are The Quads?" is the LIVE
--                                       GIP model (Updated: date, GDP/CPI
--                                       actuals + estimates, quad path); the
--                                       rest is boilerplate, hashed for change
--   callouts    Chart Callouts        — one-off charts: title/levels/summary
--   grind       Macro Grind           — fixed stat tables (prior close)
--   volatility  Volatility            — fixed vol tables/series boxes
-- Tables are images: values are read off page PNGs and sanity-checked.
-- Chart-line reads are listed in raw->'approx_fields'.
--
-- The quad model's estimate quarters are ALSO written to hedgeye_quad_stated
-- (product 'Macro Show Deck', scope 'quarterly', note_date = the model's
-- Updated date). Nothing here writes quad_regime_history: QUAD: stays the
-- only input for the confirmed regime (tools/quad_regime.py).
--
-- Apply: py apply_migration.py migrations/115_tms_decks.sql
-- Reversible: DROP TABLE tms_decks.
CREATE TABLE IF NOT EXISTS tms_decks (
    show_date           DATE PRIMARY KEY,
    data_date           DATE,          -- close the Macro Grind tables use
    deck_file           TEXT,
    source_email_id     TEXT,
    page_count          INT,
    sections            JSONB,         -- {"top3": [4,5,6], "process": [...], ...}
    top3                JSONB,         -- [{n, topic, text}]
    process_text        JSONB,         -- {"<page>": {"sha": s, "text": s}}
    quad_model_updated  DATE,
    quad_model          JSONB,         -- {updated, quarters:[...], path:[...]}
    sector_perf         JSONB,         -- [{sector, ticker, price, d1, mtd, qtd, ytd}]
    sector_rel          JSONB,         -- same, relative to SPX
    volume              JSONB,         -- [{metric, vs_prior_day, vs_1m, vs_3m, vs_1y}]
    style_factors       JSONB,         -- [{group, factor, d1, w1, m1, m3, m6, ytd}]
    usd_corr            JSONB,         -- [{metric, d15, d30, d90, d120, d180, hi_52w, lo_52w, pct_pos, pct_neg}]
    cftc                JSONB,         -- [{group, metric, latest, wow, avg_3m, avg_6m, avg_1y, max_3y, min_3y, z_1y, z_3y}]
    ivol                JSONB,         -- [{section, name, ticker, ytd_ret, ivol_prem, ...}]
    macro_vol           JSONB,         -- [{index, last, d1, w1, m1, label}]
    series              JSONB,         -- [{page_title, series, last, d1_ago, w1_ago, w4_ago, ...}]
    callouts            JSONB,         -- [{page, title, source, levels, summary}]
    extra_pages         JSONB,         -- pages whose title no key knows yet
    raw                 JSONB,
    parsed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tms_decks_quad_updated ON tms_decks (quad_model_updated);
