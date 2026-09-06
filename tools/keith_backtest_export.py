"""keith_backtest_export.py — export every KEITH pattern fire since inception
(long side AND the inverse short-side study) plus the forward bars needed to
score them. EXPORT ONLY: no returns, no win rates, no performance numbers —
scoring happens downstream so this export can be audited first.

    python -m tools.keith_backtest_export --discover     # step 1, always
    # confirm the SCHEMA dict below against the discover output
    python -m tools.keith_backtest_export --export       # step 2

Outputs keith_export/: fires.csv, fires_short.csv, bars.csv, events.csv,
control.csv, control_short.csv, MANIFEST.txt.

THREE QUESTIONS --discover ANSWERS:

Q1  Does tools.keith_pattern persist its fires, or recompute on demand?
    SCHEMA["fires_table"]: None means no persisted fires exist and the
    export must REPLAY the detector over history.

Q2  Is the range table an as-of-date snapshot, or overwritten in place?
    SCHEMA["ranges_is_snapshot"]. --export REFUSES to run unless True —
    a replay against backfilled ranges evaluates June's support against
    today's band, and every downstream number would be fiction.

Q3  How many names carry bearish TREND per day, and how do those name-days
    distribute BY MONTH? If they cluster into a few weeks, the short study
    is testing a handful of drawdowns, not a signal.

REPLAY MODE (active because Q1 = recompute). Two hard requirements:

  1. AS-OF RANGES ONLY. The replay may read only the per-day stored rows
     (mfr_snapshots + the hedgeye_risk_ranges overlay, in the same
     authority order production uses). No substitution of later-known
     data. Enforced by the ranges_is_snapshot gate + by sourcing every
     condition column from the (ticker, date)-keyed tables the live
     scanner itself wrote on that day.

  2. LONG SIDE: PRODUCTION CODE PATH, NOT A REIMPLEMENTATION. Long fires
     come from tools.keith_pattern.build_series() + detect() — the exact
     functions the live KEITH command runs. Parameters recorded in
     MANIFEST.txt.

SHORT SIDE. No short mirror exists in production code; detect_short() BELOW
IS its definition, built by mechanically inverting conditions 1-3 of the
long pattern (bearish TREND + rally + FAILED trade resistance) with the
long side's thresholds MIRRORED, not fitted (long pullback rp<=0.35 ->
short rally rp>=0.65, etc.). The close condition does not obviously
invert, so three variants are exported:

    S1  next close DOWN off the test close   (strict inverse)
    S2  next close UP off the test close     (uninverted — squeeze-fade;
        matches KM's documented shorts: HSY 174.93 into a green day,
        WING above range top in a bearish trend)
    S3  no close condition — fires on the resistance-test day itself

Long variant naming: "standard" = production loose mode
(entry_on_standing=True, what KEITH / the weekly report run); "strict" =
entry only on an observed trend transition into BULLISH. Short variants
all use the standing-entry (standard) mode; S1/S2/S3 vary only the close
condition.
"""
from __future__ import annotations

import csv
import os
import random
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_pg
from tools.keith_pattern import (ARMED_RP, BREAK_EPS, PULLBACK_RP, TEST_RP,
                                 build_series, detect)

# ═══════════════════════════════ SCHEMA ═════════════════════════════════════
# Filled from `--discover` output (run 2026-08-29). Every enrichment entry may
# be set to None — the corresponding fires column degrades to blank.
SCHEMA = {
    # Q1: no table in the DB persists detector fires (hedgeye_keiths_signals
    # is Keith's PUBLISHED signal feed, not detector output; report_rows
    # kind='keith_weekly' stores rendered text). => REPLAY MODE.
    "fires_table": None,

    # Q2 verdict (evidence printed by --discover):
    #   - mfr_snapshots is keyed (ticker, snapshot_date); the only writer
    #     stamps snapshot_date = fetch-day and upserts THAT day's row only.
    #     0 rows have fetched_at more than 2 days after snapshot_date.
    #   - hedgeye_risk_ranges rows are re-parsed from the SAME dated source
    #     email (source_email_id) — parsed_at moves, the as-of date does not.
    #   - migrations 028/083 backfilled columns derived from each row's OWN
    #     full_payload — same-day data, not later knowledge.
    "ranges_is_snapshot": True,

    "series_table": "mfr_snapshots",          # (ticker, snapshot_date) keyed
    "rr_overlay_table": "hedgeye_risk_ranges",  # (ticker, signal_date) keyed
    "bars_table": "mfr_snapshots",            # close=price; volume: see note
    "events": {
        "ss": "ss_flow_events",               # add/drop
        "rta": "hedgeye_rta",                 # Keith alerts incl short/cover
        "rerank": "hedgeye_portfolio_solutions",  # rank by snapshot_date
    },
    "enrich": {
        "tier": "bucket_history",             # as-of; coverage starts 2026-07-06
        "ss_member": "ss_roster_history",
        "quad": "hedgeye_portfolio_solutions",  # monthly/quarterly per date
        "vol_tag": "vol_regime_daily",        # VIX/equity phase, as-of
        "book": "book_positions",             # as-of; coverage starts 2026-07-01
        "targets": "position_targets",
        "sector": "tools.asset_classifier",   # ticker_tags-backed (current-day)
    },
}

CONTROL_SEED = 20260829
CONTROL_OVERSAMPLE = 5
BARS_PAD_DAYS = 60
SHORT_VARIANTS = ("S1", "S2", "S3")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "keith_export")

_TIER_MARK = {"active": "●●", "top_idea": "●", "bench": "·"}

# position_targets.account stores labels; book_positions.account_number
# stores Fidelity numbers. Map per CLAUDE.md's ACCOUNTS.
_ACCOUNT_MAP = {"IND": "X96383748", "RIRA": "244859926",
                "ROTH": "245734604"}


def _tier_glyph(bucket):
    if not bucket:
        return ""
    if bucket == "removed":
        return "removed"
    for prefix, mark in _TIER_MARK.items():
        if bucket.startswith(prefix) or bucket.endswith("_" + prefix):
            return mark
    if "bench" in bucket:
        return "·"
    return bucket  # unknown bucket string: export verbatim, never hide


