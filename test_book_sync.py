"""test_book_sync.py — pure-logic cover for the 2026-07-29 broker-CSV fixes.

Run:  python3 test_book_sync.py

What broke (operator report 2026-07-29): "I send my CSV for the positions and
for the actions and the bot is not updating my positions in the reports."
Three causes, one test each below:
  1. positions were UPSERT-only per snapshot_date, so a name closed at the
     broker never left the book on a same-day re-upload;
  2. the actions importer was case-sensitive on headers (Fidelity re-cased its
     exports in July) and had no account-number fallback for the per-account
     export, so it silently parsed 0 trades;
  3. the actions CSV wrote a table no report read — nothing surfaced.

No DB, no network. DB-touching paths are exercised through fakes.
"""
import csv
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest_fidelity as IF                                   # noqa: E402
from tools import doc_ingest as DI                             # noqa: E402
from tools.report import fmt_fills                             # noqa: E402


# ─────────────────────────── fixtures ───────────────────────────────────────

POS_HEADER_OLD = ("Account Number,Account Name,Symbol,Description,Quantity,"
                  "Last Price,Current Value,Cost Basis Total,"
                  "Average Cost Basis,Total Gain/Loss Dollar,"
                  "Total Gain/Loss Percent,Percent Of Account,Type")
POS_HEADER_NEW = ("Account number,Account name,Symbol,Description,Quantity,"
                  "Last price,Current value,Cost basis total,"
                  "Average cost basis,Total gain/loss dollar,"
                  "Total gain/loss percent,Percent of account,Type")
POS_ROWS = [
    'X96383748,Individual,GLD,SPDR GOLD,10,$62.10,$621.00,$600.00,$60.00,'
    '+$21.00,+3.50%,2.10%,Margin',
    'X96383748,Individual,XLE,ENERGY SPDR,5,$90.00,$450.00,$500.00,$100.00,'
    '-$50.00,-10.00%,1.50%,Cash',
]

ACT_HEADER_OLD = ("Run Date,Account,Account Number,Action,Symbol,Description,"
                  "Type,Price,Quantity,Commission,Fees,Amount,Settlement Date")
ACT_HEADER_NEW = ("Run date,Account,Account number,Action,Symbol,Description,"
                  "Type,Price ($),Quantity,Commission ($),Fees ($),"
                  "Amount ($),Settlement date")
ACT_ROWS = [
    '07/29/2026,Individual,X96383748,YOU BOUGHT GLD,GLD,SPDR GOLD,Margin,'
    '62.10,10,0,0,-621.00,07/30/2026',
    '07/29/2026,Individual,X96383748,DEBIT CARD PURCHASE,,COFFEE,,,,,,-4.75,',
]


def _write(name, header, rows):
    d = tempfile.mkdtemp(prefix="fidtest_")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("Brokerage\n\n" + header + "\n" + "\n".join(rows) + "\n")
        f.write('\n"Disclaimer blah"\n')
    return p


# ───────────────────── 1. header tolerance (both vintages) ──────────────────

def test_canon_header_casing_and_units():
    assert IF.canon_header("Account number") == "Account Number"
    assert IF.canon_header("ACCOUNT NUMBER") == "Account Number"
    assert IF.canon_header("Price ($)") == "Price"
    assert IF.canon_header(' "Run date" ') == "Run Date"
    assert IF.canon_header("Settlement date") == "Settlement Date"
    # unknown headers survive, merely stripped
    assert IF.canon_header("  Weird Col  ") == "Weird Col"


def test_positions_parse_both_header_vintages():
    for hdr in (POS_HEADER_OLD, POS_HEADER_NEW):
        p = _write("Portfolio_Positions_Jul-29-2026.csv", hdr, POS_ROWS)
        rows, anom = IF.parse_positions(p, "2026-07-29", keep_cash=False,
                                        accounts=set())
        assert len(rows) == 2, f"{hdr[:20]}: got {len(rows)}"
        assert {r["symbol"] for r in rows} == {"GLD", "XLE"}
        assert rows[0]["snapshot_date"] == "2026-07-29"
        assert not anom, anom


