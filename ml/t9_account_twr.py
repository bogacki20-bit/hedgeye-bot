"""ml/t9_account_twr.py — T9: account-level time-weighted return.

The budget-honest, compounding-correct answer to "how did I actually
do": Modified Dietz between consecutive Fidelity snapshots (whole book,
all accounts summed), external flows (EFT deposits) stripped, chained
into a TWR curve. Benchmarks: SPY and 60/40 SPY/TLT (ADJUSTED closes,
so benchmark dividends are counted just as the account's own dividends
are inside its equity). Window = snapshot coverage only:
2026-05-11 -> 2026-09-04. Internal journals/options/interest stay in
(they are performance); only external money in/out is a flow.

    py ml/t9_account_twr.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

SQLITE = r"C:\data\hedgeye.db"
OUT = REPO / "reports"
ADJ = REPO / "data" / "reference" / "t6_px_adj.csv"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def read_csv_aum() -> pd.Series:
    """Total account value per snapshot date, CASH INCLUDED, read straight
    from the raw Portfolio_Positions CSVs (the DB parser skips cash rows,
    which makes securities-only AUM useless for Dietz: deposits vanish
    into cash and every buy looks like performance). One file per date —
    largest wins; every Current value row counts (SPAXX, pending, all)."""
    import csv as _csv
    import re as _re
    files = {}
    for p in Path(r"C:\Users\bogac\Downloads").glob("Portfolio_Positions_*.csv"):
        m = _re.search(r"Portfolio_Positions_([A-Za-z]{3})-(\d{2})-(\d{4})",
                       p.name)
        if not m:
            continue
        d = pd.Timestamp(f"{m.group(3)}-{m.group(1)}-{m.group(2)}")
        if d not in files or p.stat().st_size > files[d].stat().st_size:
            files[d] = p
    vals = {}
    for d, p in files.items():
        tot = 0.0
        n = 0
        with p.open(newline="", encoding="utf-8-sig") as fh:
            rd = _csv.DictReader(fh)
            key = next((k for k in (rd.fieldnames or [])
                        if k and k.strip().lower() == "current value"), None)
            if key is None:
                continue
            for r in rd:
                raw = (r.get(key) or "").replace("$", "").replace(",", "")
                try:
                    tot += float(raw)
                    n += 1
                except ValueError:
                    continue
        if n:
            vals[d] = tot
    return pd.Series(vals).sort_index()


def read_csv_aum_per_account() -> pd.DataFrame:
    """Per-account cash-inclusive value per snapshot date from the raw
    CSVs (largest file per date wins; every Current value row counts)."""
    import csv as _csv
    import re as _re
    files = {}
    for p in Path(r"C:\Users\bogac\Downloads").glob(
            "Portfolio_Positions_*.csv"):
        m = _re.search(r"Portfolio_Positions_([A-Za-z]{3})-(\d{2})-(\d{4})",
                       p.name)
        if not m:
            continue
        d = pd.Timestamp(f"{m.group(3)}-{m.group(1)}-{m.group(2)}")
        if d not in files or p.stat().st_size > files[d].stat().st_size:
            files[d] = p
    rows = []
    for d, p in sorted(files.items()):
        per = {"date": d}
        with p.open(newline="", encoding="utf-8-sig") as fh:
            rd = _csv.DictReader(fh)
            cols = {(k or "").strip().lower(): k
                    for k in (rd.fieldnames or [])}
            vk, ak = cols.get("current value"), cols.get("account number")
            if vk is None:
                continue
            for r in rd:
                raw = (r.get(vk) or "").replace("$", "").replace(",", "")
                try:
                    v = float(raw)
                except ValueError:
                    continue
                acct = (r.get(ak) or "?").strip()
                per[acct] = per.get(acct, 0.0) + v
        rows.append(per)
    return pd.DataFrame(rows).set_index("date").sort_index()


def dietz(series: pd.Series, flows: pd.DataFrame) -> list[tuple]:
    dates = list(series.index)
    out = []
    for i in range(1, len(dates)):
        t0, t1 = dates[i - 1], dates[i]
        v0, v1 = float(series.loc[t0]), float(series.loc[t1])
        w = flows[(flows.run_date > t0) & (flows.run_date <= t1)]
        F = float(w["amount"].sum())
        wt = sum(float(a) * ((t1 - d).days / max(1, (t1 - t0).days))
                 for d, a in zip(w["run_date"], w["amount"]))
        r = (v1 - v0 - F) / (v0 + wt) if (v0 + wt) > 0 else np.nan
        out.append((t1, r, F))
    return out


def main() -> int:
    conn = sqlite3.connect(SQLITE)
    fl = pd.read_sql_query(
        "SELECT run_date, amount, action FROM portfolio_transactions "
        "WHERE upper(action) LIKE '%ELECTRONIC FUNDS TRANSFER%' "
        "OR upper(action) LIKE '%DEPOSIT%' AND upper(action) NOT LIKE "
        "'%FDIC%'", conn)
    conn.close()
    per = read_csv_aum_per_account()
    fl["run_date"] = pd.to_datetime(fl["run_date"], format="%m/%d/%Y")
    fl = fl[fl["amount"].abs() > 0]
    no_flows = fl.iloc[0:0]

    say(f"# T9 — account TWR (Modified Dietz), {date.today()}")
    say(f"\nSnapshots: {len(per)}, {per.index.min().date()} .. "
        f"{per.index.max().date()}, per-account, CASH INCLUDED (read "
        f"from the raw CSVs — the DB parser drops cash rows, which is "
        f"why a first pass looked catastrophic).")
    say("\n**The Individual account (X96383748) has NO computable TWR "
        "from these exports**: it runs a margin debit, and Fidelity "
        "position exports show assets only, never the loan. Deposits "
        "that pay down the debit look like vanished money "
        "(gross assets 28,063 -> 21,747 while 18,800 of EFT deposits "
        "landed). Its true equity return needs the Balances export or "
        "Fidelity's own Performance page. Reported below: the two "
        "margin-free, flow-free accounts, where Dietz is exact.")

    px = pd.read_csv(ADJ, index_col=0, parse_dates=True)
    spy = px["SPY"].dropna()
    tlt = px["TLT"].dropna()

    def bench(series, closes):
        out = []
        for i in range(1, len(series.index)):
            t0, t1 = series.index[i - 1], series.index[i]
            out.append(float(closes[closes.index <= t1].iloc[-1]
                             / closes[closes.index <= t0].iloc[-1] - 1))
        return out

    curves = {}
    say(f"\n## May 11 -> Sep 4, chained (IRAs: no margin, no flows)")
    for name, col in (("Rollover IRA", "244859926"),
                      ("Roth IRA", "245734604")):
        s = per[col].dropna()
        rs = dietz(s, no_flows)
        c = pd.Series(np.cumprod([1.0] + [1 + r for _t, r, _f in rs]),
                      index=[s.index[0]] + [t for t, _r, _f in rs])
        curves[name] = c
    spy_r = bench(per["244859926"].dropna(), spy)
    tlt_r = bench(per["244859926"].dropna(), tlt)
    idx = curves["Rollover IRA"].index
    curves["SPY"] = pd.Series(np.cumprod([1.0] + [1 + r for r in spy_r]),
                              index=idx)
    curves["60/40"] = pd.Series(
        np.cumprod([1.0] + [1 + 0.6 * a + 0.4 * b
                            for a, b in zip(spy_r, tlt_r)]), index=idx)
    for name, c in curves.items():
        tot = c.iloc[-1] - 1
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        ann = (1 + tot) ** (1 / yrs) - 1
        dd = (c / c.cummax() - 1).min()
        say(f"{name:<14} total {tot*100:+6.2f}%  annualized "
            f"{ann*100:+7.2f}%  maxDD(snapshot) {dd*100:+6.2f}%")

    say("\n## Individual account — raw series (NOT a return; margin "
        "debit invisible)")
    ind = per["X96383748"].dropna()
    say(f"gross assets {ind.iloc[0]:,.0f} -> {ind.iloc[-1]:,.0f} USD; "
        f"EFT deposits in window: "
        f"{fl[(fl.run_date > ind.index[0]) & (fl.run_date <= ind.index[-1])]['amount'].sum():+,.0f} USD. "
        f"Change net of deposits: "
        f"{ind.iloc[-1] - ind.iloc[0] - fl[(fl.run_date > ind.index[0]) & (fl.run_date <= ind.index[-1])]['amount'].sum():+,.0f} USD "
        f"— an UPPER BOUND on losses only if the margin debit was "
        f"unchanged, which we cannot verify from these files.")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.5, 6))
    for name in ("Rollover IRA", "Roth IRA", "SPY", "60/40"):
        c = curves[name]
        ax.plot(c.index, (c - 1) * 100, lw=1.8, label=name)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_ylabel("cumulative TWR %")
    ax.set_title("T9 — IRA TWR vs SPY vs 60/40 (May 11 – Sep 4, 2026)")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t9_account_twr_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nPNG: {p1.name}")
    say("\nCaveats: window = snapshot coverage (2026-05-11 on; earlier "
        "book invisible); benchmarks on adjusted closes; IRA numbers "
        "are exact Dietz (no margin, no external flows); the whole-book "
        "and Individual numbers are NOT computable without margin "
        "balances — get them from Fidelity's Performance page or a "
        "Balances export.")

    p = OUT / f"kris_account_twr_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
