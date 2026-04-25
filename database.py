"""
Database Layer
SQLite storage for scraped items, signals, and state tracking.
"""

import sqlite3
import json
import logging
import os
from datetime import date, datetime

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/hedgeye.db")

# Ensure directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id              TEXT PRIMARY KEY,
            title           TEXT,
            subject         TEXT,
            body            TEXT,
            full_content    TEXT,
            source          TEXT,
            timestamp       TEXT,
            classified_type TEXT,
            summary         TEXT,
            macro_regime    TEXT,
            market_tone     TEXT,
            vol_regime      TEXT,
            systematic_flow TEXT,
            spx_support     REAL,
            spx_resistance  REAL,
            action_required INTEGER DEFAULT 0,
            author          TEXT,
            raw_json        TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id         TEXT REFERENCES items(id),
            ticker          TEXT NOT NULL,
            direction       TEXT,
            conviction      TEXT,
            sector          TEXT,
            asset_class     TEXT,
            thesis          TEXT,
            timestamp       TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS morning_briefs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_date  TEXT UNIQUE,
            sent_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_items_type      ON items(classified_type);
        CREATE INDEX IF NOT EXISTS idx_items_timestamp ON items(timestamp);
        CREATE INDEX IF NOT EXISTS idx_signals_ticker  ON signals(ticker);
        """)
    log.info(f"Database initialized at {DB_PATH}")


def save_item(item: dict):
    """Save a classified item and its signals to the database."""
    spx = item.get("spx_levels") or {}

    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO items
                (id, title, subject, body, full_content, source, timestamp,
                 classified_type, summary, macro_regime, market_tone,
                 vol_regime, systematic_flow, spx_support, spx_resistance,
                 action_required, author, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item.get("id"),
                item.get("title"),
                item.get("subject"),
                item.get("body","")[:2000],
                item.get("full_content","")[:8000],
                item.get("source"),
                item.get("timestamp"),
                item.get("classified_type"),
                item.get("summary"),
                item.get("macro_regime"),
                item.get("market_tone"),
                item.get("vol_regime"),
                item.get("systematic_flow"),
                spx.get("support"),
                spx.get("resistance"),
                1 if item.get("action_required") else 0,
                item.get("author"),
                json.dumps(item)
            ))

            # Save individual ticker signals
            tickers = item.get("tickers") or []
            for t in tickers:
                if not t.get("ticker"):
                    continue
                conn.execute("""
                    INSERT INTO signals
                    (item_id, ticker, direction, conviction, sector, asset_class, thesis, timestamp)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    item.get("id"),
                    t.get("ticker"),
                    t.get("direction", "Long"),
                    t.get("conviction"),
                    t.get("sector"),
                    t.get("asset_class"),
                    t.get("thesis"),
                    item.get("timestamp")
                ))

        log.debug(f"Saved item: {item.get('id')}")

    except Exception as e:
        log.error(f"DB save error: {e}")


def get_seen_ids() -> set:
    """Return set of all item IDs already in database (portal scrape)."""
    init_db()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM items WHERE source = 'portal_scrape'"
            ).fetchall()
            return {row["id"] for row in rows}
    except Exception as e:
        log.error(f"get_seen_ids error: {e}")
        return set()


def get_seen_email_ids() -> set:
    """Return set of all email item IDs already processed."""
    init_db()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM items WHERE source = 'email'"
            ).fetchall()
            return {row["id"] for row in rows}
    except Exception as e:
        log.error(f"get_seen_email_ids error: {e}")
        return set()


def was_morning_brief_sent(brief_date: date) -> bool:
    """Check if morning brief was already sent today."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM morning_briefs WHERE brief_date = ?",
                (brief_date.isoformat(),)
            ).fetchone()
            return row is not None
    except Exception as e:
        log.error(f"was_morning_brief_sent error: {e}")
        return False


def mark_morning_brief_sent(brief_date: date):
    """Record that morning brief was sent today."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO morning_briefs (brief_date) VALUES (?)",
                (brief_date.isoformat(),)
            )
    except Exception as e:
        log.error(f"mark_morning_brief_sent error: {e}")


def get_recent_signals(days: int = 7) -> list[dict]:
    """Return recent trade signals."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT s.*, i.summary, i.macro_regime, i.market_tone
                FROM signals s
                JOIN items i ON s.item_id = i.id
                WHERE s.created_at >= datetime('now', ?)
                ORDER BY s.created_at DESC
            """, (f"-{days} days",)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"get_recent_signals error: {e}")
        return []


# Initialize on import
init_db()
