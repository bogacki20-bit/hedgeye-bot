"""diag_darkpull.py — why aren't MFR-served ranges reaching the DB?

For each dark name: is it in the operator's MFR watchlist, does the API serve it,
and does the served payload actually carry a range under the SAME extraction path
the save path uses?

Range extraction reuses mfr_client._flatten_for_save rather than reimplementing
the rangeData.lowerRange / upperRange fallbacks — a local copy would drift from
the real save path and could report has_range=Y for a payload save would reject.

  DRY (default, read-only): list_watchlist membership + fetch_raw + has_range.
  --save: for every ticker that showed has_range=Y, call fetch_and_save and
          re-query v_screener to show BEFORE(NO RANGE) -> AFTER.

Needs MFR_API_TOKEN in env, so run under:  railway run python tools/diag_darkpull.py
Without it fetch_raw short-circuits to None and every row would read EMPTY — a
false negative — so the script aborts up front instead.
"""

from __future__ import annotations

import argparse
import os
import sys

# Run as `python tools/diag_darkpull.py`, so sys.path[0] is tools/ — the repo
# root holding mfr_client / db_pg is not importable without this.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DARK = ["NOBL", "BUG", "COLO", "CPER", "ENZL", "EPHE", "IPAY",
        "REW", "SPLV", "VTIP", "WEAT"]


def _v_screener_rows(tickers):
    """{ticker: (range_low, range_high, snapshot_date)} — read-only."""
    import db_pg
    out = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT ticker, range_low, range_high, snapshot_date "
                        "FROM v_screener WHERE ticker = ANY(%s)", (list(tickers),))
            for t, lo, hi, sd in cur.fetchall():
                out[t] = (lo, hi, sd)
    except Exception as e:
        print(f"  ! v_screener query failed: {e}")
    return out


def _fmt(x, nd=2):
    return f"{float(x):.{nd}f}" if x is not None else "—"


def dry_pass():
    import mfr_client
    if not mfr_client._resolve_token():
        sys.exit("MFR_API_TOKEN not in env — every fetch would return None and the "
                 "table would read EMPTY for all 11 (a false negative). "
                 "Run: railway run python tools/diag_darkpull.py")

    wl = mfr_client.list_watchlist() or []
    wl_set = {str(t).upper() for t in wl}
    print(f"=== list_watchlist(): {len(wl)} symbols ===\n")

    hdr = (f"{'ticker':<8} {'in_wl':<6} {'fetched':<8} {'has_range':<10} "
           f"{'low':>10} {'high':>10}  note")
    print(hdr)
    print("-" * len(hdr))

    served = {}
    for t in DARK:
        in_wl = "Y" if t.upper() in wl_set else "N"
        note = ""
        try:
            raw = mfr_client.fetch_raw(t)
        except Exception as e:
            print(f"{t:<8} {in_wl:<6} {'ERR':<8} {'-':<10} {'—':>10} {'—':>10}  "
                  f"{type(e).__name__}: {str(e)[:60]}")
            continue
        if not raw:
            # fetch_raw tries the ticker then its alias variants; none resolved.
            variants = [t] + list(getattr(mfr_client, "_ALIASES", {}).get(t, []))
            print(f"{t:<8} {in_wl:<6} {'EMPTY':<8} {'-':<10} {'—':>10} {'—':>10}  "
                  f"404 under every variant tried ({', '.join(variants)})")
            continue

        flat = mfr_client._flatten_for_save(raw)      # the real save-path extractor
        lo, hi = flat.get("range_low"), flat.get("range_high")
        px = flat.get("price")
        has = "Y" if (lo is not None and hi is not None) else "N"
        if has == "Y":
            served[t] = (lo, hi)
        if px is None:
            note = "no latestPrice"
        note = (note + " " if note else "") + f"served_as={raw.get('_mfr_ticker_used', t)}"
        print(f"{t:<8} {in_wl:<6} {'OK':<8} {has:<10} {_fmt(lo):>10} {_fmt(hi):>10}  {note}")

    print(f"\n  served WITH a range: {len(served)}/{len(DARK)} "
          f"-> {', '.join(sorted(served)) or 'none'}")
    return served


def save_pass(served):
    import mfr_client
    if not served:
        print("\n=== SAVE pass skipped — nothing had has_range=Y ===")
        return
    before = _v_screener_rows(served)
    print(f"\n=== SAVE pass: fetch_and_save for {len(served)} ticker(s) ===")
    saved_ok = []
    for t in sorted(served):
        try:
            res = mfr_client.fetch_and_save(t)
            saved_ok.append(t) if res else None
            if not res:
                print(f"  {t:<8} fetch_and_save returned None")
        except Exception as e:
            print(f"  {t:<8} fetch_and_save raised {type(e).__name__}: {str(e)[:70]}")
    after = _v_screener_rows(served)

    print(f"\n  {'ticker':<8} {'BEFORE':<22} -> AFTER")
    print("  " + "-" * 60)
    now_banded = 0
    for t in sorted(served):
        b = before.get(t)
        a = after.get(t)
        b_s = "NO RANGE" if not b or b[0] is None else f"[{_fmt(b[0])}-{_fmt(b[1])}]"
        if a and a[0] is not None:
            a_s = f"[{_fmt(a[0])}-{_fmt(a[1])}] {a[2]}"
            now_banded += 1
        else:
            a_s = "NO RANGE (still)"
        print(f"  {t:<8} {b_s:<22} -> {a_s}")
    print(f"\n  now carrying a band: {now_banded}/{len(served)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true",
                    help="persist via fetch_and_save (default OFF = read-only)")
    a = ap.parse_args()
    served = dry_pass()
    if a.save:
        save_pass(served)
    else:
        print("\n  DRY run (read-only). Re-run with --save to persist.")


if __name__ == "__main__":
    main()