def test_activity_parse_both_header_vintages():
    """The July re-casing + the '($)' money columns used to yield 0 trades."""
    for hdr in (ACT_HEADER_OLD, ACT_HEADER_NEW):
        p = _write("Accounts_History.csv", hdr, ACT_ROWS)
        acts, anom, dropped = IF.parse_activity(p, keep_spending=False,
                                                accounts=set())
        assert dropped == 1, f"{hdr[:20]}: spending row not dropped"
        assert len(acts) == 1, f"{hdr[:20]}: got {len(acts)} trades"
        a = acts[0]
        assert a["action_type"] == "buy" and a["side"] == "buy"
        assert a["symbol"] == "GLD" and a["quantity"] == 10.0
        assert a["price"] == 62.10 and a["amount"] == -621.00
        assert a["account_number"] == "X96383748"


def test_activity_account_recovered_from_filename():
    """History_for_Account_*.csv has no Account Number column — blank accounts
    collapse under the actions_log dedupe key, so recover it from the name."""
    hdr = ("Run Date,Action,Symbol,Description,Type,Price ($),Quantity,"
           "Commission ($),Fees ($),Amount ($),Settlement Date")
    row = ('07/29/2026,YOU SOLD XLE,XLE,ENERGY SPDR,Cash,90.00,5,0,0,450.00,'
           '07/30/2026')
    p = _write("History_for_Account_X96383748.csv", hdr, [row])
    acts, anom, _ = IF.parse_activity(p, keep_spending=False, accounts=set())
    assert len(acts) == 1
    assert acts[0]["account_number"] == "X96383748", acts[0]["account_number"]
    assert not anom, anom


# ───────────────────── 2. snapshot replace + stale guard ────────────────────

class _FakeCur:
    def __init__(self, outer):
        self.o = outer

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.o.sql.append((" ".join(sql.split())[:200], params))
        if sql.strip().upper().startswith("DELETE"):
            self.o.rowcount_val = self.o.existing_rows
        self.rowcount = getattr(self.o, "rowcount_val", 0)

    def fetchone(self):
        return (None,)


class _FakeConn:
    def __init__(self, outer):
        self.o = outer
        self.autocommit = False

    def cursor(self):
        return _FakeCur(self.o)

    def commit(self):
        self.o.committed = True

    def rollback(self):
        self.o.rolled_back = True

    def close(self):
        pass


class _Harness:
    """Stands in for psycopg2 + execute_values inside commit()."""

    def __init__(self, existing_rows=3):
        self.sql = []
        self.existing_rows = existing_rows
        self.committed = False
        self.rolled_back = False
        self.execute_values_calls = []

    def connect(self, dsn):
        return _FakeConn(self)


def _patched_commit(harness, positions, activity, replace_date=None):
    import types
    fake_pg = types.ModuleType("psycopg2")
    fake_pg.connect = harness.connect
    fake_extras = types.ModuleType("psycopg2.extras")

    def _ev(cur, sql, argslist):
        harness.execute_values_calls.append((" ".join(sql.split())[:40],
                                             len(argslist)))
        cur.execute(sql)
    fake_extras.execute_values = _ev
    fake_pg.extras = fake_extras
    saved = {k: sys.modules.get(k) for k in ("psycopg2", "psycopg2.extras")}
    saved_env = os.environ.get("DATABASE_URL")
    sys.modules["psycopg2"] = fake_pg
    sys.modules["psycopg2.extras"] = fake_extras
    os.environ["DATABASE_URL"] = "postgres://fake/fake"
    try:
        return IF.commit(positions, activity, replace_date=replace_date)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        if saved_env is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_env


