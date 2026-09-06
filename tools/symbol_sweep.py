"""One-shot storage sweep for malformed ticker symbols (2026-08-23).

Companion to tools/symbol_guard.py: the guard stops new garbage at write
time; this sweep removes what the ungated parsers already stored. Same
philosophy — coverage decides, not spelling:

  * a symbol KNOWN to the curated stores (ticker_tags, book, mfr_snapshots,
    hedgeye_risk_ranges, etf_pro_ranges, alias maps) is kept;
  * an unknown symbol gets ONE batched live-quote probe (1mo window);
  * option-shaped and letterless tokens are always garbage;
  * unknown + unresolvable => DELETE (with --commit; default is report-only).

Hand-verified spares — real instruments a probe cannot vouch for — are
listed in SPARE with the evidence, so the sweep can never take them:
fragment/word lookalikes that collide with real tickers (DE=Deere, L=Loews)
are protected by the probe itself (they resolve), so SPARE only needs the
probe-invisible ones.

When sweeping ticker_tags, membership EXCLUDES ticker_tags itself so a junk
tag row cannot vouch for its own survival.

Usage:
    python -m tools.symbol_sweep            # report only
    python -m tools.symbol_sweep --commit   # delete confirmed garbage
"""
from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger(__name__)

# (table, symbol column) — every parser-writable symbol store. NOT included:
# hedgeye_risk_ranges / hedgeye_signal_changes (legitimately carry FX/index
# macro instruments and showed no garbage), book_positions (broker = truth),
# mfr_snapshots (MFR accepted the symbol = proof of existence).
TABLES = [
    ("hedgeye_momo", "ticker"),
    ("hedgeye_rta", "ticker"),
    ("hedgeye_portfolio_actions", "ticker"),
    ("hedgeye_keiths_signals", "ticker"),
    ("hedgeye_retail", "ticker"),
    ("hedgeye_signal_strength", "ticker"),
    ("ss_roster_history", "ticker"),        # ss_roster_current is a VIEW on this
    ("ss_flow_events", "ticker"),
    ("ticker_tags", "ticker"),
    ("hedgeye_ticker_inventory", "ticker"),
    ("hedgeye_ticker_history", "ticker"),   # monitored_tickers is a VIEW on inventory
]

# Probe-invisible real instruments, hand-verified 2026-08-23:
SPARE = {
    "SS":    "SHANGHAI COMPOSITE per its own hedgeye_risk_ranges rows",
    "MAG7":  "momo tracker's deliberate Mag7 basket (symbol_guard PSEUDO)",
    "GLASF": "Glass House Brands, OTC — ticker_tags CANNABIS + SS roster; "
             "yahoo returns no bars",
    "ATZ":   "Aritzia, TSX-only (ATZ.TO resolves; Hedgeye writes it bare)",
    "ADS":   "adidas in Hedgeye's bare notation (ADS.DE) — ambiguous with "
             "the delisted US ADS; mention rows kept, enrollment already "
             "excludes it via retail's side IS NULL",
    "TCNNF": "Trulieve's OTC line — same issuer as the HELD TRLV; its "
             "signal_strength add/remove rows are real events, and yahoo "
             "no longer quoting the OTC symbol is not evidence against them",
}


