"""_classifier_damage.py — READ-ONLY: email volume + routing over 60d
(from hedgeye_emails_raw — the 'items' table never made it to Postgres;
the classifier result is used IN-MEMORY only, so the outage window doesn't
show in the DB — it shows as missing decision_engine recommendations) +
LIVE smoke tests of the replacement models.
    python _classifier_damage.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""
        SELECT received_at::date, count(*)
        FROM hedgeye_emails_raw
        WHERE received_at >= CURRENT_DATE - 14
        GROUP BY 1 ORDER BY 1""")
    print("emails/day (14d):")
    for d, n in cur.fetchall():
        print(f"  {d}  {n:>3}  {'█' * min(n, 40)}")
    cur.execute("""
        SELECT COALESCE(classified_as, '(none)'), count(*)
        FROM hedgeye_emails_raw
        WHERE received_at >= CURRENT_DATE - 60
        GROUP BY 1 ORDER BY 2 DESC""")
    print("\nrouting mix (60d, classified_as = dedicated-parser label):")
    for k, n in cur.fetchall():
        print(f"  {k:<28} {n:>5}")

print("\nLIVE smoke test of replacement models:")
if not os.environ.get("ANTHROPIC_API_KEY"):        # .env fallback
    try:
        for ln in open(os.path.join(os.path.dirname(__file__), ".env")):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass
import anthropic
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
for label, model in (
        ("classifier", os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-5")),
        ("OCR", os.environ.get("OCR_MODEL", "claude-haiku-4-5-20251001")),
        ("decision_engine", os.environ.get("DECISION_ENGINE_MODEL",
                                           "claude-sonnet-4-5"))):
    try:
        r = client.messages.create(model=model, max_tokens=20,
                                   messages=[{"role": "user",
                                              "content": "Reply with only: OK"}])
        print(f"  {label:<16} {model:<28} {r.content[0].text.strip()!r}  ✅")
    except Exception as e:
        print(f"  {label:<16} {model:<28} FAILED — {e}")