def test_commit_replaces_the_snapshot():
    """THE bug: without the DELETE, a position closed at the broker stayed in
    book_positions forever and kept counting in BOOK/CONC/fills."""
    h = _Harness(existing_rows=3)
    pos = [{"snapshot_date": "2026-07-29", "account_number": "X1",
            "symbol": "GLD", "quantity": 1}]
    stats = _patched_commit(h, pos, [], replace_date="2026-07-29")
    deletes = [(s, p) for s, p in h.sql
               if s.startswith("DELETE FROM book_positions")]
    assert deletes, f"no DELETE issued: {h.sql}"
    assert stats["deleted"] == 3, stats
    assert h.committed and not h.rolled_back


def test_commit_delete_is_scoped_to_the_accounts_in_the_file():
    """Fidelity exports whatever accounts are in view. A Roth-only download
    must NOT wipe the Individual + Rollover rows at the same snapshot_date."""
    h = _Harness(existing_rows=4)
    pos = [{"snapshot_date": "2026-07-29", "account_number": "245734604",
            "symbol": "GLD", "quantity": 1}]
    _patched_commit(h, pos, [], replace_date="2026-07-29")
    dels = [(s, p) for s, p in h.sql
            if s.startswith("DELETE FROM book_positions")]
    assert len(dels) == 1, h.sql
    sql, params = dels[0]
    assert "account_number = ANY" in sql, sql
    assert params == ("2026-07-29", ["245734604"]), params


def test_commit_delete_scope_covers_every_account_present():
    h = _Harness()
    pos = [{"snapshot_date": "2026-07-29", "account_number": a,
            "symbol": "GLD", "quantity": 1}
           for a in ("X96383748", "244859926", "X96383748")]
    _patched_commit(h, pos, [], replace_date="2026-07-29")
    _, params = [(s, p) for s, p in h.sql if s.startswith("DELETE")][0]
    assert params[1] == ["244859926", "X96383748"], params


def test_commit_without_replace_date_does_not_delete():
    h = _Harness()
    _patched_commit(h, [{"snapshot_date": "2026-07-29", "account_number": "X1",
                         "symbol": "GLD", "quantity": 1}], [], replace_date=None)
    assert not [s for s, _ in h.sql if s.startswith("DELETE")]


def test_commit_refuses_to_wipe_on_zero_positions():
    """A parse that yields nothing must never empty the book."""
    h = _Harness()
    try:
        _patched_commit(h, [], [], replace_date="2026-07-29")
    except SystemExit as e:
        assert "refusing to replace" in str(e), e
    else:
        raise AssertionError("empty replace was allowed")
    assert not [s for s, _ in h.sql if s.startswith("DELETE")]


def test_infer_snapshot_date_formats():
    assert IF._infer_snapshot_date("Portfolio_Positions_Jul-29-2026.csv") \
        == "2026-07-29"
    assert IF._infer_snapshot_date("Portfolio_Positions_Jul-1-2026 (1).csv") \
        == "2026-07-01"
    assert IF._infer_snapshot_date("positions_2026-07-29.csv") == "2026-07-29"


def test_infer_snapshot_date_fallback_is_market_local():
    """UTC fallback stamped TOMORROW for anything uploaded after 8pm ET, which
    shadowed the next morning's real export."""
    got = IF._infer_snapshot_date("no_date_here.csv")
    assert got == IF.today_et().isoformat(), got
    assert IF.today_et() <= dt.datetime.utcnow().date()