def sweep(commit: bool = False) -> dict:
    import db_pg
    from tools import symbol_guard as sg

    known = sg.known_universe(refresh=True)
    # ticker_tags self-exclusion done PROPERLY: rebuild the union from the
    # other stores rather than set-subtracting tags (subtraction also removed
    # symbols vouched by mfr/book/RR — FESB_F nearly got condemned that way).
    known_sans_tags = sg._alias_names()
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for sql in (
                "SELECT DISTINCT upper(underlying) FROM book_positions "
                "WHERE underlying IS NOT NULL AND asset_class <> 'cash'",
                "SELECT DISTINCT upper(ticker) FROM mfr_snapshots",
                "SELECT DISTINCT upper(ticker) FROM hedgeye_risk_ranges",
                "SELECT DISTINCT upper(ticker) FROM hedgeye_etf_pro_ranges",
            ):
                cur.execute(sql)
                known_sans_tags |= {r[0] for r in cur.fetchall() if r[0]}
    except Exception as e:
        print(f"WARN: could not build tags-excluded membership: {e}")
        known_sans_tags = known

    # Curated exclusion lists are spares too: every KNOWN_UNCOVERABLE /
    # PARKED_FOR_SOURCE name is a REAL instrument someone verified (dead,
    # renamed, foreign, or alias-only) — the sweep must never take those.
    curated = set()
    try:
        from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE
        curated = {s.upper() for s in KNOWN_UNCOVERABLE} | \
                  {s.upper() for s in PARKED_FOR_SOURCE}
    except Exception as e:
        print(f"WARN: curated lists unavailable: {e}")

    # Pass 1: collect every distinct symbol per table, classify offline.
    per_table: dict = {}
    unknown: set = set()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for tbl, col in TABLES:
            cur.execute(f"SELECT DISTINCT upper({col}) FROM {tbl} "
                        f"WHERE {col} IS NOT NULL")
            syms = {r[0] for r in cur.fetchall() if r[0]}
            per_table[tbl] = syms
            member = known_sans_tags if tbl == "ticker_tags" else known
            for t in syms:
                if t in SPARE or t in curated or t in member:
                    continue
                unknown.add(t)

    # Pass 2: batched probe over all unknowns, then an INDIVIDUAL retry
    # (with the dot->dash class-share fallback) for every batch-negative —
    # a 100-symbol yf.download can rate-limit into false negatives, and a
    # false negative here is a wrong delete.
    resolved: set = set()
    probe_ok = True
    if unknown:
        try:
            import yfinance as yf
            m = {t: sg.yf_symbol_for(t) for t in unknown}
            data = yf.download(sorted(set(m.values())), period="1mo",
                               interval="1d", group_by="ticker",
                               progress=False, threads=True)
            for t, s in m.items():
                try:
                    closes = (data[s]["Close"] if len(set(m.values())) > 1
                              else data["Close"])
                    if closes.dropna().shape[0] > 0:
                        resolved.add(t)
                except Exception:
                    pass
            negatives = sorted(unknown - resolved)
            print(f"batch probe: {len(resolved)} resolved, "
                  f"{len(negatives)} negative — confirming negatives "
                  f"individually")
            for t in negatives:
                v = sg.resolve_live(t)
                if v:
                    resolved.add(t)
                    print(f"   {t}: batch false-negative, resolves "
                          f"individually — SPARED")
                elif v is None:
                    probe_ok = False
                    print(f"!! individual probe errored on {t} — refusing "
                          f"soft deletes this run")
                    break
        except Exception as e:
            probe_ok = False
            print(f"!! live probe failed ({e}) — REFUSING to delete on shape "
                  f"alone. Report below is classification-only.")

    # Pass 3: verdicts + (optionally) deletes.
    summary = {"deleted_rows": 0, "tables": {}}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for tbl, col in TABLES:
            member = known_sans_tags if tbl == "ticker_tags" else known
            garbage = sorted(
                t for t in per_table[tbl]
                if t not in SPARE and t not in curated
                and t not in member and t not in resolved
            )
            # Hard garbage (option-shaped / letterless) is deletable even
            # without a probe; soft garbage (word-tokens) needs the probe's
            # negative answer.
            hard = [t for t in garbage
                    if sg.OPTION_RE.search(t) or not sg.plausible(t)]
            soft = [t for t in garbage if t not in hard]
            if not probe_ok:
                soft = []       # no probe, no soft verdicts
            todo = sorted(set(hard) | set(soft))
            summary["tables"][tbl] = {"hard": hard, "soft": soft}
            if not todo:
                continue
            print(f"{tbl}: {len(todo)} garbage symbol(s)")
            if hard:
                print(f"   hard (shape): {' '.join(hard)}")
            if soft:
                print(f"   soft (unknown + no live quote): {' '.join(soft)}")
            if commit:
                cur.execute(
                    f"DELETE FROM {tbl} WHERE upper({col}) = ANY(%s)", (todo,))
                print(f"   deleted {cur.rowcount} row(s)")
                summary["deleted_rows"] += cur.rowcount
        if commit:
            conn.commit()
            print(f"COMMITTED — {summary['deleted_rows']} rows deleted total")
        else:
            print("(report only — re-run with --commit to delete)")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    sweep(commit=args.commit)
    sys.exit(0)
