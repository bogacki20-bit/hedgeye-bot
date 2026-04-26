"""
Portfolio Tracker
Ingests Fidelity CSV exports (Portfolio_Positions, History_for_Account,
Accounts_History) into SQLite, and exposes account rules + queries that
the trade recommender uses when a Hedgeye signal arrives.

Account rules (the bot enforces these when sizing recommendations):
  X96383748  Individual    long + short ETFs    no options    $5,000 margin buffer
  244859926  Rollover IRA  long-only ETFs       no options
  245734604  Roth IRA      long-only ETFs       no options

Usage:
  python portfolio.py <csv_or_dir> [<csv_or_dir> ...]
  python portfolio.py ~/Downloads/Portfolio_Positions_Apr-12-2026.csv
  python portfolio.py ~/Downloads/             # ingests every recognized file
"""

import csv
import hashlib
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from database import get_conn, init_db

log = logging.getLogger(__name__)

# ───── Account configuration ─────
INDIVIDUAL_ACCOUNT = "X96383748"
ROLLOVER_IRA       = "244859926"
ROTH_IRA           = "245734604"
MARGIN_BUFFER_USD  = 5_000.00

ACCOUNTS = {
    INDIVIDUAL_ACCOUNT: {
        "name":           "Individual",
        "long":           True,
        "short":          True,
        "options":        False,
        "etfs_only":      True,
        "margin_buffer":  MARGIN_BUFFER_USD,
        "hedgeye_target": True,   # Hedgeye signals get routed here by default
    },
    ROLLOVER_IRA: {
        "name":           "Rollover IRA",
        "long":           True,
        "short":          False,
        "options":        False,
        "etfs_only":      True,
        "margin_buffer":  0,
        "hedgeye_target": False,
    },
    ROTH_IRA: {
        "name":           "Roth IRA",
        "long":           True,
        "short":          False,
        "options":        False,
        "etfs_only":      True,
        "margin_buffer":  0,
        "hedgeye_target": False,
    },
}

CASH_SYMBOL_PATTERN = re.compile(r".+\*\*$")  # SPAXX**, CORE**, etc.
SKIP_SYMBOLS = {"Pending activity"}


# ───── CSV value parsers ─────

def _money(val):
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "--"):
        return None
    s = s.replace("$", "").replace(",", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def _pct(val):
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "--"):
        return None
    s = s.replace("%", "").replace("+", "")
    try:
        return float(s) / 100
    except ValueError:
        return None


def _num(val):
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def date_from_positions_filename(path) -> str:
    """Portfolio_Positions_Apr-12-2026.csv → '2026-04-12'."""
    m = re.search(r"([A-Z][a-z]{2}-\d{1,2}-\d{4})", Path(path).stem)
    if not m:
        return datetime.now().strftime("%Y-%m-%d")
    return datetime.strptime(m.group(1), "%b-%d-%Y").strftime("%Y-%m-%d")


def detect_file_type(path) -> str:
    name = Path(path).name
    if name.startswith("Portfolio_Positions_"):
        return "positions"
    if name.startswith("History_for_Account_") or name.startswith("Accounts_History"):
        return "transactions"
    return "unknown"


# ───── Positions ─────

def parse_positions(path):
    """Return (rows, snapshot_date) from a Portfolio_Positions_*.csv."""
    snapshot_date = date_from_positions_filename(path)
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            symbol = (r.get("Symbol") or "").strip()
            if not symbol or symbol in SKIP_SYMBOLS or CASH_SYMBOL_PATTERN.match(symbol):
                continue
            rows.append({
                "snapshot_date":   snapshot_date,
                "account_number":  (r.get("Account Number") or "").strip(),
                "account_name":    (r.get("Account Name") or "").strip(),
                "symbol":          symbol,
                "description":     (r.get("Description") or "").strip(),
                "quantity":        _num(r.get("Quantity")),
                "last_price":      _money(r.get("Last Price")),
                "current_value":   _money(r.get("Current Value")),
                "today_gl_dollar": _money(r.get("Today's Gain/Loss Dollar")),
                "today_gl_pct":    _pct(r.get("Today's Gain/Loss Percent")),
                "total_gl_dollar": _money(r.get("Total Gain/Loss Dollar")),
                "total_gl_pct":    _pct(r.get("Total Gain/Loss Percent")),
                "pct_of_account":  _pct(r.get("Percent Of Account")),
                "cost_basis":      _money(r.get("Cost Basis Total")),
                "avg_cost_basis":  _money(r.get("Average Cost Basis")),
                "account_type":    (r.get("Type") or "").strip(),
            })
    return rows, snapshot_date