def _ingest_with(monkey_newest, path, force=False, prior_counts=None):
    saved = IF.newest_snapshot_date
    saved_commit = IF.commit
    saved_prior = IF.prior_counts_by_account
    calls = []
    IF.newest_snapshot_date = lambda: monkey_newest
    IF.prior_counts_by_account = lambda accts: dict(prior_counts or {})
    IF.commit = lambda p, a, replace_date=None, replace_accounts=None: (
        calls.append((replace_date, sorted(replace_accounts or [])))
        or {"deleted": 0})
    saved_db = sys.modules.get("db_pg")
    import types
    fake_db = types.ModuleType("db_pg")
    fake_db.with_db_retry = lambda fn: fn()
    sys.modules["db_pg"] = fake_db
    try:
        return IF.ingest(path, force=force), calls
    finally:
        IF.newest_snapshot_date = saved
        IF.commit = saved_commit
        IF.prior_counts_by_account = saved_prior
        if saved_db is None:
            sys.modules.pop("db_pg", None)
        else:
            sys.modules["db_pg"] = saved_db


def test_shrink_offenders_pure():
    # half or more of the prior rows: fine
    assert IF.shrink_offenders({"A": 35}, {"A": 70}) == []
    assert IF.shrink_offenders({"A": 36}, {"A": 70}) == []
    # under half: flagged
    assert IF.shrink_offenders({"A": 24}, {"A": 70}) == [("A", 24, 70)]
    # account missing from the new file entirely
    assert IF.shrink_offenders({}, {"A": 70}) == [("A", 0, 70)]
    # tiny prior snapshots are not a signal
    assert IF.shrink_offenders({"A": 1}, {"A": 4}) == []
    assert IF.shrink_offenders({}, {}) == []


def test_ingest_refuses_a_truncated_export():
    """A half-downloaded file still parses clean — the short final line looks
    like a footer — so row count is the only tell before it wipes an account."""
    p = _write("Portfolio_Positions_Jul-29-2026.csv", POS_HEADER_OLD, POS_ROWS)
    try:
        _ingest_with(dt.date(2026, 7, 29), p,
                     prior_counts={"X96383748": 70})
    except IF.StaleUploadError as e:
        assert "TRUNCATED" in str(e) and "2 rows vs 70" in str(e), e
    else:
        raise AssertionError("truncated export was accepted")


def test_ingest_force_overrides_the_truncation_guard():
    p = _write("Portfolio_Positions_Jul-29-2026.csv", POS_HEADER_OLD, POS_ROWS)
    _, calls = _ingest_with(dt.date(2026, 7, 29), p, force=True,
                            prior_counts={"X96383748": 70})
    assert calls == [("2026-07-29", ["X96383748"])], calls


def test_ingest_refuses_a_file_older_than_the_newest_snapshot():
    """Every read uses max(snapshot_date); an older write is invisible, and the
    old code still replied 'Book synced'."""
    p = _write("Portfolio_Positions_Jul-24-2026.csv", POS_HEADER_OLD, POS_ROWS)
    try:
        _ingest_with(dt.date(2026, 7, 28), p)
    except IF.StaleUploadError as e:
        assert "OLDER" in str(e) and "2026-07-28" in str(e), e
    else:
        raise AssertionError("stale upload was accepted")


def test_ingest_accepts_same_or_newer_and_replaces():
    p = _write("Portfolio_Positions_Jul-29-2026.csv", POS_HEADER_OLD, POS_ROWS)
    res, calls = _ingest_with(dt.date(2026, 7, 29), p)
    assert calls == [("2026-07-29", ["X96383748"])], calls
    assert res["rows"] == 2 and res["prior_newest"] == dt.date(2026, 7, 29)
    assert res["accounts"] == ["X96383748"], res


def test_ingest_force_overrides_the_stale_guard():
    p = _write("Portfolio_Positions_Jul-24-2026.csv", POS_HEADER_OLD, POS_ROWS)
    res, calls = _ingest_with(dt.date(2026, 7, 28), p, force=True)
    assert calls == [("2026-07-24", ["X96383748"])], calls


def test_ingest_refuses_a_future_dated_file():
    future = (IF.today_et() + dt.timedelta(days=3))
    name = f"Portfolio_Positions_{future.strftime('%b-%d-%Y')}.csv"
    p = _write(name, POS_HEADER_OLD, POS_ROWS)
    try:
        _ingest_with(None, p)
    except IF.StaleUploadError as e:
        assert "FUTURE" in str(e), e
    else:
        raise AssertionError("future-dated upload was accepted")


