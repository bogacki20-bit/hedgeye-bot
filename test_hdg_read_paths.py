"""test_hdg_read_paths.py — the 2026-08-23 read-path fixes, independently.

Three fixes shipped together (they share a view) but each is tested on its
own here so a regression names the exact broken issue:
  A. QUAD confirm arbitration  (issue #1a — quad_regime.last_quad_confirm)
  B. hdg band overlay          (issue #1b — migration 077 + _SOURCE_SLICE_SQL)
  C. OutBucket trend gate      (issue #4  — same migration, separate CTE)

Run:  python test_hdg_read_paths.py
Pure logic + source-text assertions: no DB, no network.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))


def _src(rel):
    with open(os.path.join(_HERE, rel), encoding="utf-8") as f:
        return f.read()


class _StubCur:
    """Scripted cursor: each execute() shifts the next canned row out."""
    def __init__(self, rows):
        self._rows, self._r = list(rows), None

    def execute(self, sql, *a):
        self._r = self._rows.pop(0)

    def fetchone(self):
        return self._r


# ───────────────────────── A. QUAD confirm arbitration ──────────────────────

def _ts(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_quad_ok_confirm_newer_than_history_wins():
    """The reported symptom: header stuck on 8/02 (last VALUE change) while
    the operator's OK replies landed in bot_state on 8/17. The later of the
    two stores must win."""
    from tools.quad_regime import last_quad_confirm
    cur = _StubCur([( _ts(2026, 8, 2), ),
                    ("2026-08-17T10:20:06.771982+00:00",)])
    assert last_quad_confirm(cur) == _ts(2026, 8, 17, 10).replace(
        hour=10, minute=20, second=6, microsecond=771982)


def test_quad_set_command_newer_than_ok_wins():
    """A QUAD: set appends history with NOW() — after a set, the header must
    show the set date even if the last OK is older."""
    from tools.quad_regime import last_quad_confirm
    cur = _StubCur([( _ts(2026, 8, 23), ),
                    ("2026-08-17T10:20:06+00:00",)])
    assert last_quad_confirm(cur) == _ts(2026, 8, 23)


def test_quad_survives_missing_or_garbage_bot_state():
    from tools.quad_regime import last_quad_confirm
    # no bot_state row at all
    cur = _StubCur([( _ts(2026, 8, 2), ), (None,)])
    assert last_quad_confirm(cur) == _ts(2026, 8, 2)
    # unparseable stamp falls back to history, never raises
    cur = _StubCur([( _ts(2026, 8, 2), ), ("not-a-timestamp",)])
    assert last_quad_confirm(cur) == _ts(2026, 8, 2)
    # both empty -> None (header prints NONE, not a crash)
    cur = _StubCur([(None,), (None,)])
    assert last_quad_confirm(cur) is None


def test_quad_readers_actually_route_through_the_helper():
    """Read-side wiring: all three header renderers must use the arbitrated
    value, not raw max(effective_at)."""
    for rel in ("tools/report.py", "tools/eod_stat_pack.py"):
        src = _src(rel)
        assert "last_quad_confirm" in src, rel
        assert 'cur.execute("SELECT max(effective_at) FROM ' \
               'quad_regime_history")' not in src, \
            f"{rel} still reads history alone"


# ───────────────────────── B. hdg band overlay (#1b) ────────────────────────

def test_view_migration_overlays_hdg_bands_with_a_freshness_bound():
    sql = _src("migrations/077_screener_hdg_bands_outbucket.sql")
    assert "hdg_band" in sql
    assert "buy_trade AS range_low" in sql and "sell_trade AS range_high" in sql
    # fresh-only: a stale hdg band must fall back to mfr, not freeze
    assert "signal_date >= CURRENT_DATE - 7" in sql
    # ordered-bounds guard: a malformed row can never invert a band
    assert "sell_trade > buy_trade" in sql
    # the overlay actually reaches the computed columns
    assert "COALESCE(hb.range_low,  lm.range_low)" in sql
    assert "band_source" in sql


def test_source_slice_sql_mirrors_the_band_overlay():
    src = _src("tools/screener.py")
    body = src.split("_SOURCE_SLICE_SQL")[1]
    assert "hdg_band" in body and "band_source" in body
    assert "COALESCE(hb.range_low, lm.range_low)" in body
    assert "signal_date >= CURRENT_DATE - 7" in body


def test_rp_series_joins_hdg_per_session():
    """SECTOR FLOW / DOLLAR+BONDS series: hdg band overrides mfr on the exact
    (ticker, session) pair, with both-bounds-present join guards so COALESCE
    can never mix one hdg bound with one mfr bound."""
    src = _src("tools/report.py")
    head = src.split("def _rp_series")[1].split("def ")[0]
    assert "hedgeye_risk_ranges" in head
    assert "r.signal_date = m.snapshot_date" in head
    assert "r.buy_trade IS NOT NULL" in head and \
           "r.sell_trade IS NOT NULL" in head
    assert "r.sell_trade > r.buy_trade" in head


# ───────────────────────── C. OutBucket trend gate (#4) ─────────────────────

def test_view_migration_reads_the_outbucket_feed():
    sql = _src("migrations/077_screener_hdg_bands_outbucket.sql")
    assert "hedgeye_signal_changes" in sql
    assert "'out_bucket'" in sql and "'trend_change'" in sql
    # only real trend states can win — 'new_addition' payloads must not
    assert "new_state IN ('BULLISH', 'BEARISH', 'NEUTRAL')" in sql


def test_newest_signal_wins_not_the_frozen_rr_row():
    """The XLU failure shape: RR row frozen 7/30 BULLISH, out_bucket 7/31
    BEARISH. The resolution CASE must prefer the change row when it is at
    least as new, in BOTH copies of the SQL."""
    for rel, tag in (("migrations/077_screener_hdg_bands_outbucket.sql", "view"),
                     ("tools/screener.py", "slice")):
        src = _src(rel)
        assert "ch.signal_date >= rr.signal_date" in src, tag
        assert "FULL OUTER JOIN" in src, \
            f"{tag}: a name with ONLY an out_bucket row (XLF/XLE/GDX) must " \
            f"still resolve a trend"


def test_trend_has_no_freshness_cutoff_but_bands_do():
    """OutBucket tags are live until Hedgeye changes them (they republish as
    trend_change rows), so trend resolution must NOT be date-bounded — while
    the band overlay MUST be. The asymmetry is the design."""
    sql = _src("migrations/077_screener_hdg_bands_outbucket.sql")
    trend_cte = sql.split("hedgeye_chg AS (")[1].split("hedgeye_trend AS (")[0]
    assert "CURRENT_DATE" not in trend_cte
    band_cte = sql.split("hdg_band AS (")[1].split("held AS (")[0]
    assert "CURRENT_DATE - 7" in band_cte


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