def save_positions(rows, snapshot_date) -> int:
    """Replace any existing rows for snapshot_date and insert the new ones."""
    if not rows:
        return 0
    with get_conn() as conn:
        conn.execute("DELETE FROM portfolio_positions WHERE snapshot_date = ?", (snapshot_date,))
        conn.executemany("""
            INSERT INTO portfolio_positions
            (snapshot_date, account_number, account_name, symbol, description,
             quantity, last_price, current_value, today_gl_dollar, today_gl_pct,
             total_gl_dollar, total_gl_pct, pct_of_account, cost_basis,
             avg_cost_basis, account_type)
            VALUES
            (:snapshot_date, :account_number, :account_name, :symbol, :description,
             :quantity, :last_price, :current_value, :today_gl_dollar, :today_gl_pct,
             :total_gl_dollar, :total_gl_pct, :pct_of_account, :cost_basis,
             :avg_cost_basis, :account_type)
        """, rows)
    return len(rows)


# ───── Transactions ─────

def _txn_hash(r) -> str:
    key = "|".join(str(r.get(k, "")) for k in
                   ("run_date", "account", "symbol", "quantity", "price", "amount", "action"))
    return hashlib.sha256(key.encode()).hexdigest()


def parse_transactions(path):
    """Parse single- or multi-account Fidelity history CSV."""
    # Locate the header line (file has blank/legal lines before/after data)
    header_idx = None
    with open(path, encoding="utf-8-sig") as f:
        for i, line in enumerate(f):
            if line.startswith("Run Date,"):
                header_idx = i
                break
    if header_idx is None:
        log.warning(f"No header row in {path}")
        return []

    default_account = None
    m = re.search(r"History_for_Account_([A-Z0-9]+)", Path(path).name)
    if m:
        default_account = m.group(1)

    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for _ in range(header_idx):
            next(f)
        reader = csv.DictReader(f)
        for r in reader:
            run_date = (r.get("Run Date") or "").strip().lstrip()
            symbol   = (r.get("Symbol") or "").strip()
            action   = (r.get("Action") or "").strip()
            if not run_date or (not symbol and not action):
                continue
            # Skip legal-disclaimer lines that bleed past the data
            if len(run_date) > 20:
                continue

            row = {
                "run_date":        run_date,
                "account":         (r.get("Account") or default_account or "").strip().strip('"'),
                "action":          action,
                "symbol":          symbol,
                "description":     (r.get("Description") or r.get("Security Description") or "").strip(),
                "security_type":   (r.get("Type") or r.get("Security Type") or "").strip(),
                "quantity":        _num(r.get("Quantity")),
                "price":           _money(r.get("Price") or r.get("Price ($)")),
                "amount":          _money(r.get("Amount") or r.get("Amount ($)")),
                "commission":      _money(r.get("Commission") or r.get("Commission ($)")),
                "fees":            _money(r.get("Fees") or r.get("Fees ($)")),
                "settlement_date": (r.get("Settlement Date") or "").strip(),
                "source_file":     Path(path).name,
            }
            row["row_hash"] = _txn_hash(row)
            rows.append(row)
    return rows


def save_transactions(rows) -> int:
    if not rows:
        return 0
    inserted = 0
    with get_conn() as conn:
        for r in rows:
            try:
                conn.execute("""
                    INSERT INTO portfolio_transactions
                    (run_date, account, action, symbol, description, security_type,
                     quantity, price, amount, commission, fees, settlement_date,
                     source_file, row_hash)
                    VALUES
                    (:run_date, :account, :action, :symbol, :description, :security_type,
                     :quantity, :price, :amount, :commission, :fees, :settlement_date,
                     :source_file, :row_hash)
                """, r)
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # already ingested
    return inserted