# ───────────────────── 3. classification of the two CSVs ────────────────────

def test_classify_fidelity_csv_names():
    cases = {
        "Portfolio_Positions_Jul-29-2026.csv": "fidelity_positions",
        "Portfolio_Positions_Jul-29-2026 (1).csv": "fidelity_positions",
        "Portfolio Positions Jul-29-2026.csv": "fidelity_positions",
        "Accounts_History.csv": "fidelity_actions",
        "Accounts_History__5_.csv": "fidelity_actions",
        "History_for_Account_X96383748.csv": "fidelity_actions",
    }
    for fn, want in cases.items():
        got = DI.classify_upload(fn, "")
        assert got == want, f"{fn}: {got} != {want}"


def test_classify_still_beats_note_patterns():
    """Broker CSVs must out-rank every note/report pattern — they overwrite the
    real book."""
    assert DI.classify_upload("Portfolio_Positions_Jul-29-2026.csv",
                              "tier one alpha founder's note") \
        == "fidelity_positions"


# ───────────────────── 4. FILLS line (replaces the T1A line) ────────────────

def test_fmt_fills_aggregates_and_signs():
    rows = [("GLD", "YOU BOUGHT GLD", 10, 62.00),
            ("GLD", "YOU BOUGHT GLD", 10, 62.20),
            ("XLE", "YOU SOLD XLE", 5, 90.00)]
    out = fmt_fills(rows, dt.date(2026, 7, 29), dt.date(2026, 7, 29))
    assert out.startswith("FILLS today (3): "), out
    assert "+20 GLD@62.10×2" in out, out
    assert "-5 XLE@90.00" in out, out


def test_fmt_fills_no_rows_is_loud_about_staleness():
    out = fmt_fills([], dt.date(2026, 7, 24), dt.date(2026, 7, 29))
    assert "none today" in out and "5d stale" in out, out
    out = fmt_fills([], dt.date(2026, 7, 29), dt.date(2026, 7, 29))
    assert "none today" in out and "stale" not in out, out
    out = fmt_fills([], None, dt.date(2026, 7, 29))
    assert "EMPTY" in out, out


def test_fmt_fills_handles_missing_price():
    out = fmt_fills([("BUXX", "YOU BOUGHT BUXX", 3, None)],
                    dt.date(2026, 7, 29), dt.date(2026, 7, 29))
    assert "+3 BUXX" in out and "@" not in out, out


# ───────────────────── 5. T1A is gone from the report surfaces ──────────────

def _src(rel):
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel),
                encoding="utf-8").read()


def test_report_body_no_longer_renders_t1a():
    src = _src("tools/report.py")
    assert "from tools.t1a_parse import latest_line" not in src
    assert "T1A: n/a" not in src
    assert "fills_line(cur)" in src


def test_daypack_excludes_tier1alpha_docs():
    from tools.daypack import drop_excluded, EXCLUDED_DOC_KINDS
    assert "tier1alpha" in EXCLUDED_DOC_KINDS
    rows = [(1, "fn.pdf", "founders_note_am", None, 10, "x"),
            (2, "t1a.png", "tier1alpha", None, 99, "y"),
            (3, "fp.pdf", "flow_patrol", None, 10, "z")]
    kept, dropped = drop_excluded(rows)
    assert [r[0] for r in kept] == [1, 3], kept
    assert dropped == {"tier1alpha": 1}, dropped
    # nothing to drop -> untouched
    assert drop_excluded(rows[:1]) == (rows[:1], {})


def test_t1a_ingest_hook_is_still_wired():
    """Stripping the report line must NOT stop the corpus from building."""
    src = _src("tools/doc_ingest.py")
    assert "from tools.t1a_parse import ingest_hook" in src


if __name__ == "__main__":
    import inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"],
                                              inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
