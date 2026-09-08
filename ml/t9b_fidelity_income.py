"""ml/t9b_fidelity_income.py — T9b: the definitive whole-book TWR from
Fidelity's own Investment Income export (All Accounts, margin included —
Fidelity's "Market change" already excludes flows/interest/dividends).

Monthly return = (market change + dividends + interest - fees) /
(beginning balance + 0.5 * net flow)   [flow timing within the month is
unknown -> mid-month weighting], chained Sep-2025 -> Sep-4-2026.
Benchmarks: SPY and 60/40 on adjusted closes over the same month ends.

    py ml/t9b_fidelity_income.py "<path to Investment_income CSV>"
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

OUT = REPO / "reports"
ADJ = REPO / "data" / "reference" / "t6_px_adj.csv"
LINES: list[str] = []
MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def say(s=""):
    print(s)
    LINES.append(s)


def money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else
                r"C:\Users\bogac\Downloads\Investment_income_balance_detail.csv")
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if not r or not re.match(r"^[A-Z][a-z]{2} 20\d\d", r[0] or ""):
                continue
            m = re.match(r"^([A-Z][a-z]{2}) (20\d\d)(?:\(As of "
                         r"([A-Za-z]{3})-(\d{2})-(\d{4})\))?", r[0])
            yr, mo = int(m.group(2)), MONTHS[m.group(1)]
            asof = (date(int(m.group(5)), MONTHS[m.group(3)],
                         int(m.group(4))) if m.group(3) else None)
            rows.append(dict(
                year=yr, month=mo, asof=asof, beg=money(r[1]),
                mkt=money(r[2]), div=money(r[3]), intr=money(r[4]),
                dep=money(r[5]), wdr=money(r[6]), fees=money(r[7]),
                end=money(r[8])))
    df = (pd.DataFrame(rows)
          .sort_values(["year", "month"]).reset_index(drop=True))

    say(f"# T9b — whole-book TWR from Fidelity's own income export "
        f"({date.today()})")
    say(f"\nSource: {path.name}, All Accounts (margin included — "
        f"Fidelity's 'Market change' is performance net of every flow). "
        f"{len(df)} monthly rows, "
        f"{df.iloc[0]['year']}-{df.iloc[0]['month']:02d} (from "
        f"{df.iloc[0]['asof'] or 'month start'}) .. "
        f"{df.iloc[-1]['year']}-{df.iloc[-1]['month']:02d} (to "
        f"{df.iloc[-1]['asof'] or 'month end'}).")

    df["pnl"] = df["mkt"] + df["div"] + df["intr"] - df["fees"].fillna(0)
    df["flow"] = df["dep"] - df["wdr"]
    df["ret"] = df["pnl"] / (df["beg"] + 0.5 * df["flow"])

    px = pd.read_csv(ADJ, index_col=0, parse_dates=True)
    spy = px["SPY"].dropna()
    tlt = px["TLT"].dropna()

    def month_end_close(c, y, m, asof):
        if asof:
            return float(c[c.index <= pd.Timestamp(asof)].iloc[-1])
        last = pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)
        return float(c[c.index <= last].iloc[-1])

    say(f"\n{'month':<10}{'begin':>10}{'P&L':>9}{'net flow':>10}"
        f"{'acct':>8}{'SPY':>8}{'60/40':>8}")
    acct = spyc = b64 = 1.0
    prev_s = prev_t = None
    curve = {"acct": [], "spy": [], "b64": [], "idx": []}
    for _, r in df.iterrows():
        s1 = month_end_close(spy, r["year"], r["month"], r["asof"])
        t1 = month_end_close(tlt, r["year"], r["month"], r["asof"])
        if prev_s is None:
            # anchor = prior month end (or the as-of start for row 1)
            start = (pd.Timestamp(r["asof"]) if r["asof"] else
                     pd.Timestamp(r["year"], r["month"], 1)
                     - pd.offsets.Day(1))
            prev_s = float(spy[spy.index <= start].iloc[-1])
            prev_t = float(tlt[tlt.index <= start].iloc[-1])
        rs = s1 / prev_s - 1
        rt = t1 / prev_t - 1
        r64 = 0.6 * rs + 0.4 * rt
        acct *= (1 + r["ret"])
        spyc *= (1 + rs)
        b64 *= (1 + r64)
        curve["acct"].append(acct)
        curve["spy"].append(spyc)
        curve["b64"].append(b64)
        curve["idx"].append(pd.Timestamp(r["year"], r["month"], 1)
                            + pd.offsets.MonthEnd(0))
        say(f"{r['year']}-{r['month']:02d}  {r['beg']:>10,.0f}"
            f"{r['pnl']:>9,.0f}{r['flow']:>10,.0f}"
            f"{r['ret']*100:>+7.2f}%{rs*100:>+7.2f}%{r64*100:>+7.2f}%")
        prev_s, prev_t = s1, t1

    yrs = (curve["idx"][-1] - curve["idx"][0]).days / 365.25 + 1 / 12
    say(f"\n## Chained, Sep 2025 -> Sep 4 2026 (~{yrs*12:.0f} months)")
    for name, key in (("ACCOUNT", "acct"), ("SPY", "spy"),
                      ("60/40", "b64")):
        c = pd.Series(curve[key], index=curve["idx"])
        tot = c.iloc[-1] - 1
        ann = (1 + tot) ** (1 / yrs) - 1
        dd = (c / c.cummax() - 1).min()
        say(f"{name:<10} total {tot*100:+7.2f}%  annualized "
            f"{ann*100:+7.2f}%  maxDD(monthly) {dd*100:+6.2f}%")
    say(f"\nDollar P&L earned (Fidelity's own attribution): "
        f"{df['pnl'].sum():+,.2f} USD on balances ~76-93k; net external "
        f"flow {df['flow'].sum():+,.2f} USD.")
    say("\nNotes: flow timing within a month unknown -> mid-month Dietz "
        "weighting; first and last months are partial (as-of dated); "
        "Fidelity nets same-day equal in/out transfers away; this series "
        "supersedes T9's whole-book attempt (margin correctly inside "
        "Fidelity's Market change) — T9's IRA-only numbers remain the "
        "per-account view.")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.5, 6))
    for name, key in (("ACCOUNT", "acct"), ("SPY", "spy"), ("60/40", "b64")):
        c = pd.Series(curve[key], index=curve["idx"])
        ax.plot(c.index, (c - 1) * 100, lw=1.8, label=name)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_ylabel("cumulative TWR %")
    ax.set_title("T9b — whole book vs SPY vs 60/40 (Sep 2025 – Sep 2026, "
                 "Fidelity's own attribution)")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t9b_fidelity_twr_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nPNG: {p1.name}")

    p = OUT / f"kris_fidelity_twr_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
