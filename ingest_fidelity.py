#!/usr/bin/env python3
"""
ingest_fidelity.py  --  parse Fidelity CSV exports into book_positions / book_activity.

Discipline (matches the rest of the bot):
  * Python owns ALL arithmetic and parsing. No LLM. No silent guesses.
  * DRY-RUN by default. Nothing touches Postgres unless you pass --commit.
  * Loud failure over silent drift: unparseable rows and sanity-check failures
    raise / warn instead of being swallowed.

SNAPSHOT SEMANTICS (changed 2026-07-29 — operator: "the bot is not updating my
positions in the reports"):
  * A positions write REPLACES the snapshot rather than merging into it —
    delete-then-insert in ONE transaction — because the broker is truth and a
    position you CLOSED has to leave the book. Upsert-only left closed names in
    book_positions forever on any same-day re-upload; they kept counting in
    BOOK/CONC/fills and kept firing alerts.
  * The delete is SCOPED to the accounts present in the file. Fidelity exports
    whatever accounts are in view, so a single-account download must not wipe
    the others at that date.
  * ingest() refuses (StaleUploadError) three ways, all bypassable with
    force=True / the Telegram caption FORCE: a file dated OLDER than
    max(snapshot_date) (every read uses max, so the write would be invisible);
    a FUTURE-dated file (it would shadow every real export until then); and an
    export that holds under half an account's prior row count (a truncated
    download parses clean — row count is the only tell).
  * CLI keeps the old merge behaviour behind --merge.

Usage:
    python ingest_fidelity.py --positions Portfolio_Positions_Jul-01-2026.csv \
                              --activity  Accounts_History__5_.csv
        -> parses, prints a summary + anomaly report, writes NOTHING.

    python ingest_fidelity.py --positions ... --activity ... --commit
        -> after you've eyeballed the dry-run, actually upserts to the DB.

DB connection: reads DATABASE_URL from env (same as the rest of the bot).

Config flags (defaults match the decisions we agreed on):
    --keep-spending      keep debit-card / bill-pay rows (default: dropped)
    --keep-cash          treat money-market sweeps as real positions (default: cash-tagged)
    --accounts A,B       restrict to specific account numbers (default: all)
"""
import argparse, csv, hashlib, io, os, re, sys
from datetime import date as _date_cls, datetime


class StaleUploadError(RuntimeError):
    """Raised when a positions CSV would write UNDER the newest snapshot in
    book_positions. Every read surface uses max(snapshot_date), so such a write
    lands where nothing reads it — silently. Loud refusal beats a lying
    'Book synced' reply (2026-07-29 operator report)."""

# ---- money markets / sweeps that are cash, not tradable book -----------------
CASH_SYMBOLS = {"CORE**", "SPAXX**", "FDRXX**", "SPAXX", "FDRXX", "QACDS"}
PENDING_MARKERS = ("Pending activity", "No Description")

OPT_RE = re.compile(r"^-([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])([\d.]+)$")