def _rp(price, lo, hi):
    if price is None or lo is None or hi is None or hi <= lo:
        return None
    return (float(price) - float(lo)) / (float(hi) - float(lo))


# ═════════════════════ SHORT-SIDE DETECTOR (this study's definition) ════════

def detect_short(rows, variant, armed_rp=ARMED_RP, pullback_rp=PULLBACK_RP,
                 test_rp=TEST_RP, break_eps=BREAK_EPS,
                 entry_on_standing: bool = True):
    """Mechanical inverse of tools.keith_pattern.detect. Stages per ticker
    over ascending [(date, price, range_low, range_high, trend)]:

      ENTRY    trend BEARISH (standing counts, matching long 'standard')
      ARMED    rp <= 1-armed_rp   (0.45): fell first — a real decline
      RALLIED  rp >= 1-pullback_rp (0.65): rallied back toward resistance
      TESTED   rp >= 1-test_rp     (0.85): close sitting ON trade resistance
      fire per variant:
        S1  next close DOWN off the test close, trend still BEARISH
        S2  next close UP   off the test close, trend still BEARISH
            (squeeze-fade; the break-above reset below still applies, so
            S2 fires under resistance, not through it)
        S3  the TESTED day itself (no close condition)
      RESET    trend leaves BEARISH, or close ABOVE range_high by
               >break_eps*(hi-lo) — resistance BROKEN, not failed.

    Thresholds are the long side's, mirrored — deliberately NOT fitted.
    After a fire the machine re-arms to ARMED (long-side rule mirrored);
    for S3 this means a multi-day hover at resistance can fire on
    consecutive days — dedup downstream if unwanted, it is not hidden.
    Returns (setups, final_stage) like detect()."""
    stage, entry_date, test_date, test_price = "IDLE", None, None, None
    prev_trend, seen = None, False
    setups = []
    for d, price, lo, hi, trend in rows:
        rp = _rp(price, lo, hi)
        bear = trend == "BEARISH"

        if stage != "IDLE":
            if not bear:
                stage, entry_date, test_date, test_price = "IDLE", None, None, None
                prev_trend, seen = trend, True
                continue
            if (rp is not None and lo is not None and hi is not None
                    and hi > lo
                    and float(price) > float(hi) + break_eps * (float(hi) - float(lo))):
                stage, entry_date, test_date, test_price = "IDLE", None, None, None
                prev_trend, seen = trend, True
                continue

        if stage == "IDLE":
            if bear and ((seen and prev_trend not in (None, "BEARISH"))
                         or entry_on_standing):
                stage, entry_date = "ENTERED", d
        elif rp is not None:
            if stage == "ENTERED" and rp <= 1.0 - armed_rp:
                stage = "ARMED"
            elif stage == "ARMED" and rp >= 1.0 - pullback_rp:
                stage = "RALLIED"
            if stage == "RALLIED" and rp >= 1.0 - test_rp:
                stage, test_date, test_price = "TESTED", d, float(price)
                if variant == "S3":
                    setups.append({"date": d, "entry_date": entry_date,
                                   "test_date": d, "price": float(price)})
                    stage = "ARMED"
            elif stage == "TESTED":
                fired = ((variant == "S1" and float(price) < test_price)
                         or (variant == "S2" and float(price) > test_price))
                if fired:
                    setups.append({"date": d, "entry_date": entry_date,
                                   "test_date": test_date,
                                   "price": float(price)})
                    stage = "ARMED"
                elif rp < 1.0 - test_rp:
                    stage = "RALLIED"    # drifted off resistance, no fire

        prev_trend, seen = trend, True
    return setups, stage


# ═══════════════════════════════ DISCOVER ═══════════════════════════════════

_CANDIDATE_TABLES = [
    ("mfr_snapshots", "snapshot_date"),
    ("hedgeye_risk_ranges", "signal_date"),
    ("price_history", "d"),
    ("yahoo_snapshots", "snapshot_date"),
    ("shadow_snapshots", "snapshot_date"),
    ("ss_flow_events", "event_date"),
    ("ss_roster_history", None),
    ("ps_flow_events", "event_date"),
    ("hedgeye_rta", "signal_date"),
    ("hedgeye_portfolio_solutions", "snapshot_date"),
    ("hedgeye_portfolio_actions", "action_date"),
    ("bucket_history", "effective_date"),
    ("book_positions", "snapshot_date"),
    ("position_targets", "set_date"),
    ("vol_regime_daily", "as_of"),
]


def bearish_distribution(series, lo_d=None, hi_d=None):
    """Per-day bearish-TREND counts from the replay series (post-RR-overlay,
    the trend the detector actually sees). Returns (per_month, overall) where
    per_month = {YYYY-MM: (days, avg_polled, avg_bearish, name_days)}."""
    by_day = defaultdict(lambda: [0, 0])     # date -> [polled, bearish]
    for rows in series.values():
        for d, _p, _lo, _hi, trend in rows:
            if (lo_d and d < lo_d) or (hi_d and d > hi_d):
                continue
            by_day[d][0] += 1
            if trend == "BEARISH":
                by_day[d][1] += 1
    months = defaultdict(lambda: [0, 0, 0])  # ym -> [days, polled, bearish]
    for d, (n, b) in by_day.items():
        m = months[d.strftime("%Y-%m")]
        m[0] += 1
        m[1] += n
        m[2] += b
    per_month = {ym: (v[0], v[1] / v[0], v[2] / v[0], v[2])
                 for ym, v in sorted(months.items())}
    tot_days = sum(v[0] for v in months.values())
    tot_bear = sum(v[2] for v in months.values())
    overall = (tot_bear / tot_days) if tot_days else 0.0
    return per_month, overall


def _fmt_bearish(per_month, overall) -> list[str]:
    lines = ["month     days  avg-polled  avg-bearish  bearish-name-days"]
    for ym, (days, ap, ab, nd) in per_month.items():
        lines.append(f"{ym}   {days:4d}  {ap:10.1f}  {ab:11.1f}  {nd:17d}")
    lines.append(f"overall avg bearish names/day: {overall:.1f}")
    return lines


