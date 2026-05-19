"""Shared helper: maintain the unified `positions_snapshot` table.

The bot's live positions table is `portfolio_positions` (consumed by the
recommender / decision engine). `positions_snapshot` is a parallel,
analysis-facing rollup keyed strictly on (account, symbol, snapshot_date)
— it collapses Fidelity's Cash/Margin split into one row per symbol and
tags the data source. The Fidelity / Kinesis position importers populate
`portfolio_positions` as before and then mirror the just-imported
snapshot date into `positions_snapshot` via `sync()`.

Idempotent: CREATE TABLE IF NOT EXISTS + UNIQUE(account, symbol,
snapshot_date) with ON CONFLICT DO UPDATE — safe to re-run.
"""
from __future__ import annotations

_DDL = """
CREATE TABLE IF NOT EXISTS positions_snapshot (
    id              bigserial PRIMARY KEY,
    snapshot_date   date NOT NULL,
    account         text NOT NULL,
    account_name    text,
    symbol          text NOT NULL,
    normalized_symbol text,
    quantity        numeric,
    last_price      numeric,
    current_value   numeric,
    source          text,
    ingested_at     timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_positions_snapshot_natural
    ON positions_snapshot (account, symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_positions_snapshot_date
    ON positions_snapshot (snapshot_date);
"""


def ensure_table(cur) -> None:
    """Create positions_snapshot + indexes if absent (idempotent)."""
    cur.execute(_DDL)


def sync(cur, snapshot_date, source: str, crypto: bool) -> int:
    """Mirror one snapshot date from portfolio_positions into
    positions_snapshot.

    crypto=True  -> only account_type='Crypto' rows (Kinesis holdings)
    crypto=False -> everything except account_type='Crypto' (Fidelity)

    Fidelity keeps a symbol in both Cash and Margin sub-accounts; we sum
    quantity / current_value and take the max last_price so the natural
    key (account, symbol, snapshot_date) stays unique. Returns rowcount.
    """
    ensure_table(cur)
    acct_filter = ("COALESCE(account_type,'') = 'Crypto'" if crypto
                   else "COALESCE(account_type,'') <> 'Crypto'")
    cur.execute(
        f"""
        INSERT INTO positions_snapshot
            (snapshot_date, account, account_name, symbol,
             normalized_symbol, quantity, last_price, current_value,
             source, ingested_at)
        SELECT snapshot_date,
               account_number,
               MAX(account_name),
               symbol,
               MAX(normalized_symbol),
               SUM(quantity),
               MAX(last_price),
               SUM(current_value),
               %s,
               NOW()
          FROM portfolio_positions
         WHERE snapshot_date = %s
           AND {acct_filter}
         GROUP BY snapshot_date, account_number, symbol
        ON CONFLICT (account, symbol, snapshot_date) DO UPDATE SET
            account_name      = EXCLUDED.account_name,
            normalized_symbol = EXCLUDED.normalized_symbol,
            quantity          = EXCLUDED.quantity,
            last_price        = EXCLUDED.last_price,
            current_value     = EXCLUDED.current_value,
            source            = EXCLUDED.source,
            ingested_at       = NOW()
        """,
        (source, snapshot_date),
    )
    return cur.rowcount or 0
