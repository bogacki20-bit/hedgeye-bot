"""signal_store.py — the first-class Postgres store for the paper-signal
program (operator spec 8/29 round 2). ARCHITECTURE RULE: these tables ARE
the store; CSVs only ever leave via tools.study_dump, for scoring.

Tables (all created idempotently by ensure_tables):

  signal_paper_fires   ONE table for every paper signal ever tested —
                       keith long/short variants, composite, flush, future
                       ideas. Detectors append here nightly. PK
                       (signal_name, variant, ticker, fire_date); variant
                       is '' for signals without variants (PK columns must
                       be NOT NULL). features jsonb carries the per-signal
                       payload so new signals need no DDL.

  signal_controls      Persisted control draws so scoring reproduces
                       forever (seed recorded per row; canonical seed
                       20260829). signal_name distinguishes generations
                       (e.g. keith_long_fired_only vs keith_long_full).

  trend_daily          As-of trend per name per day, whole universe — the
                       POST-RR-OVERLAY trend the KEITH detector evaluates
                       (tools.keith_pattern.build_series authority order).
                       Materialized nightly + backfilled from stored
                       inputs; a NULL trend row = polled that day, trend
                       unknown (declared, not hidden).

Nothing here alerts, trades, or touches REPORT or the live entry/exit
path. Append-only by convention; upserts exist so reruns refresh rather
than duplicate.
"""
from __future__ import annotations

import json

import psycopg2.extras

DEFAULT_SEED = 20260829

_DDL = """
CREATE TABLE IF NOT EXISTS signal_paper_fires (
    signal_name text NOT NULL,
    variant     text NOT NULL DEFAULT '',
    ticker      text NOT NULL,
    fire_date   date NOT NULL,
    features    jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_name, variant, ticker, fire_date)
);
CREATE INDEX IF NOT EXISTS ix_signal_paper_fires_date
    ON signal_paper_fires (fire_date);

CREATE TABLE IF NOT EXISTS signal_controls (
    signal_name text NOT NULL,
    ticker      text NOT NULL,
    date        date NOT NULL,
    seed        bigint NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_name, ticker, date)
);

CREATE TABLE IF NOT EXISTS trend_daily (
    ticker text NOT NULL,
    date   date NOT NULL,
    trend  text,
    PRIMARY KEY (ticker, date)
);
"""


def ensure_tables(cur) -> None:
    cur.execute(_DDL)


def upsert_fires(cur, signal_name: str, rows) -> int:
    """rows: iterable of (variant, ticker, fire_date, features_dict).
    Upsert (refresh features on conflict). Returns rows written."""
    rows = [(signal_name, v or "", t, d, json.dumps(f, default=str))
            for v, t, d, f in rows]
    if not rows:
        return 0
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO signal_paper_fires
               (signal_name, variant, ticker, fire_date, features)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (signal_name, variant, ticker, fire_date)
           DO UPDATE SET features = EXCLUDED.features""",
        rows, page_size=500)
    return len(rows)


def upsert_controls(cur, signal_name: str, picks, seed: int = DEFAULT_SEED) -> int:
    """picks: iterable of (ticker, date)."""
    rows = [(signal_name, t, d, seed) for t, d in picks]
    if not rows:
        return 0
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO signal_controls (signal_name, ticker, date, seed)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (signal_name, ticker, date) DO NOTHING""",
        rows, page_size=500)
    return len(rows)


def upsert_trend(cur, rows) -> int:
    """rows: iterable of (ticker, date, trend_or_None)."""
    rows = list(rows)
    if not rows:
        return 0
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO trend_daily (ticker, date, trend)
           VALUES (%s, %s, %s)
           ON CONFLICT (ticker, date) DO UPDATE SET trend = EXCLUDED.trend""",
        rows, page_size=1000)
    return len(rows)
