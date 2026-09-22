"""freshness.py — the FRESHNESS CONTRACTS (operator 9/21: 'why does that
keep happening and how many others are some version of that?').

Root cause of the XLI/ETHA class: every feed is append-only history read
back as 'latest row per name', and latest-KNOWN silently becomes
latest-TRUE unless someone writes an age gate. Authority rules ship
without expiries; fixes have been reactive, one source per incident.

This file is the structural fix: EVERY source declares its maximum
serving age in ONE place, and the doctor sweeps all contracts daily —
any source serving beyond its window FAILs loudly, including sources
added later (registering here is part of adding a source).

Contract = (label, sql returning the latest data date, max_age_days,
what breaks when it's stale). weekend_ok adds 2 days on Mon/Tue for
weekly Sunday products and weekday-only feeds.
"""

from __future__ import annotations

import datetime as dt

# (key, label, sql -> single date, max_age_days, weekend_pad, consequence)
CONTRACTS = [
    ("mfr_sweep", "MFR daily sweep",
     "SELECT max(snapshot_date) FROM mfr_snapshots", 1, True,
     "every range/rp/trend fallback goes stale"),
    ("hdg_rr", "Hedgeye risk-range email",
     "SELECT max(signal_date) FROM hedgeye_risk_ranges", 3, True,
     "the senior range source stops serving; mfr silently takes over"),
    ("keiths", "Keith's Signal Longs/Shorts (weekly)",
     "SELECT max(signal_date) FROM hedgeye_keiths_signals", 9, False,
     "financials sigstr shorts route on an old list"),
    ("etfpro", "ETF Pro (weekly)",
     "SELECT max(week_of) FROM hedgeye_etf_pro_ranges", 9, False,
     "etfpro roster + shelf membership goes stale"),
    ("portsol", "Portfolio Solutions re-rank",
     "SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions", 4, True,
     "ps ranks on shelf rows go stale"),
    ("ideas", "Investing Ideas newsletter (weekly, Sunday)",
     "SELECT max(signal_date) FROM hedgeye_ii_newsletter", 9, False,
     "SCREEN ideas lens serves an old roster as current"),
    ("btcq", "Crypto Quant sentiment (any non-null)",
     "SELECT max(signal_date) FROM hedgeye_crypto_quant "
     "WHERE sentiment IS NOT NULL", 7, False,
     "crypto trend authority expires (overrides already gated 7d)"),
    ("sgwalls", "EquityHub walls capture",
     "SELECT max(snapshot_date) FROM spotgamma_snapshots", 1, True,
     "walls tables/suffixes go dark (display gated 3d)"),
    ("sgtilt", "SG gamma tilt (indices capture)",
     "SELECT max(trade_date) FROM sg_tilt", 2, True,
     "GAMMA REGIME line + zone-entry sizing hint go stale"),
    ("secmon", "Sector monitor change emails (Sunday)",
     "SELECT max(signal_date) FROM hedgeye_sector_monitor_events", 9, False,
     "Retail/Financials Pro monitors stop updating"),
    ("ss_anchor", "Signal Strength Friday anchor",
     "SELECT max(anchor_date) FROM ss_roster_anchor", 9, False,
     "SS roster drift accumulates unchecked"),
    ("book", "Fidelity book snapshot",
     "SELECT max(snapshot_date) FROM book_positions", 2, True,
     "sizing/caps/attribution computed off an old book"),
    ("fills", "Lot ledger (fills)",
     "SELECT max(run_date) FROM fills", 4, True,
     "short clock + shelf run on old entry dates"),
    ("cracks", "Energy complex daily row",
     "SELECT max(as_of) FROM energy_cracks_daily", 2, True,
     "crack-spread deltas freeze (display self-flags)"),
    ("cashflows", "Cash flows (History_for_Account export)",
     "SELECT max(flow_date) FROM cash_flows WHERE NOT pending", 9, False,
     "spending line + account reconciliation go blind"),
    ("retailpro", "Retail Pro sided tags",
     "SELECT max(signal_date) FROM hedgeye_retail WHERE side IS NOT NULL", 9, False,
     "retail pro roster ages toward its 21d cutoff"),
]


def sweep(today: dt.date | None = None) -> list[tuple[str, int, int, str]]:
    """[(label, age_days, max_age, consequence)] for every source OVER its
    contract. Empty list = every source inside contract."""
    import db_pg
    today = today or dt.date.today()
    pad = 2 if today.weekday() in (0, 1) else 0     # Mon/Tue weekend pad
    out = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for _key, label, sql, max_age, weekend_ok, consequence in CONTRACTS:
            try:
                cur.execute(sql)
                r = cur.fetchone()
                d = r[0] if r else None
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                out.append((label, -1, max_age, f"query failed: {e}"))
                continue
            if d is None:
                out.append((label, -1, max_age, "no rows at all"))
                continue
            if isinstance(d, dt.datetime):
                d = d.date()
            age = (today - d).days
            limit = max_age + (pad if weekend_ok else 0)
            if age > limit:
                out.append((label, age, max_age, consequence))
    return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import db_pg
    db_pg._load_dotenv_fallback()
    over = sweep()
    if not over:
        print("all sources inside freshness contract")
    for label, age, mx, cons in over:
        print(f"OVER: {label} — {age}d old (contract {mx}d) -> {cons}")