# ───── Queries ─────

def latest_snapshot_date():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM portfolio_positions"
        ).fetchone()
        return row["d"] if row else None


def get_positions(account=None):
    """All positions in the latest snapshot, optionally filtered by account."""
    snap = latest_snapshot_date()
    if not snap:
        return []
    sql  = "SELECT * FROM portfolio_positions WHERE snapshot_date = ?"
    args = [snap]
    if account:
        sql += " AND account_number = ?"
        args.append(account)
    sql += " ORDER BY account_number, symbol"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_position_for(symbol, account=None):
    """Position rows for a symbol (may span cash/margin sub-rows)."""
    snap = latest_snapshot_date()
    if not snap:
        return []
    sql  = "SELECT * FROM portfolio_positions WHERE snapshot_date = ? AND symbol = ?"
    args = [snap, symbol.upper()]
    if account:
        sql += " AND account_number = ?"
        args.append(account)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def position_summary(symbol, account=None):
    """Aggregated view of a single symbol across cash/margin (and accounts)."""
    rows = get_position_for(symbol, account)
    if not rows:
        return None
    qty   = sum((r["quantity"]      or 0) for r in rows)
    val   = sum((r["current_value"] or 0) for r in rows)
    cost  = sum((r["cost_basis"]    or 0) for r in rows)
    pl    = sum((r["total_gl_dollar"] or 0) for r in rows)
    return {
        "symbol":        symbol.upper(),
        "shares":        qty,
        "current_value": val,
        "cost_basis":    cost,
        "total_gl":      pl,
        "total_gl_pct":  (pl / cost * 100) if cost else None,
        "accounts":      sorted({r["account_number"] for r in rows}),
        "last_price":    rows[0].get("last_price"),
    }


def account_value(account):
    """Sum of current_value across all positions in an account (latest snapshot)."""
    snap = latest_snapshot_date()
    if not snap:
        return 0.0
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(current_value), 0) AS total
            FROM portfolio_positions
            WHERE snapshot_date = ? AND account_number = ?
        """, (snap, account)).fetchone()
        return float(row["total"] or 0)


# ───── Trading rule check ─────

def can_trade(account, direction, instrument="ETF"):
    """
    Return (allowed, reason). `direction` is "long" or "short".
    Rules: no options anywhere; only Individual may short; ETFs only.
    """
    cfg = ACCOUNTS.get(account)
    if not cfg:
        return False, f"Unknown account {account}"

    instr = (instrument or "ETF").upper()
    dirn  = (direction  or "").lower()

    if instr == "OPTION" and not cfg["options"]:
        return False, f"Options trading disabled for {cfg['name']}"
    if cfg["etfs_only"] and instr not in ("ETF", "STOCK"):
        return False, f"{cfg['name']} restricted to ETFs"
    if dirn == "short" and not cfg["short"]:
        return False, f"{cfg['name']} is long-only"
    if dirn == "long" and not cfg["long"]:
        return False, f"{cfg['name']} cannot go long"
    return True, "OK"


def hedgeye_target_account(direction):
    """Default account for routing a Hedgeye signal."""
    # Hedgeye signals are tactical; route them to the active trading account.
    # (Shorts must go here anyway — IRAs are long-only.)
    return INDIVIDUAL_ACCOUNT


# ───── CLI ─────

def ingest(path):
    p = Path(path).expanduser()
    if p.is_dir():
        files = sorted(
            list(p.glob("Portfolio_Positions_*.csv")) +
            list(p.glob("History_for_Account_*.csv")) +
            list(p.glob("Accounts_History*.csv"))
        )
    else:
        files = [p]

    for f in files:
        kind = detect_file_type(f)
        if kind == "positions":
            rows, snap = parse_positions(f)
            n = save_positions(rows, snap)
            log.info(f"[{f.name}] positions: {n} rows for snapshot {snap}")
        elif kind == "transactions":
            rows = parse_transactions(f)
            n = save_transactions(rows)
            log.info(f"[{f.name}] transactions: {n} new (of {len(rows)} parsed)")
        else:
            log.warning(f"[{f.name}] unknown file type — skipped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        ingest(arg)
    log.info("Done.")
