"""diag_volsymbols.py — READ-ONLY. Which volatility symbols are activated in the
MFR account, and are they actually landing in mfr_snapshots?

Same failure class as the dark names: a symbol can sit in list_watchlist() (so the
fan-out requests it) yet have no row in the DB. This separates "not activated"
from "activated but not ingesting".

Needs MFR_API_TOKEN:  railway run python tools/diag_volsymbols.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Explicit vol tickers, plus loose matches on VOL / CVIX / FX per the brief.
EXACT = {"MOVE", "GVZ", "OVX", "VIX", "VXN", "RVX", "VVIX", "SKEW"}
LOOSE = re.compile(r"(VOL|CVIX|FX)", re.I)
# FX-volatility specifically — what we actually want to know exists.
FXVOL = re.compile(r"(CVIX|FXVOL|FX.?VOL|JPMVXY|VXY|EUVIX|JYVIX|BPVIX)", re.I)


def _norm(sym: str) -> str:
    return (sym or "").upper().lstrip("^").strip()


def main() -> None:
    import mfr_client
    if not mfr_client._resolve_token():
        sys.exit("MFR_API_TOKEN not in env - run: railway run python tools/diag_volsymbols.py")

    wl = mfr_client.list_watchlist() or []
    print(f"=== list_watchlist(): {len(wl)} symbols ===\n")

    matches = []
    for s in wl:
        n = _norm(s)
        if n in EXACT or LOOSE.search(n):
            matches.append(s)
    matches.sort()
    print(f"=== VOLATILITY-LOOKING SYMBOLS: {len(matches)} ===")

    # names come from the per-asset payload; only fetch the matches
    info = {}
    for s in matches:
        try:
            raw = mfr_client.fetch_raw(s)
        except Exception as e:
            info[s] = (None, f"ERR {type(e).__name__}")
            continue
        if not raw:
            info[s] = (None, "EMPTY (404 all variants)")
            continue
        name = (raw.get("name") or raw.get("assetName") or raw.get("description")
                or raw.get("longName") or "")
        flat = mfr_client._flatten_for_save(raw)
        info[s] = (flat.get("price"), str(name)[:44])

    # latest stored row per symbol
    import db_pg
    latest = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""SELECT DISTINCT ON (ticker) ticker, snapshot_date, price
                           FROM mfr_snapshots WHERE ticker = ANY(%s)
                           ORDER BY ticker, snapshot_date DESC""", (matches,))
            for t, sd, px in cur.fetchall():
                latest[t] = (sd, px)
            cur.execute("SELECT max(snapshot_date) FROM mfr_snapshots")
            batch_max = cur.fetchone()[0]
    except Exception as e:
        print(f"  ! db query failed: {e}")
        batch_max = None

    hdr = f"{'symbol':<10} {'api_px':>10} {'db_date':<12} {'db_px':>10}  {'status':<14} name"
    print(hdr)
    print("-" * len(hdr))
    missing = []
    for s in matches:
        api_px, name = info.get(s, (None, ""))
        sd, dbpx = latest.get(s, (None, None))
        if sd is None:
            status = "NO ROW EVER"
            missing.append(s)
        elif batch_max and (batch_max - sd).days >= 2:
            status = f"STALE {(batch_max - sd).days}d"
            missing.append(s)
        else:
            status = "ok"
        f = lambda x: f"{float(x):>10.2f}" if x is not None else " " * 9 + "-"
        print(f"{s:<10} {f(api_px)} {str(sd or '-'):<12} {f(dbpx)}  {status:<14} {name}")

    print(f"\n  activated but NOT ingesting: {missing or 'none'}")

    fx = [s for s in wl if FXVOL.search(_norm(s))]
    print(f"\n=== FX-VOLATILITY SERIES IN THE ACCOUNT: "
          f"{'YES -> ' + ', '.join(sorted(fx)) if fx else 'NO'} ===")
    if not fx:
        print("  (no CVIX / JPM VXY / currency-vol symbol activated)")


if __name__ == "__main__":
    main()
