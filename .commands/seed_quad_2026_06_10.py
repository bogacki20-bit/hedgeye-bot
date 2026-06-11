"""Seed bot_state.monthly_quad / .quarterly_quad to 'Quad 3'.

Operator: 2026-06-10. Quad-3 read off the latest macro show. The doctrine
fix (work order item 1) raises QuadUnsetError when these rows are absent,
so this seed has to land before the scanner / monitor can run on the
fresh schema.
"""
import sys, json
sys.path.insert(0, r"C:\Projects\hedgeye-bot")

from dotenv import load_dotenv
load_dotenv(r"C:\Projects\hedgeye-bot\.env")

from tools.quad_regime import set_quads

# One door — writes bot_state (short + legacy keys), appends
# quad_regime_history, fires rotation alert on a real change. Source
# 'operator-seed' so the row is filterable later.
result = set_quads(
    monthly_quad="Quad 3",
    quarterly_quad="Quad 3",
    source="operator-seed",
    notes="2026-06-10 night work order item 1 — operator seed",
    alert_on_change=False,  # operator-initiated; don't ping the user about own action
)
print(json.dumps(result, indent=2, default=str))
