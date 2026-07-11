"""READ-ONLY options post-mortem over outcomes_log (rebuilt corpus).
    python _options_postmortem.py
Splits realized P&L options vs equity, ranks option underlyings, shows the
monthly bleed curve and hold-time pattern. Writes nothing."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_pg

with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT ticker, normalized_ticker, pnl_dollars, was_winner,
               holding_period_minutes, closed_at
        FROM outcomes_log""")
    rows = cur.fetchall()

opts   = [r for r in rows if (r[0] or "").startswith("-")]
equity = [r for r in rows if not (r[0] or "").startswith("-")]

def stats(rs):
    if not rs:
        return "none"
    tot = sum(float(r[2]) for r in rs)
    w = sum(1 for r in rs if r[3])
    return f"n={len(rs):<5} win={w}/{len(rs)} ({w/len(rs)*100:.0f}%)  P&L=${tot:,.2f}"

print(f"ALL     {stats(rows)}")
print(f"EQUITY  {stats(equity)}")
print(f"OPTIONS {stats(opts)}")

print("\nOPTIONS by underlying (worst first):")
by_u: dict = {}
for r in opts:
    by_u.setdefault(r[1], []).append(r)
for u, rs in sorted(by_u.items(), key=lambda kv: sum(float(r[2]) for r in kv[1])):
    tot = sum(float(r[2]) for r in rs)
    w = sum(1 for r in rs if r[3])
    days = [float(r[4]) / 1440 for r in rs if r[4] is not None]
    avg_d = f"{sum(days)/len(days):.0f}d" if days else "?"
    print(f"  {u:<8} legs={len(rs):<3} win={w:<3} avg_hold={avg_d:<5} P&L=${tot:>10,.2f}")

print("\nOPTIONS P&L by month closed:")
by_m: dict = {}
for r in opts:
    m = str(r[5])[:7] if r[5] else "?"
    by_m[m] = by_m.get(m, 0.0) + float(r[2])
for m in sorted(by_m):
    print(f"  {m}   ${by_m[m]:>10,.2f}")

print("\nEQUITY P&L by month closed:")
by_m = {}
for r in equity:
    m = str(r[5])[:7] if r[5] else "?"
    by_m[m] = by_m.get(m, 0.0) + float(r[2])
for m in sorted(by_m):
    print(f"  {m}   ${by_m[m]:>10,.2f}")
