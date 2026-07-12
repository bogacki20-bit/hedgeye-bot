"""_llm_ledger.py — READ-ONLY: what the LLM paths ACTUALLY cost, from the
bot's own llm_calls ledger (decision_engine logs every call with an
estimated USD). Shows weekly spend by caller/model over 90d — both the
real recommender bill and the date calls went dark (the classifier
outage starved it of trade_signal triggers).
    python _llm_ledger.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""
        SELECT date_trunc('week', called_at)::date AS wk, caller,
               count(*), sum(est_cost_usd), avg(est_cost_usd)
        FROM llm_calls
        WHERE called_at >= CURRENT_DATE - 90
        GROUP BY 1, 2 ORDER BY 1, 4 DESC""")
    rows = cur.fetchall()
    if not rows:
        print("llm_calls empty for 90d — no LLM spend recorded at all.")
        sys.exit(0)
    print(f"{'week':<12}{'caller':<22}{'calls':>6}{'total$':>9}{'avg$/call':>11}")
    wk_tot = {}
    for wk, caller, n, tot, avg in rows:
        wk_tot[wk] = wk_tot.get(wk, 0) + float(tot or 0)
        print(f"{str(wk):<12}{caller:<22}{n:>6}{float(tot or 0):>9.2f}"
              f"{float(avg or 0):>11.4f}")
    print("\nweekly totals:")
    for wk in sorted(wk_tot):
        v = wk_tot[wk]
        print(f"  {wk}  ${v:>7.2f}  {'█' * min(int(v * 2), 40)}")
    cur.execute("SELECT max(called_at) FROM llm_calls")
    print(f"\nlast recorded LLM call: {cur.fetchone()[0]}")