def money(x):
    """'$1,234.56' / '+$74.00' / '-$0.74' / '--' / '' -> float | None."""
    if x is None:
        return None
    s = str(x).replace("$", "").replace(",", "").replace("+", "").strip()
    if s in ("--", "", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pct(x):
    if x is None:
        return None
    s = str(x).replace("%", "").replace("+", "").strip()
    if s in ("--", "", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_option(sym):
    """'-AMZN260717P230' -> (underlying, expiry 'YYYY-MM-DD', 'P', 230.0) or None."""
    m = OPT_RE.match(sym)
    if not m:
        return None
    u, y, mo, d, cp, k = m.groups()
    return u, f"20{y}-{mo}-{d}", cp, float(k)


# Fidelity switched export header casing ~2026-07-11 ("Account Number" ->
# "Account number", "Cost Basis Total" -> "Cost basis total", ...). Canonicalize
# via lowercase lookup so BOTH vintages parse identically. History-file headers
# not in this map pass through unchanged.
_HDR_CANON = {
    "account number":            "Account Number",
    "account name":              "Account Name",
    "symbol":                    "Symbol",
    "description":               "Description",
    "quantity":                  "Quantity",
    "last price":                "Last Price",
    "last price change":         "Last Price Change",
    "current value":             "Current Value",
    "today's gain/loss dollar":  "Today's Gain/Loss Dollar",
    "today's gain/loss percent": "Today's Gain/Loss Percent",
    "total gain/loss dollar":    "Total Gain/Loss Dollar",
    "total gain/loss percent":   "Total Gain/Loss Percent",
    "percent of account":        "Percent Of Account",
    "cost basis total":          "Cost Basis Total",
    "average cost basis":        "Average Cost Basis",
    "type":                      "Type",
    # Accounts_History / History_for_Account columns (2026-07-29: the activity
    # parser was never given the casing fix the positions parser got, so a
    # re-cased export parsed to zero trades).
    "run date":                  "Run Date",
    "settlement date":           "Settlement Date",
    "action":                    "Action",
    "account":                   "Account",
    "price":                     "Price",
    "amount":                    "Amount",
    "commission":                "Commission",
    "fees":                      "Fees",
    "accrued interest":          "Accrued Interest",
    "exchange quantity":         "Exchange Quantity",
    "exchange currency":         "Exchange Currency",
    "currency":                  "Currency",
    "exchange rate":             "Exchange Rate",
}

# Fidelity writes money columns as 'Price ($)' on some exports and 'Price' on
# others. Normalize the suffix away BEFORE the canon lookup.
_HDR_UNIT_RE = re.compile(r"\s*\(\s*\$\s*\)\s*$")


def canon_header(h: str) -> str:
    """One Fidelity header cell -> canonical name. Casing- and unit-tolerant;
    unknown headers pass through stripped."""
    t = _HDR_UNIT_RE.sub("", (h or "").strip().strip('"'))
    t = re.sub(r"\s+", " ", t)
    return _HDR_CANON.get(t.lower(), t)


def read_clean(path, header_startswith):
    """Read a Fidelity CSV: strip BOM, drop footer/disclaimer rows, fix the
    trailing-comma column shift (index_col=False equivalent for stdlib csv).
    Header match + column names are casing-tolerant (Fidelity 2026-07 format
    change); a missing header fails LOUD with the filename, never a bare
    StopIteration."""
    text = open(path, encoding="utf-8-sig").read()
    lines = text.splitlines()
    # find header (case-insensitive — Fidelity changed casing 2026-07)
    try:
        # tolerate a leading quote/BOM/whitespace on the header cell
        hdr_idx = next(i for i, ln in enumerate(lines)
                       if ln.lstrip().lstrip('"﻿')
                           .lower().startswith(header_startswith.lower()))
    except StopIteration:
        sys.exit(f"LOUD FAIL: no header line starting with {header_startswith!r} "
                 f"in {path} — is this the right Fidelity export?")
    header = next(csv.reader([lines[hdr_idx]]))
    header = [canon_header(h) for h in header]
    rows = []
    for ln in lines[hdr_idx + 1:]:
        if not ln.strip():
            continue
        fields = next(csv.reader([ln]))
        # footer disclaimer rows have far fewer/odd fields or start with a quote blob
        if len(fields) < len(header):
            continue
        fields = fields[:len(header)]  # drop phantom trailing-comma column
        rows.append(dict(zip(header, [f.strip() for f in fields])))
    return header, rows


# ---------------------------------------------------------------------------
# POSITIONS
# ---------------------------------------------------------------------------
def _agg_lots(rows):
    """Collapse multiple Fidelity lot rows for one (account, symbol) into a single
    book position. Sums quantity / market_value / cost_basis / total_gl_dollar /
    pct_of_account; recomputes avg_cost (= cost_basis/quantity) and total_gl_pct;
    preserves the Type breakdown as lot_types (e.g. 'Cash+Margin'). Python owns all
    arithmetic. Single-lot positions pass through unchanged but still get lot_types."""
    def _sum(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) if vals else None
    base = dict(rows[0])
    types = sorted({(r.get("_lot_type") or "").strip()
                    for r in rows if (r.get("_lot_type") or "").strip()})
    if len(rows) > 1:
        qty, cb, gl = _sum("quantity"), _sum("cost_basis"), _sum("total_gl_dollar")
        base["quantity"]        = qty
        base["market_value"]    = _sum("market_value")
        base["cost_basis"]      = cb
        base["total_gl_dollar"] = gl
        base["pct_of_account"]  = _sum("pct_of_account")
        base["avg_cost"]        = (cb / qty) if (cb is not None and qty not in (None, 0)) else None
        base["total_gl_pct"]    = (gl / cb * 100.0) if (gl is not None and cb not in (None, 0)) else None
    base["lot_types"] = "+".join(types) if types else None
    base.pop("_lot_type", None)
    return base


def parse_positions(path, snapshot_date, keep_cash, accounts):
    _, rows = read_clean(path, "Account Number")
    out, anomalies = [], []
    for r in rows:
        sym = (r.get("Symbol") or "").strip()
        acct = (r.get("Account Number") or "").strip()
        if not sym or not acct:
            continue
        if accounts and acct not in accounts:
            continue
        is_opt = sym.startswith("-")
        exp = otype = strike = None
        if is_opt:
            p = parse_option(sym)
            if p is None:
                # DECISION (2026-08-23): reject the row outright, never store
                # the raw contract string as an underlying. A raw string here
                # previously flowed into v_screener's universe, book sides,
                # the sizing denominator, and the enrollment backlog as if it
                # were an equity ticker. These accounts hold no options by
                # rule (portfolio.ACCOUNTS), so an unparseable option symbol
                # is a malformed export line, not a position to preserve —
                # and the anomaly BLOCKS auto-commit on the CLI path, so it
                # cannot pass unnoticed.
                anomalies.append(f"UNPARSEABLE OPTION SYMBOL (row rejected): "
                                 f"{sym!r}")
                continue
            underlying, exp, otype, strike = p
            asset = "option"
        elif sym in CASH_SYMBOLS or sym.startswith("Pending"):
            underlying, asset = sym, "cash"
        else:
            underlying, asset = sym, "equity"

        if asset == "cash" and not keep_cash:
            # still record it, but as cash; SCREEN filters on asset_class != 'cash'
            pass

        out.append({
            "snapshot_date": snapshot_date,
            "account_number": acct,
            "account_name": (r.get("Account Name") or "").strip(),
            "symbol": sym,
            "underlying": underlying,
            "description": r.get("Description"),
            "asset_class": asset,
            "is_option": is_opt,
            "opt_expiry": exp, "opt_type": otype, "opt_strike": strike,
            "quantity": money(r.get("Quantity")),
            "last_price": money(r.get("Last Price")),
            "market_value": money(r.get("Current Value")),
            "cost_basis": money(r.get("Cost Basis Total")),
            "avg_cost": money(r.get("Average Cost Basis")),
            "total_gl_dollar": money(r.get("Total Gain/Loss Dollar")),
            "total_gl_pct": pct(r.get("Total Gain/Loss Percent")),
            "pct_of_account": pct(r.get("Percent Of Account")),
            "_lot_type": (r.get("Type") or "").strip() or None,
        })
    # Collapse Fidelity lot rows (Cash / Margin / Short) into one position per
    # (account, symbol). Type breakdown preserved as lot_types (decision-relevant).
    groups: dict = {}
    for p in out:
        groups.setdefault((p["account_number"], p["symbol"]), []).append(p)
    return [_agg_lots(g) for g in groups.values()], anomalies


# ---------------------------------------------------------------------------
# ACTIVITY
# ---------------------------------------------------------------------------
def classify_action(a):
    au = a.upper()
    if any(k in au for k in ("DEBIT CARD", "BILL PAY", "CHECK PAID", "CHECK RECEIVED",
                             "CASH ADVANCE", "ATM")):
        return "personal_spending", None
    if "OPENING TRANSACTION" in au:
        return "option_open", ("buy" if "YOU BOUGHT" in au else "sell")
    if "CLOSING TRANSACTION" in au:
        return "option_close", ("buy" if "YOU BOUGHT" in au else "sell")
    if "REINVESTMENT" in au:
        return "reinvest", "buy"
    if "YOU BOUGHT" in au:
        return "buy", "buy"
    if "YOU SOLD" in au:
        return "sell", "sell"
    if "DIVIDEND" in au or "INTEREST EARNED" in au:
        return "income", None
    if "FEE CHARGED" in au or "FEE" == au.split()[0] if au.split() else False:
        return "fee", None
    if any(k in au for k in ("TRANSFER", "JOURNAL", "DEPOSIT", "EFT", "WITHDRAWAL")):
        return "cash_move", None
    return "other", None


def to_date(s):
    for fmt in ("%m/%d/%Y",):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


ACCT_IN_NAME_RE = re.compile(r"History_for_Account_([A-Za-z0-9]+)", re.I)


def parse_activity(path, keep_spending, accounts):
    _, rows = read_clean(path, "Run Date")
    # Per-account exports (History_for_Account_X96383748.csv) carry NO
    # 'Account Number' column — recover it from the filename rather than
    # writing blank-account rows that collapse under the dedupe key.
    m = ACCT_IN_NAME_RE.search(os.path.basename(path))
    acct_fallback = m.group(1) if m else ""
    out, anomalies, dropped_spending = [], [], 0
    for r in rows:
        run = to_date(r.get("Run Date") or "")
        if not run:
            continue
        acct = (r.get("Account Number") or "").strip().strip('"') or acct_fallback
        if not acct:
            anomalies.append(f"ROW WITH NO ACCOUNT NUMBER: {r.get('Action')!r}")
        if accounts and acct not in accounts:
            continue
        action = (r.get("Action") or "").strip()
        atype, side = classify_action(action)
        if atype == "personal_spending" and not keep_spending:
            dropped_spending += 1
            continue

        sym = (r.get("Symbol") or "").strip()
        is_opt = sym.startswith("-")
        exp = otype = strike = None
        underlying = None
        if sym:
            if is_opt:
                p = parse_option(sym)
                if p:
                    underlying, exp, otype, strike = p
                else:
                    # Activity differs from positions: the row is a REAL cash
                    # movement, so it is kept — but the raw contract string
                    # is never stored as an underlying (underlying stays
                    # NULL; the raw form survives in `symbol` for audit).
                    anomalies.append(f"UNPARSEABLE OPTION SYMBOL "
                                     f"(activity; underlying nulled): {sym!r}")
            else:
                underlying = sym

        amount = money(r.get("Amount"))
        qty = money(r.get("Quantity"))
        row_hash = hashlib.sha1(
            f"{run}|{acct}|{action}|{sym}|{amount}|{qty}".encode()
        ).hexdigest()

        out.append({
            "run_date": run,
            "settlement_date": to_date(r.get("Settlement Date") or ""),
            "account_number": acct,
            "account_name": (r.get("Account") or "").strip().strip('"'),
            "action_raw": action,
            "action_type": atype,
            "side": side,
            "symbol": sym or None,
            "underlying": underlying,
            "is_option": is_opt,
            "opt_expiry": exp, "opt_type": otype, "opt_strike": strike,
            "description": r.get("Description"),
            "price": money(r.get("Price")),
            "quantity": qty,
            "commission": money(r.get("Commission")),
            "fees": money(r.get("Fees")),
            "amount": amount,
            "row_hash": row_hash,
        })
    return out, anomalies, dropped_spending


# ---------------------------------------------------------------------------
def summarize(positions, activity, anomalies, dropped_spending):
    print("=" * 64)
    print("DRY-RUN SUMMARY  (nothing written unless --commit is passed)")
    print("=" * 64)
    if positions:
        by_acct = {}
        tot = 0.0
        for p in positions:
            by_acct.setdefault(p["account_name"], [0, 0.0])
            by_acct[p["account_name"]][0] += 1
            by_acct[p["account_name"]][1] += (p["market_value"] or 0.0)
            tot += (p["market_value"] or 0.0)
        print(f"\nPOSITIONS: {len(positions)} rows, book value ${tot:,.0f}")
        for a, (n, v) in sorted(by_acct.items()):
            print(f"   {a:<32} {n:>3} rows  ${v:>12,.0f}")
        opts = [p for p in positions if p["is_option"]]
        print(f"   options: {len(opts)}  equity: {sum(1 for p in positions if p['asset_class']=='equity')}  "
              f"cash: {sum(1 for p in positions if p['asset_class']=='cash')}")
    if activity:
        from collections import Counter
        c = Counter(a["action_type"] for a in activity)
        print(f"\nACTIVITY: {len(activity)} rows kept "
              f"({dropped_spending} personal-spending rows dropped)")
        for k, v in c.most_common():
            print(f"   {k:<16} {v:>4}")
    if anomalies:
        print(f"\n!!! {len(anomalies)} ANOMALIES (review before committing):")
        for a in anomalies[:20]:
            print("   " + a)
    else:
        print("\nNo parse anomalies.")
    # Unmapped-wrapper detector (read-only): flag single-name/index wrapper ETFs in this
    # export with no linkage row. Proposes; never auto-writes (operator CONFIRMs via WRAP).
    try:
        from tools.wrapper_links import detector_summary_line
        line = detector_summary_line([(p.get("symbol"), p.get("description")) for p in positions])
        if line:
            print("\n" + line)
    except Exception as e:
        print(f"\n(wrapper detector skipped: {e})")
    print()


def commit(positions, activity, replace_date=None, replace_accounts=None):
    """Write positions/activity. When `replace_date` is set the snapshot is
    REPLACED, not merged: existing book_positions rows at that date are deleted
    first, inside the same transaction, so a position you CLOSED at the broker
    actually leaves the book. Upsert-only (the pre-2026-07-29 behaviour) left
    closed names in the book forever whenever a second export for the same day
    was uploaded — they kept counting in BOOK/CONC/fills and kept firing alerts.

    The delete is SCOPED to `replace_accounts` (default: the accounts present in
    `positions`). Fidelity exports whatever accounts are in view, so a
    single-account download is a normal operator action — an unscoped delete
    would silently wipe the other accounts at that date.

    Refuses to delete when `positions` is empty: a parse that yielded nothing
    must never wipe the book. Returns {deleted, positions, activity}."""
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        sys.exit("psycopg2 not installed; cannot --commit. `pip install psycopg2-binary`")
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        try:
            from db_pg import _load_dotenv_fallback
            _load_dotenv_fallback()
        except Exception:
            pass
        dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("Neither DATABASE_PUBLIC_URL nor DATABASE_URL set (and no .env fallback).")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    stats = {"deleted": 0, "positions": len(positions), "activity": len(activity)}
    try:
        with conn.cursor() as cur:
            if replace_date is not None:
                if not positions:
                    sys.exit("LOUD FAIL: refusing to replace snapshot "
                             f"{replace_date} with ZERO parsed positions — "
                             "that would wipe the book. Check the export.")
                accts = sorted(replace_accounts if replace_accounts is not None
                               else {p["account_number"] for p in positions})
                if not accts:
                    sys.exit("LOUD FAIL: refusing to replace snapshot "
                             f"{replace_date} with no account scope.")
                cur.execute("DELETE FROM book_positions "
                            "WHERE snapshot_date = %s "
                            "AND account_number = ANY(%s)",
                            (replace_date, accts))
                stats["deleted"] = cur.rowcount or 0
                stats["accounts"] = accts
                print(f"  cleared {stats['deleted']} existing rows at "
                      f"{replace_date} for {', '.join(accts)}")
            if positions:
                cols = list(positions[0].keys())
                execute_values(cur,
                    f"INSERT INTO book_positions ({','.join(cols)}) VALUES %s "
                    f"ON CONFLICT (snapshot_date, account_number, symbol) DO UPDATE SET "
                    + ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in
                               ("snapshot_date", "account_number", "symbol")),
                    [[p[c] for c in cols] for p in positions])
                print(f"  upserted {len(positions)} positions")
            if activity:
                cols = list(activity[0].keys())
                execute_values(cur,
                    f"INSERT INTO book_activity ({','.join(cols)}) VALUES %s "
                    f"ON CONFLICT (row_hash) DO NOTHING",
                    [[a[c] for c in cols] for a in activity])
                print(f"  inserted up to {len(activity)} activity rows (dupes skipped)")
        conn.commit()
        print("COMMITTED.")
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def today_et():
    """Market-local today. The Railway container runs UTC, so a file uploaded
    after 8pm ET used to fall back to TOMORROW's date and shadow the next
    morning's real export (every read uses max(snapshot_date))."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return datetime.today().date()


def newest_snapshot_date():
    """max(snapshot_date) currently in book_positions, or None. This is the
    date every read surface actually reads."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(snapshot_date) FROM book_positions")
        r = cur.fetchone()
    return r[0] if r else None