def discover() -> None:
    with db_pg.get_conn() as c:
        cur = c.cursor()
        print("=" * 72)
        print("KEITH EXPORT — DISCOVER  (read-only)")
        print("=" * 72)

        # ── Q1: persisted fires? ────────────────────────────────────────────
        print("\n[Q1] Does tools.keith_pattern persist its fires?")
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public'
                         AND (table_name ILIKE '%%keith%%'
                              OR table_name ILIKE '%%fire%%')""")
        cand = [r[0] for r in cur.fetchall()]
        print(f"  keith/fire-named tables: {cand or 'none'}")
        print("  hedgeye_keiths_signals = Keith's PUBLISHED signal feed "
              "(parser_keiths_signals), not detector output.")
        print("  alerts_fired / book_alerts_fired = other detectors' dedup "
              "tables; keith_pattern sends NO automatic per-setup alerts.")
        cur.execute("SELECT kind, count(*) FROM report_rows GROUP BY kind "
                    "ORDER BY 2 DESC")
        print(f"  report_rows kinds: {cur.fetchall()}")
        print("  keith_weekly rows are RENDERED TEXT, not structured fires.")
        print("  >> Q1 VERDICT: RECOMPUTES ON DEMAND. No fires table. "
              "Export = historical REPLAY (see REPLAY MODE in docstring).")

        # ── Q2: as-of snapshot or overwritten? ──────────────────────────────
        print("\n[Q2] Is the range table an as-of-date snapshot?")
        print("  mfr_snapshots PK (ticker, snapshot_date); sole writer "
              "db_pg.save_mfr_snapshot stamps snapshot_date=fetch-day (UTC) "
              "and upserts only that day's row.")
        cur.execute("""SELECT count(*) FILTER (WHERE n > 1), count(*)
                       FROM (SELECT ticker, count(DISTINCT snapshot_date) n
                             FROM mfr_snapshots GROUP BY ticker) x""")
        multi, total_t = cur.fetchone()
        print(f"  distinct dates per ticker: {multi}/{total_t} tickers have "
              f">1 snapshot_date  -> "
              f"{'SNAPSHOT (history retained)' if multi else 'OVERWRITE'}")
        cur.execute("SELECT count(*) FROM mfr_snapshots "
                    "WHERE fetched_at::date > snapshot_date + 2")
        late = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM mfr_snapshots")
        total = cur.fetchone()[0]
        print(f"  empirical backfill smell: {late}/{total} rows fetched >2d "
              "after their snapshot_date"
              + ("  << CONTAMINATED — set ranges_is_snapshot=False and STOP"
                 if late else "  (clean)"))
        cur.execute("SELECT count(*) FROM hedgeye_risk_ranges "
                    "WHERE parsed_at::date > signal_date + 2")
        rr_late = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM hedgeye_risk_ranges")
        rr_total = cur.fetchone()[0]
        print(f"  hedgeye_risk_ranges: {rr_late}/{rr_total} rows re-parsed "
              ">2d after signal_date — re-parses re-extract the SAME dated "
              "source email (source_email_id); as-of data, newer parse code.")
        print("  known drift precedent (TXG 7/24, bot band 43.41-53.70 vs "
              "live 42.36-52.37) is INTRADAY STALENESS — same-day last-write"
              "-wins — not retroactive overwrite. The stored row is what the "
              "bot knew that day, which is exactly what a replay should use.")
        verdict_ok = bool(multi) and not late
        print(f"  >> Q2 VERDICT: "
              f"{'AS-OF SNAPSHOT — replay is clean.' if verdict_ok else 'OVERWRITTEN / CONTAMINATED — STOP. Fix snapshotting before measuring anything.'}")

        # ── Q3: bearish-TREND name-days, by month ───────────────────────────
        print("\n[Q3] Bearish-TREND names per day (post-RR-overlay — the "
              "trend the detector sees), full series span:")
        series = build_series(cur)
        per_month, overall = bearish_distribution(series)
        for line in _fmt_bearish(per_month, overall):
            print("  " + line)
        print("  >> Read the shape: bearish name-days clustered into a few "
              "weeks = the short study tests a handful of drawdowns, not a "
              "signal. The manifest recomputes this over the actual fire "
              "window.")

        # ── survivorship: is the replay universe as-it-was, not as-it-is? ──
        print("\n[UNIVERSE — survivorship check]")
        cur.execute("SELECT max(snapshot_date) FROM mfr_snapshots")
        latest = cur.fetchone()[0]
        cur.execute("""SELECT count(DISTINCT ticker) FROM mfr_snapshots
                       WHERE ticker NOT IN (SELECT DISTINCT ticker
                                            FROM mfr_snapshots
                                            WHERE snapshot_date >= %s)""",
                    (latest - timedelta(days=7),))
        gone = cur.fetchone()[0]
        print(f"  {gone} tickers have history but NO snapshot in the last 7 "
              "days (dropped/delisted since). The replay reads ALL rows ever "
              "written — build_series() takes no roster filter — so these "
              "names ARE evaluated over the dates they were polled. The "
              "universe is the roster as it was on each date, not today's "
              "survivor set.")

        # ── table inventory for SCHEMA ──────────────────────────────────────
        print("\n[TABLES]")
        for t, dc in _CANDIDATE_TABLES:
            cur.execute("SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name=%s", (t,))
            if not cur.fetchone():
                print(f"  {t:32s} ABSENT")
                continue
            if dc:
                cur.execute(f"SELECT count(*), min({dc}), max({dc}) FROM {t}")
                n, lo, hi = cur.fetchone()
                print(f"  {t:32s} {n:7d} rows  {lo} .. {hi}")
            else:
                cur.execute(f"SELECT count(*) FROM {t}")
                print(f"  {t:32s} {cur.fetchone()[0]:7d} rows")

        print("\n[SCHEMA] The SCHEMA dict at the top of this file is filled "
              "with the verdicts above. If this run shows different "
              "verdicts, update it before --export; --export refuses to run "
              "unless ranges_is_snapshot is True.")


# ═══════════════════════════════ EXPORT ═════════════════════════════════════

def _git_provenance() -> str:
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=root,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"],
                               capture_output=True, text=True, cwd=root,
                               timeout=10).stdout.strip()
        return f"{rev}{' DIRTY-WORKING-TREE' if dirty else ' clean'}"
    except Exception as e:
        return f"unknown ({e})"


def _asof(sorted_pairs, d):
    """Latest (dt, val) with dt <= d from an ASCENDING list, else None."""
    hit = None
    for dt_, val in sorted_pairs:
        if dt_ > d:
            break
        hit = val
    return hit


FIRES_COLS = ["fire_date", "ticker", "variant", "tier", "rp_at_fire",
              "rp_published_at_fire", "rp_source",
              "range_low_asof", "range_high_asof", "range_asof_date",
              "range_source", "close_at_fire", "entry_date",
              "test_date", "mom", "hurst", "iv", "rv", "ivpd",
              "vol_tag", "sector", "in_book_at_fire",
              "fill_pct_at_fire", "ss_member_at_fire",
              "quad_monthly", "quad_quarterly"]


def export(to_store: bool = False) -> None:
    if SCHEMA["ranges_is_snapshot"] is not True:
        sys.exit("REFUSING TO EXPORT: SCHEMA['ranges_is_snapshot'] is not "
                 "True. Run --discover; if the verdict is 'overwritten', the "
                 "snapshotting must be fixed before anything is measured.")

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest: list[str] = []
    caveats: list[str] = []

    with db_pg.get_conn() as c:
        cur = c.cursor()

        # ── replay series: PRODUCTION assembly (mfr + RR authority order) ──
        series = build_series(cur)

        # provenance: which (ticker, date) rows had their range overlaid by RR
        cur.execute("""SELECT ticker, signal_date FROM hedgeye_risk_ranges
                       WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL
                         AND sell_trade > buy_trade""")
        rr_range_keys = set(cur.fetchall())

        day_row = {(t, r[0]): r for t, rows in series.items() for r in rows}

        # ── LONG fires: production detect() ─────────────────────────────────
        long_fires = []
        long_keys = set()
        for variant, loose in (("standard", True), ("strict", False)):
            for t, rows in sorted(series.items()):
                setups, _stage = detect(rows, entry_on_standing=loose)
                for s in setups:
                    long_fires.append({"ticker": t, "variant": variant, "s": s})
                    long_keys.add((t, s["date"]))

        # ── SHORT fires: detect_short(), S1/S2/S3 ───────────────────────────
        short_fires = []
        short_keys = set()
        for variant in SHORT_VARIANTS:
            for t, rows in sorted(series.items()):
                setups, _stage = detect_short(rows, variant)
                for s in setups:
                    short_fires.append({"ticker": t, "variant": variant,
                                        "s": s})
                    short_keys.add((t, s["date"]))

        if not long_fires and not short_fires:
            sys.exit("No fires detected on either side — nothing to export.")

        all_fire_dates = ([f["s"]["date"] for f in long_fires]
                          + [f["s"]["date"] for f in short_fires])
        first_fire, last_fire = min(all_fire_dates), max(all_fire_dates)
        long_tickers = sorted({f["ticker"] for f in long_fires})
        short_tickers = sorted({f["ticker"] for f in short_fires})

        # ── enrichment loads (each optional / degrades to blank) ────────────
        keys = sorted({(f["ticker"], f["s"]["date"])
                       for f in long_fires + short_fires})
        mfr_extra = {}
        cur.execute("""SELECT ticker, snapshot_date, momentum_signal, hurst,
                              iv::float, rv::float, mfr_pos_short::float,
                              rp_source
                       FROM mfr_snapshots
                       WHERE (ticker, snapshot_date) IN %s""", (tuple(keys),))
        for t, d, mom, hurst, iv, rv, pos, rps in cur.fetchall():
            ivpd = (iv / rv - 1.0) if (iv and rv) else None
            mfr_extra[(t, d)] = (mom, hurst, iv, rv, ivpd, pos, rps)

        buckets = defaultdict(list)
        try:
            cur.execute("SELECT ticker, effective_date, bucket "
                        "FROM bucket_history ORDER BY ticker, effective_date")
            for t, d, b in cur.fetchall():
                buckets[t].append((d, b))
        except Exception as e:
            c.rollback()
            caveats.append(f"bucket_history unavailable ({e}) — tier blank")

        roster = defaultdict(list)
        try:
            cur.execute("SELECT ticker, added_on, removed_on "
                        "FROM ss_roster_history")
            for t, a, r_ in cur.fetchall():
                roster[t].append((a, r_))
        except Exception as e:
            c.rollback()
            caveats.append(f"ss_roster_history unavailable ({e})")

        quads = []
        try:
            cur.execute("""SELECT snapshot_date, max(monthly_quad),
                                  max(quarterly_quad)
                           FROM hedgeye_portfolio_solutions
                           GROUP BY snapshot_date ORDER BY snapshot_date""")
            quads = [(d, (qm, qq)) for d, qm, qq in cur.fetchall()]
        except Exception as e:
            c.rollback()
            caveats.append(f"quad source unavailable ({e})")

        vol_phases = []
        try:
            cur.execute("""SELECT as_of, phase FROM vol_regime_daily
                           WHERE index_name='VIX' ORDER BY as_of""")
            vol_phases = cur.fetchall()
        except Exception as e:
            c.rollback()
            caveats.append(f"vol_regime_daily unavailable ({e})")

        book_by_date = {}
        try:
            cur.execute("""SELECT snapshot_date, account_number,
                                  upper(coalesce(underlying, symbol)),
                                  market_value::float
                           FROM book_positions""")
            for d, acct, sym, mv in cur.fetchall():
                bd = book_by_date.setdefault(d, {})
                ad = bd.setdefault(acct, {"pos": defaultdict(float),
                                          "total": 0.0})
                ad["pos"][sym] += mv or 0.0
                ad["total"] += mv or 0.0
        except Exception as e:
            c.rollback()
            caveats.append(f"book_positions unavailable ({e}) — in_book blank")
        book_dates = sorted(book_by_date)

        targets = defaultdict(list)   # ticker -> [(set_date, account, pct)]
        try:
            cur.execute("SELECT ticker, set_date, account, target_pct::float "
                        "FROM position_targets ORDER BY set_date")
            for t, sd, acct, pct in cur.fetchall():
                targets[t.upper()].append((sd, _ACCOUNT_MAP.get(acct, acct),
                                           pct))
        except Exception as e:
            c.rollback()
            caveats.append(f"position_targets unavailable ({e}) — fill blank")

        sector_of = {}
        try:
            from tools.asset_classifier import classify
            for t in sorted(set(long_tickers) | set(short_tickers)):
                try:
                    sector_of[t] = (classify(t) or {}).get("sector") or ""
                except Exception:
                    sector_of[t] = ""
            caveats.append("sector comes from CURRENT ticker_tags/classifier "
                           "state, not as-of fire date (sector history is "
                           "not versioned; sectors rarely move — treat "
                           "sector slices as approximate)")
        except Exception as e:
            caveats.append(f"asset_classifier unavailable ({e}) — sector blank")

        def book_asof(d):
            hit = None
            for bd in book_dates:
                if bd > d:
                    break
                hit = bd
            return hit

        def fire_row(f):
            t, s = f["ticker"], f["s"]
            d = s["date"]
            row = day_row.get((t, d))
            _, price, lo, hi, _trend = row if row else (d, s["price"],
                                                        None, None, None)
            rp = _rp(price, lo, hi)
            mom, hurst, iv, rv, ivpd, pos_pub, rps = mfr_extra.get(
                (t, d), (None,) * 7)
            bucket = _asof(buckets.get(t, []), d)
            q = _asof(quads, d) or ("", "")
            vol = _asof(vol_phases, d) or ""
            ss_member = any(a <= d and (r_ is None or r_ > d)
                            for a, r_ in roster.get(t, []))
            bd = book_asof(d)
            in_book = ""
            fill = ""
            if bd is not None:
                accts = book_by_date[bd]
                mv_by_acct = {acct: ad["pos"].get(t.upper(), 0.0)
                              for acct, ad in accts.items()}
                in_book = any(abs(v) > 1e-9 for v in mv_by_acct.values())
                tgt = {}
                for sd, acct, pct in targets.get(t.upper(), []):
                    if sd is None or sd <= d:
                        tgt[acct] = pct    # latest set_date wins (sorted)
                denom = sum((tgt.get(acct, 0.0) / 100.0) * ad["total"]
                            for acct, ad in accts.items())
                if denom > 0:
                    num = sum(abs(mv_by_acct.get(acct, 0.0)) for acct in tgt)
                    fill = round(100.0 * num / denom, 1)
            return [d, t, f["variant"], _tier_glyph(bucket),
                    round(rp, 4) if rp is not None else "",
                    pos_pub if pos_pub is not None else "",
                    rps or "",
                    lo if lo is not None else "",
                    hi if hi is not None else "",
                    d, "hedgeye_rr" if (t, d) in rr_range_keys else "mfr",
                    price, s["entry_date"], s["test_date"],
                    mom or "", hurst if hurst is not None else "",
                    iv if iv is not None else "",
                    rv if rv is not None else "",
                    round(ivpd, 4) if ivpd is not None else "",
                    vol, sector_of.get(t, ""), in_book, fill,
                    ss_member, q[0] or "", q[1] or ""]

        def write_fires(path, fires):
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(FIRES_COLS)
                for f in sorted(fires, key=lambda x: (x["s"]["date"],
                                                      x["ticker"],
                                                      x["variant"])):
                    w.writerow(fire_row(f))

        write_fires(os.path.join(OUT_DIR, "fires.csv"), long_fires)
        write_fires(os.path.join(OUT_DIR, "fires_short.csv"), short_fires)

        # ── bars.csv (both sides + SPY) ─────────────────────────────────────
        # close = mfr_snapshots.price (the same close the detector consumed).
        # volume: mfr publishes previous_day_volume; the volume FOR bar date D
        # is taken from the NEXT stored row (date E) when E-D <= 4 calendar
        # days (consecutive trading days), else left blank — never guessed.
        bar_tickers = sorted(set(long_tickers) | set(short_tickers) | {"SPY"})
        lo_d = first_fire - timedelta(days=BARS_PAD_DAYS)
        hi_d = last_fire + timedelta(days=BARS_PAD_DAYS)
        cur.execute("""SELECT ticker, snapshot_date, price::float,
                              previous_day_volume
                       FROM mfr_snapshots
                       WHERE ticker IN %s AND snapshot_date BETWEEN %s AND %s
                         AND price IS NOT NULL
                       ORDER BY ticker, snapshot_date""",
                    (tuple(bar_tickers), lo_d, hi_d))
        bars_by_t = defaultdict(list)
        for t, d, px, pdv in cur.fetchall():
            bars_by_t[t].append([d, px, pdv])
        n_bars = 0
        with open(os.path.join(OUT_DIR, "bars.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "date", "close", "volume", "source"])
            for t in bar_tickers:
                rows = bars_by_t.get(t, [])
                for i, (d, px, _pdv) in enumerate(rows):
                    vol = ""
                    if i + 1 < len(rows):
                        nd, _npx, npdv = rows[i + 1]
                        if npdv is not None and (nd - d).days <= 4:
                            vol = npdv
                    w.writerow([t, d, px, vol, "mfr"])
                    n_bars += 1

        # ── bars_full.csv (ENTIRE polling universe, inception onward) ───────
        # Same format/volume-alignment as bars.csv. This is what the
        # fingerprint scan and its control run against.
        cur.execute("""SELECT ticker, snapshot_date, price::float,
                              previous_day_volume
                       FROM mfr_snapshots
                       WHERE price IS NOT NULL
                       ORDER BY ticker, snapshot_date""")
        full_by_t = defaultdict(list)
        for t, d, px, pdv in cur.fetchall():
            full_by_t[t].append([d, px, pdv])
        n_bars_full = 0
        with open(os.path.join(OUT_DIR, "bars_full.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "date", "close", "volume", "source"])
            for t in sorted(full_by_t):
                rows = full_by_t[t]
                for i, (d, px, _pdv) in enumerate(rows):
                    vol = ""
                    if i + 1 < len(rows):
                        nd, _npx, npdv = rows[i + 1]
                        if npdv is not None and (nd - d).days <= 4:
                            vol = npdv
                    w.writerow([t, d, px, vol, "mfr"])
                    n_bars_full += 1

        # ── ranges_daily.csv + trend_daily.csv (whole universe, as-of) ──────
        # Straight from the replay series: the post-RR-overlay range and
        # trend the detector actually evaluates each day. A blank range is a
        # declared gap (MFR had no band that day), never backfilled.
        n_ranges = n_ranges_blank = n_trend = n_trend_blank = 0
        with open(os.path.join(OUT_DIR, "ranges_daily.csv"), "w", newline="",
                  encoding="utf-8") as rf, \
             open(os.path.join(OUT_DIR, "trend_daily.csv"), "w", newline="",
                  encoding="utf-8") as tf:
            rw, tw = csv.writer(rf), csv.writer(tf)
            rw.writerow(["ticker", "date", "range_low", "range_high",
                         "source"])
            tw.writerow(["ticker", "date", "trend"])
            for t in sorted(series):
                for d, _px, lo, hi, trend in series[t]:
                    if lo is not None and hi is not None:
                        rw.writerow([t, d, lo, hi,
                                     "hedgeye_rr" if (t, d) in rr_range_keys
                                     else "mfr"])
                    else:
                        rw.writerow([t, d, "", "", ""])
                        n_ranges_blank += 1
                    n_ranges += 1
                    tw.writerow([t, d, trend or ""])
                    n_trend += 1
                    if not trend:
                        n_trend_blank += 1

        # ── events.csv ──────────────────────────────────────────────────────
        ev_counts = defaultdict(int)
        rta_short_cover = 0
        with open(os.path.join(OUT_DIR, "events.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["source", "ticker", "date", "action"])
            cur.execute("SELECT ticker, event_date, event FROM ss_flow_events "
                        "ORDER BY event_date, ticker")
            for t, d, ev in cur.fetchall():
                w.writerow(["ss", t, d, ev])
                ev_counts["ss"] += 1
            cur.execute("""SELECT ticker, signal_date, action, side,
                                  signal_type, km_flag
                           FROM hedgeye_rta ORDER BY signal_date, ticker""")
            for t, d, act, side, st_, km in cur.fetchall():
                label = "/".join(x for x in (act, side, st_) if x)
                if km:
                    label += "/KM"
                w.writerow(["rta", t, d, label])
                ev_counts["rta"] += 1
                if side == "short" or (st_ or "").startswith("cover"):
                    rta_short_cover += 1
            cur.execute("""SELECT ticker, snapshot_date, rank
                           FROM hedgeye_portfolio_solutions
                           ORDER BY snapshot_date, rank""")
            for t, d, rk in cur.fetchall():
                w.writerow(["rerank", t, d, f"rank={rk}"])
                ev_counts["rerank"] += 1

        # ── controls (seeded, reproducible, side-specific pools) ────────────
        def write_control(path, pool, n_distinct_fires):
            pool = sorted(pool)
            n_target = CONTROL_OVERSAMPLE * n_distinct_fires
            rng = random.Random(CONTROL_SEED)
            picked = sorted(rng.sample(pool, min(n_target, len(pool))))
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["ticker", "date"])
                for t, d in picked:
                    w.writerow([t, d])
            return len(picked), n_target, len(pool), picked

        # long control v2 (operator 8/29): drawn from the FULL polling
        # universe — any polled day with a price, no long fire that day.
        # Same seed, same 5x, same non-fire rule; only the pool widened.
        full_pool = {(t, r[0]) for t, rows in series.items() for r in rows
                     if r[1] is not None and (t, r[0]) not in long_keys}
        n_ctrl, n_tgt, n_pool, picked_full = write_control(
            os.path.join(OUT_DIR, "control.csv"), full_pool, len(long_keys))
        ctrl_line = (f"control.csv: {n_ctrl} rows, FULL-UNIVERSE pool "
                     f"(target {n_tgt} = {CONTROL_OVERSAMPLE}x "
                     f"{len(long_keys)} distinct long fires, pool {n_pool}, "
                     f"seed {CONTROL_SEED})")
        if n_pool < n_tgt:
            caveats.append(f"long control pool exhausted: wanted {n_tgt}, "
                           f"pool holds {n_pool} — exported all")

        # continuity: round-1's fired-tickers-only control, same seed/rule
        fired_pool = {(t, r[0]) for t in long_tickers
                      for r in series.get(t, [])
                      if r[1] is not None and (t, r[0]) not in long_keys}
        nf_ctrl, nf_tgt, nf_pool, picked_fired = write_control(
            os.path.join(OUT_DIR, "control_fired_only.csv"), fired_pool,
            len(long_keys))
        ctrl_fired_line = (f"control_fired_only.csv: {nf_ctrl} rows "
                           f"(round-1 rule: fired-long tickers only; pool "
                           f"{nf_pool}, seed {CONTROL_SEED})")

        # short: fired-short tickers, days carrying BEARISH trend (the trend
        # the detector sees), no short fire (any variant) that day. Shorts
        # get their own control — positive equity drift makes the long
        # control's base rate invalid for them.
        short_pool = {(t, r[0]) for t in short_tickers
                      for r in series.get(t, [])
                      if r[1] is not None and r[4] == "BEARISH"
                      and (t, r[0]) not in short_keys}
        ns_ctrl, ns_tgt, ns_pool, picked_short = write_control(
            os.path.join(OUT_DIR, "control_short.csv"), short_pool,
            len(short_keys))
        ctrl_short_line = (f"control_short.csv: {ns_ctrl} rows (target "
                           f"{ns_tgt} = {CONTROL_OVERSAMPLE}x "
                           f"{len(short_keys)} distinct short fires, pool "
                           f"{ns_pool}, seed {CONTROL_SEED})")
        if ns_pool < ns_tgt:
            caveats.append(f"short control pool exhausted: wanted {ns_tgt}, "
                           f"pool holds {ns_pool} — exported all "
                           "(bearish-TREND days are scarce; see Q3)")

        # ── store backfill (round 2: tables are the store, CSVs the dump) ──
        if to_store:
            from tools.signal_store import (ensure_tables, upsert_controls,
                                            upsert_fires)
            ensure_tables(cur)

            def feat(f):
                d = dict(zip(FIRES_COLS, fire_row(f)))
                # PK columns live outside features; drop the duplication
                for k in ("fire_date", "ticker", "variant"):
                    d.pop(k, None)
                return {k: v for k, v in d.items() if v != ""}

            n_store_l = upsert_fires(cur, "keith_long",
                                     [(f["variant"], f["ticker"],
                                       f["s"]["date"], feat(f))
                                      for f in long_fires])
            n_store_s = upsert_fires(cur, "keith_short",
                                     [(f["variant"], f["ticker"],
                                       f["s"]["date"], feat(f))
                                      for f in short_fires])
            n_store_c = (upsert_controls(cur, "keith_long_full", picked_full,
                                         CONTROL_SEED)
                         + upsert_controls(cur, "keith_long_fired_only",
                                           picked_fired, CONTROL_SEED)
                         + upsert_controls(cur, "keith_short", picked_short,
                                           CONTROL_SEED))
            c.commit()
            print(f"store backfill: signal_paper_fires keith_long="
                  f"{n_store_l} keith_short={n_store_s}; signal_controls="
                  f"{n_store_c} across 3 generations")

        # ── manifest bookkeeping ────────────────────────────────────────────
        def no_forward(fires):
            return sorted({(f["ticker"], str(f["s"]["date"]), f["variant"])
                           for f in fires
                           if not any(d > f["s"]["date"]
                                      for d, _, _ in
                                      bars_by_t.get(f["ticker"], []))})

        def quad_span(fires):
            qs = {(_asof(quads, f["s"]["date"]) or ("", ""))[0]
                  for f in fires}
            qs.discard("")
            qs.discard(None)
            return qs

        def interp_flags(label, fires):
            distinct = {(f["ticker"], f["s"]["date"]) for f in fires}
            out = []
            if len(distinct) < 100:
                out.append(f"{label}: n = {len(distinct)} distinct fires "
                           "< 100 — AGGREGATE ONLY, no slicing by "
                           "sector/tier/Quad.")
            qs = quad_span(fires)
            if len(qs) <= 1:
                out.append(f"{label}: fires span "
                           f"{len(qs) or 'zero known'} Quad(s) "
                           f"({', '.join(sorted(qs)) or 'n/a'}) — NO REGIME "
                           "CLAIM possible.")
            return out

        interp = interp_flags("LONG (both variants)", long_fires)
        for v in ("standard", "strict"):
            interp += interp_flags(
                f"LONG {v}", [f for f in long_fires if f["variant"] == v])
        interp += interp_flags("SHORT (all variants)", short_fires)
        for v in SHORT_VARIANTS:
            interp += interp_flags(
                f"SHORT {v}", [f for f in short_fires if f["variant"] == v])

        all_dates = [r[0] for rows in series.values() for r in rows]
        series_span = (min(all_dates), max(all_dates))
        stale_fired = sorted(
            t for t in set(long_tickers) | set(short_tickers)
            if max(r[0] for r in series[t]) < series_span[1]
            - timedelta(days=7))

        by_variant_l = defaultdict(int)
        for f in long_fires:
            by_variant_l[f["variant"]] += 1
        by_variant_s = defaultdict(int)
        for f in short_fires:
            by_variant_s[f["variant"]] += 1

        per_month, overall = bearish_distribution(series, first_fire,
                                                  last_fire)
        no_fwd_l = no_forward(long_fires)
        no_fwd_s = no_forward(short_fires)

        manifest += [
            "KEITH BACKTEST EXPORT — MANIFEST (long + short study)",
            f"generated: {datetime.now().isoformat(timespec='seconds')}  "
            f"git: {_git_provenance()}",
            f"long detector: tools.keith_pattern.detect (production path) — "
            f"armed_rp={ARMED_RP} pullback_rp={PULLBACK_RP} test_rp={TEST_RP} "
            f"break_eps={BREAK_EPS}",
            "short detector: detect_short() in tools/keith_backtest_export.py"
            " — NO production short code exists; this export DEFINES the "
            "inverse. Thresholds mirrored from the long side (armed "
            f"rp<={1-ARMED_RP:g}, rally rp>={1-PULLBACK_RP:g}, test "
            f"rp>={1-TEST_RP:g}), deliberately NOT fitted. Reset on trend "
            "leaving BEARISH or close above range_high (resistance broken). "
            "All short variants use standing-entry (long 'standard' mode).",
            "long variants: standard = production loose mode; strict = "
            "entry only on observed trend transition to BULLISH.",
            "short variants: S1 next close DOWN off test close (strict "
            "inverse) · S2 next close UP (uninverted, squeeze-fade) · S3 "
            "fires on the resistance-test day itself (no close condition).",
            "",
            "== LOOK-AHEAD VERDICT (Q2) ==",
            "The range table (mfr_snapshots, RR overlay from "
            "hedgeye_risk_ranges) IS an as-of-date snapshot. The sole writer "
            "stamps each row with its fetch day and only ever overwrites "
            "that same day's row; zero rows were written more than 2 days "
            "after their stated date, and 789/791 tickers retain >1 "
            "distinct snapshot_date. RR rows are occasionally re-parsed, "
            "but from the same dated source email. The TXG 7/24 band drift "
            "is intraday staleness (last fetch of the day wins), not "
            "retroactive overwrite — the stored band is what the bot knew "
            "that day, which is what a replay should evaluate. "
            "VERDICT: replay is clean of look-ahead contamination.",
            "",
            "== Q1 ==",
            "Fires are NOT persisted anywhere; this export REPLAYED the "
            "detectors over the stored history (as-of rows only; long side "
            "on the production code path).",
            "",
            "== Q3: BEARISH-TREND DISTRIBUTION (fire window "
            f"{first_fire} .. {last_fire}) ==",
        ] + _fmt_bearish(per_month, overall) + [
            "",
            "== ROW COUNTS ==",
            f"fires.csv (LONG): {len(long_fires)} rows "
            f"({by_variant_l['standard']} standard, "
            f"{by_variant_l['strict']} strict; {len(long_keys)} distinct "
            f"ticker-days) — {len(long_tickers)} distinct tickers, "
            f"{min(f['s']['date'] for f in long_fires) if long_fires else 'n/a'}"
            f" .. "
            f"{max(f['s']['date'] for f in long_fires) if long_fires else 'n/a'}",
            f"fires_short.csv (SHORT): {len(short_fires)} rows "
            f"(S1={by_variant_s['S1']}, S2={by_variant_s['S2']}, "
            f"S3={by_variant_s['S3']}; {len(short_keys)} distinct "
            f"ticker-days) — {len(short_tickers)} distinct tickers, "
            f"{min(f['s']['date'] for f in short_fires) if short_fires else 'n/a'}"
            f" .. "
            f"{max(f['s']['date'] for f in short_fires) if short_fires else 'n/a'}",
            f"bars.csv:    {n_bars} rows, {len(bar_tickers)} tickers "
            f"(both sides + SPY), window {lo_d} .. {hi_d}",
            f"events.csv:  ss={ev_counts['ss']} rta={ev_counts['rta']} "
            f"(of which short/cover alerts: {rta_short_cover}) "
            f"rerank={ev_counts['rerank']}",
            ctrl_line,
            ctrl_fired_line,
            ctrl_short_line,
            f"bars_full.csv:   {n_bars_full} rows, {len(full_by_t)} tickers "
            "(ENTIRE polling universe, inception onward)",
            f"ranges_daily.csv: {n_ranges} rows ({n_ranges_blank} with no "
            "range that day — declared blank, never backfilled)",
            f"trend_daily.csv:  {n_trend} rows ({n_trend_blank} with no "
            "known trend — blank)",
            "",
            "== ROUND-2 AMENDMENT NOTES ==",
            "This is a REGENERATION of the whole export (same harness, same "
            "seed) plus: bars_full/ranges_daily/trend_daily for the entire "
            "universe, and control.csv redrawn from the FULL universe. The "
            "round-1 fired-tickers control is kept verbatim-in-rule as "
            "control_fired_only.csv. ranges_daily and trend_daily are the "
            "POST-RR-OVERLAY values the detector evaluates (RR authoritative "
            "where the name was in that day's RR email; source column says "
            "which). Fire counts can differ marginally from the 8/29 "
            "morning run where later same-day fetches updated that day's "
            "snapshot rows.",
            "",
            f"series coverage: {len(series)} tickers, "
            f"{series_span[0]} .. {series_span[1]} (inception of "
            f"mfr_snapshots — fires cannot predate it)",
            "",
            "== INTERPRETABILITY LIMITS (per side / per variant) ==",
        ] + (interp or ["none tripped"]) + [
            "",
            "== UNIVERSE (survivorship) ==",
            "Replay universe = every ticker ever polled, over the dates it "
            "was polled (build_series takes no roster filter) — the roster "
            "as it WAS on each date, not today's survivor set. "
            f"{len(stale_fired)} fired ticker(s) have since left the "
            "polling universe (no snapshot in the final 7 days of "
            f"coverage): {', '.join(stale_fired) or 'none'}.",
            "",
            "== FIRES WITH NO FORWARD BARS ==",
            ("LONG " + (f"{len(no_fwd_l)}: "
                        + "; ".join(f"{t}@{d}({v})" for t, d, v in no_fwd_l)
                        if no_fwd_l else "none")),
            ("SHORT " + (f"{len(no_fwd_s)}: "
                         + "; ".join(f"{t}@{d}({v})" for t, d, v in no_fwd_s)
                         if no_fwd_s else "none")),
            f"NOTE: bar data ends {series_span[1]}; fires within "
            f"{BARS_PAD_DAYS} days of that date have a truncated forward "
            "window — score accordingly.",
            "",
            "== CAVEATS ==",
        ]
        manifest += [f"- {x}" for x in caveats] + [
            "- S3 has no close condition and re-arms after firing (long-side "
            "re-arm rule mirrored), so a multi-day hover at resistance can "
            "fire on consecutive days. Not hidden; dedup downstream if "
            "unwanted.",
            "- bars.csv volume: MFR publishes previous-day volume; volume "
            "for bar D is taken from the next stored row when it is within "
            "4 calendar days, else blank. Blank means unknown, never zero.",
            "- ivpd = iv/rv - 1 (ratio premium, matches ss_flow_events).",
            "- vol_tag = VIX (equity sleeve) phase from vol_regime_daily, "
            "as-of fire date; coverage 2026-05-24 onward.",
            "- tier from bucket_history as-of fire date; coverage starts "
            "2026-07-06 — earlier fires have blank tier (NOT backfilled "
            "from today's roster: that would be look-ahead).",
            "- in_book/fill from book_positions as-of latest snapshot <= "
            "fire date; coverage starts 2026-07-01, snapshots are not "
            "daily — 'as-of' can be several days stale. fill_pct = "
            "sum(|mv|)/sum(target_pct * acct_value) over accounts with a "
            "target set on or before the fire date; blank when no target. "
            "The position_targets TABLE holds only 4 tickers (FUTY, BNDD, "
            "TUA, ULS, all set 2026-07-11) — the live fill% display "
            "derives DEFAULT targets in code (tools/position_targets.py), "
            "which are not persisted and are NOT reconstructed here (that "
            "would be a silent substitution). Expect fill_pct blank for "
            "nearly every fire.",
            "- quad_monthly/quarterly from hedgeye_portfolio_solutions "
            "as-of fire date (latest re-rank on or before).",
            "- control.csv (round 2): FULL polling universe, any day with a "
            "price and no long fire that day. control_fired_only.csv: the "
            "round-1 rule (fired-long tickers only), kept for continuity. "
            "control_short.csv: fired-short tickers, days carrying "
            "BEARISH trend with no short fire any variant — shorts get "
            "their own control because equity drift makes the long "
            "control's base rate asymmetric. Neither is a SPY benchmark; "
            "both are needed and neither replaces the other.",
            "- EXPORT ONLY: no returns, win rates, or performance numbers "
            "computed here, by design.",
        ]
        with open(os.path.join(OUT_DIR, "MANIFEST.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(manifest) + "\n")

    print("\n".join(manifest))
    print(f"\nwrote {OUT_DIR}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--discover" in argv:
        discover()
    elif "--backfill" in argv:
        # round 2: CSVs + upsert fires/controls into the first-class store
        # (signal_paper_fires / signal_controls via tools.signal_store)
        export(to_store=True)
    elif "--export" in argv:
        export()
    else:
        sys.exit("usage: python -m tools.keith_backtest_export "
                 "--discover | --export | --backfill")


if __name__ == "__main__":
    main()
