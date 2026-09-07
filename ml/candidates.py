"""ml/candidates.py — Round-2 Phase A Step 3: candidate rules.

A candidate = a bar where the process would even consider a long.
  setup_dip: above_trend == 1 AND rp < RP_MAX AND decel_streak >= 2
             AND distribution == 0
  setup_any: every bar with a defined rp (control).
RP_MAX starts at 0.35; if setup_dip yields < 300 rows total the brief says
loosen to 0.45 and say so — the report prints both counts either way.

Also reported (operator addition, README_SEAN.md): the share of setup_dip
candidates where mega_buy fired within +/-3 bars. DIAGNOSTIC ONLY — the +3
side looks at future bars, so this number must never become a feature; it
answers "does the indicator's own buy logic agree with our rule?".

    py ml/candidates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402

TICKERS = ["SPY", "UUP", "USO", "AAAU", "TLT"]
RP_BASE, RP_LOOSE = 0.35, 0.45


def load():
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT ticker, bar_date, rp, above_trend, decel_streak, "
            "distribution, mega_buy FROM ml_features WHERE ticker = ANY(%s)",
            conn, params=(TICKERS,))
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in f.columns[2:]:
        f[c] = f[c].astype(float)
    return f.sort_values(["ticker", "bar_date"]).reset_index(drop=True)


def dip_mask(f, rp_max):
    return ((f["above_trend"] == 1) & (f["rp"] < rp_max)
            & (f["decel_streak"] >= 2) & (f["distribution"] == 0))


def year_table(f, mask):
    d = f[mask].copy()
    d["year"] = d["bar_date"].dt.year
    tab = d.pivot_table(index="ticker", columns="year", values="bar_date",
                        aggfunc="count").fillna(0).astype(int)
    tab["total"] = tab.sum(axis=1)
    return tab


def megabuy_share(f, rp_max):
    """Share of candidates with mega_buy==1 within +/-3 bars (per ticker
    calendar). Diagnostic only — the +3 side is future information."""
    n_hit = n_tot = 0
    for _t, g in f.groupby("ticker"):
        g = g.reset_index(drop=True)
        mb = g["mega_buy"].fillna(0).astype(bool)
        near = mb.copy()
        for k in (1, 2, 3):
            near = (near | mb.shift(k).fillna(False)
                    | mb.shift(-k).fillna(False))
        m = dip_mask(g, rp_max)
        n_tot += int(m.sum())
        n_hit += int((m & near).sum())
    return n_hit, n_tot


def main() -> int:
    f = load()
    n_base = int(dip_mask(f, RP_BASE).sum())
    rp_max = RP_BASE if n_base >= 300 else RP_LOOSE
    if rp_max != RP_BASE:
        print(f"setup_dip @ rp<{RP_BASE} yields only {n_base} rows (<300) — "
              f"LOOSENED to rp<{RP_LOOSE} per the brief.")
    print(f"setup_any rows (rp defined): {int(f['rp'].notna().sum())}")
    print(f"setup_dip total @ rp<{RP_BASE}: {n_base}   "
          f"@ rp<{RP_LOOSE}: {int(dip_mask(f, RP_LOOSE).sum())}   "
          f"-> ACTIVE threshold rp<{rp_max}")
    print("\nsetup_dip candidates by ticker x year (active threshold):")
    print(year_table(f, dip_mask(f, rp_max)).to_string())
    hit, tot = megabuy_share(f, rp_max)
    print(f"\nmega_buy within +/-3 bars of a setup_dip candidate: "
          f"{hit}/{tot} = {hit / tot * 100:.1f}%  (diagnostic only — the +3 "
          f"side is future information, never a feature)")
    base = f["mega_buy"].fillna(0).mean() * 100
    print(f"(unconditional mega_buy fire rate: {base:.2f}% of bars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