def prior_counts_by_account(accounts):
    """{account_number: rows in that account's most recent snapshot}. Used to
    catch a TRUNCATED download: a half-finished file still parses cleanly (the
    short last line is dropped as a footer), so row count is the only tell."""
    import db_pg
    out: dict = {}
    if not accounts:
        return out
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT account_number, count(*) FROM book_positions b
            WHERE account_number = ANY(%s)
              AND snapshot_date = (SELECT max(snapshot_date) FROM book_positions
                                   WHERE account_number = b.account_number)
            GROUP BY account_number""", (sorted(accounts),))
        for acct, n in cur.fetchall():
            out[acct] = int(n)
    return out


SHRINK_FLOOR = 0.5          # refuse below half the prior row count
SHRINK_MIN_PRIOR = 5        # ...but only when the prior snapshot was real


def shrink_offenders(new_counts, prior_counts,
                     floor=SHRINK_FLOOR, min_prior=SHRINK_MIN_PRIOR):
    """Pure. [(account, new_n, prior_n)] where the new export holds less than
    `floor` of the prior row count. Empty list = safe to replace."""
    bad = []
    for acct, prior_n in (prior_counts or {}).items():
        if prior_n < min_prior:
            continue
        new_n = (new_counts or {}).get(acct, 0)
        if new_n < prior_n * floor:
            bad.append((acct, new_n, prior_n))
    return sorted(bad)


def _infer_snapshot_date(path):
    """Snapshot date from a Fidelity positions filename
    (Portfolio_Positions_Mmm-DD-YYYY.csv, or a YYYY-MM-DD variant). The date is
    authoritative for the reconcile, so fall back to today only as a last resort
    and say so loudly on stderr."""
    base = os.path.basename(path)
    m = re.search(r"([A-Za-z]{3})-(\d{1,2})-(20\d{2})", base)
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)}-{int(m.group(2)):02d}-{m.group(3)}",
                "%b-%d-%Y").date().isoformat()
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})", base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    print(f"WARN ingest_fidelity: no date in filename {base!r} — using today (ET)",
          file=sys.stderr)
    return today_et().isoformat()


def ingest(csv_path, force=False):
    """Parse a Fidelity positions CSV into book_positions (broker = truth).

    Infers snapshot_date from the filename, then REPLACES that snapshot
    (delete-then-insert in one transaction) so closed positions actually leave
    the book. Positions only (no activity).

    Two loud refusals guard the write (both bypassable with force=True):
      * the file's date is OLDER than max(snapshot_date) — the write would land
        where nothing reads it;
      * the file's date is in the FUTURE vs ET today — it would shadow every
        real export until that date arrives.

    Returns {snapshot_date, rows, deleted, prior_newest, anomalies}."""
    snapshot_date = _infer_snapshot_date(csv_path)
    sd = _date_cls.fromisoformat(snapshot_date)
    positions, anomalies = parse_positions(csv_path, snapshot_date,
                                           keep_cash=False, accounts=set())
    if not positions:
        raise StaleUploadError(
            f"{os.path.basename(csv_path)}: parsed ZERO positions — refusing to "
            f"touch the book. Is this the Portfolio_Positions export?")

    t_et = today_et()
    if sd > t_et and not force:
        raise StaleUploadError(
            f"file date {sd} is in the FUTURE (ET today {t_et}). Writing it "
            f"would shadow every real export until then. Rename the file or "
            f"re-export.")

    prior_newest = None
    try:
        prior_newest = newest_snapshot_date()
    except Exception as e:                       # never block the write on this
        print(f"WARN ingest_fidelity: newest-snapshot check failed: {e}",
              file=sys.stderr)
    if prior_newest and sd < prior_newest and not force:
        raise StaleUploadError(
            f"file date {sd} is OLDER than the book's newest snapshot "
            f"{prior_newest}. Every report reads max(snapshot_date), so this "
            f"write would be invisible. Send today's export "
            f"(caption FORCE to write it anyway).")

    # Truncation guard: the replace is scoped to the accounts in THIS file, but
    # a half-downloaded file for an account still parses clean and would blow
    # that account's snapshot away.
    accounts = {p["account_number"] for p in positions}
    new_counts: dict = {}
    for p in positions:
        new_counts[p["account_number"]] = new_counts.get(p["account_number"], 0) + 1
    shrunk = []
    try:
        shrunk = shrink_offenders(new_counts, prior_counts_by_account(accounts))
    except Exception as e:
        print(f"WARN ingest_fidelity: shrink check failed: {e}", file=sys.stderr)
    if shrunk and not force:
        detail = "; ".join(f"{a}: {n} rows vs {p} before" for a, n, p in shrunk)
        raise StaleUploadError(
            f"export looks TRUNCATED — {detail}. Refusing to replace the "
            f"snapshot. Re-download the export, or caption the file FORCE if "
            f"you really closed that much.")

    # The book write is the most critical persist there is — retry transient
    # Railway drops. The delete+insert is one transaction, so a retry is safe.
    import db_pg
    stats = db_pg.with_db_retry(
        lambda: commit(positions, [], replace_date=snapshot_date,
                       replace_accounts=accounts)) or {}
    return {"snapshot_date": snapshot_date, "rows": len(positions),
            "deleted": stats.get("deleted", 0), "prior_newest": prior_newest,
            "accounts": sorted(accounts), "anomalies": anomalies}


def ingest_activity(csv_path, keep_spending=False):
    """Parse a Fidelity Accounts_History / History_for_Account CSV into
    book_activity (append-only; dedupes on row_hash, so re-sends are free).

    Added 2026-07-29: the Telegram actions upload previously wrote ONLY
    actions_log, leaving book_activity permanently empty and every fills/P&L
    surface blind to anything uploaded from the phone.
    Returns {rows, dropped_spending, anomalies}."""
    activity, anomalies, dropped = parse_activity(csv_path, keep_spending, set())
    if activity:
        import db_pg
        db_pg.with_db_retry(lambda: commit([], activity))
    return {"rows": len(activity), "dropped_spending": dropped,
            "anomalies": anomalies}


def reconcile_book(new_date):
    """Diff book_positions at new_date vs the immediately-prior snapshot_date.
    Broker CSV is truth, so:
      added   = symbols the broker holds that the prior book lacked (bot missed)
      removed = symbols the prior book had that the broker no longer holds
      unchanged = count held across both dates.
    Cash sweeps (asset_class='cash') are excluded — they aren't book desyncs.
    Returns {prior_date, added, removed, unchanged}."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(snapshot_date) FROM book_positions "
                    "WHERE snapshot_date < %s", (new_date,))
        prior = cur.fetchone()[0]

        def _syms(d):
            cur.execute("SELECT DISTINCT symbol FROM book_positions "
                        "WHERE snapshot_date = %s "
                        "AND asset_class IS DISTINCT FROM 'cash'", (d,))
            return {r[0] for r in cur.fetchall()}

        new_syms = _syms(new_date)
        prior_syms = _syms(prior) if prior else set()
    return {"prior_date": prior,
            "added": sorted(new_syms - prior_syms),
            "removed": sorted(prior_syms - new_syms),
            "unchanged": len(new_syms & prior_syms)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions")
    ap.add_argument("--activity")
    ap.add_argument("--snapshot-date", default=today_et().isoformat())
    ap.add_argument("--merge", action="store_true",
                    help="upsert into the existing snapshot instead of "
                         "REPLACING it (default is replace: broker = truth, "
                         "so closed positions must leave the book)")
    ap.add_argument("--keep-spending", action="store_true")
    ap.add_argument("--keep-cash", action="store_true")
    ap.add_argument("--accounts", default="")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    accounts = {a.strip() for a in args.accounts.split(",") if a.strip()}
    positions, pos_anom = ([], [])
    activity, act_anom, dropped = ([], [], 0)

    if args.positions:
        positions, pos_anom = parse_positions(
            args.positions, args.snapshot_date, args.keep_cash, accounts)
    if args.activity:
        activity, act_anom, dropped = parse_activity(
            args.activity, args.keep_spending, accounts)

    summarize(positions, activity, pos_anom + act_anom, dropped)

    if args.commit:
        if pos_anom + act_anom:
            print("Refusing to auto-commit with unresolved anomalies. "
                  "Fix parsing or re-run with anomalies reviewed.")
            # comment out the next line if you want to force through
            sys.exit(1)
        commit(positions, activity,
               replace_date=(None if args.merge or not positions
                             else args.snapshot_date))
    else:
        print("Dry-run only. Re-run with --commit to write.")


if __name__ == "__main__":
    main()
