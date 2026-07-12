"""_llm_cost_model.py — READ-ONLY: what would the LLM paths cost to run,
modeled from YOUR actual volumes (items table) and prompt sizes.

Covers:
  1. email classifier (every Hedgeye email -> 1 call) at sonnet-5 vs
     haiku-4.5 — the decide-whether-to-revive question
  2. screenshot OCR (per Tier One Alpha shot) at haiku-4.5
  3. DAYPACK / REPORT / REPORT NOW: $0 — pure Python, no LLM anywhere

Prices are $/MTok, ESTIMATES from the repo's own decision_engine table +
public haiku pricing — VERIFY at console.anthropic.com before trusting
the absolute dollars; the RATIOS are solid either way.
    python _llm_cost_model.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

# $/MTok (input, output) — estimate, date-stamped 2026-07-11
PRICES = {
    "claude-sonnet-5 (est=sonnet-4-5 tier)": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
SYS_PROMPT_TOK = 900        # classifier SYSTEM_PROMPT+corpus block, rough
OUT_TOK_CLASSIFY = 300      # JSON reply
OCR_IMG_TOK = 1600          # ~one phone screenshot
OCR_OUT_TOK = 900           # transcribed text

with db_pg.get_conn() as c, c.cursor() as cur:
    try:
        cur.execute("""
            SELECT count(*)::float / 30.0,
                   COALESCE(avg(length(coalesce(text_body, html_body, ''))), 0)
            FROM hedgeye_emails_raw
            WHERE received_at >= CURRENT_DATE - 30""")
    except Exception:
        c.rollback()
        cur.execute("""
            SELECT count(*)::float / 30.0,
                   COALESCE(avg(length(coalesce(html_body, ''))), 0)
            FROM hedgeye_emails_raw
            WHERE received_at >= CURRENT_DATE - 30""")
    emails_per_day, avg_chars = cur.fetchone()
    emails_per_day, avg_chars = float(emails_per_day or 0), float(avg_chars or 0)

in_tok = SYS_PROMPT_TOK + (avg_chars or 0) / 4.0
print(f"measured: {emails_per_day:.1f} emails/day (30d avg) · "
      f"avg content {avg_chars:,.0f} chars ≈ {in_tok:,.0f} input tok/call\n")

print("1) EMAIL CLASSIFIER (if revived — currently every email calls it):")
for name, (pin, pout) in PRICES.items():
    per_call = (in_tok * pin + OUT_TOK_CLASSIFY * pout) / 1e6
    daily = per_call * emails_per_day
    print(f"   {name:<38} ${per_call:.4f}/email · "
          f"${daily:.2f}/day · ${daily * 30:.2f}/mo")

print("\n2) SCREENSHOT OCR (per Tier One Alpha shot, haiku-4.5):")
pin, pout = PRICES["claude-haiku-4-5"]
per_shot = (OCR_IMG_TOK * pin + OCR_OUT_TOK * pout) / 1e6
for shots in (5, 10, 20):
    print(f"   {shots:>2} shots/day: ${per_shot * shots:.3f}/day · "
          f"${per_shot * shots * 30:.2f}/mo   (${per_shot:.4f}/shot)")

print("\n3) DAYPACK / REPORT / REPORT NOW / SCREEN / alerts: $0 — no LLM.")
print("\nprices are ESTIMATES — verify $/MTok at console.anthropic.com; "
      "volumes and token sizes above are yours.")
