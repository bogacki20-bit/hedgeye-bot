"""Two diagnostics:

A. MFR coverage check across the current Signal Strength bucket.
   Produces outputs/ss_to_add_to_mfr.txt with the addable list.

B. SS parser gap audit. Reads the latest SS email body and counts
   distinct tickers vs what parser_signal_strength captured. If real
   email has 40-100+ tickers but parser only got Add/Remove deltas,
   we need a parser overhaul.
"""
import os
import re
import importlib

os.environ.setdefault("CURRENT_MONTHLY_QUAD_OVERRIDE",   "Quad 2")
os.environ.setdefault("CURRENT_QUARTERLY_QUAD_OVERRIDE", "Quad 3")

import db_pg
import tools.active_slice as al
importlib.reload(al); al.invalidate_cache()


# ─────────────────────────── A. MFR coverage ───────────────────────────

bd = al.source_breakdown()
ss = sorted(bd.get("signal_strength", []))
print(f"=== A. MFR coverage across {len(ss)} SS tickers ===\n")

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ticker
              FROM mfr_snapshots
             WHERE snapshot_date >= CURRENT_DATE - interval '7 days'
            """
        )
        mfr_recent = {r[0].strip().upper() for r in cur.fetchall() if r[0]}

        cur.execute(
            """
            SELECT ticker, range_low, range_high
              FROM mfr_snapshots
             WHERE ticker = ANY(%s)
               AND snapshot_date = (
                   SELECT MAX(snapshot_date) FROM mfr_snapshots m2
                    WHERE m2.ticker = mfr_snapshots.ticker
               )
            """,
            (ss,),
        )
        mfr_latest = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

good        = []    # in MFR with usable range
range_gap   = []    # in MFR but range_low/high missing or invalid
no_mfr      = []    # not in MFR at all

for t in ss:
    if t not in mfr_recent:
        no_mfr.append(t)
        continue
    lo, hi = mfr_latest.get(t, (None, None))
    if lo is None or hi is None or lo == hi:
        range_gap.append(t)
    else:
        good.append(t)

print(f"  ✓ SS tickers with usable MFR range : {len(good)}/{len(ss)}")
print(f"  ⚠ SS tickers in MFR but no range  : {len(range_gap)}")
if range_gap: print(f"      {range_gap}")
print(f"  ✗ SS tickers NOT in MFR           : {len(no_mfr)}")
if no_mfr: print(f"      {no_mfr}")


# Write the addable list — split into "probably-addable" and "skip
# (composites / FX / unusual)" using the same heuristic as before.
LIKELY_NOT_MFR = {
    "BRENT", "GOLD", "COPPER", "SILVER", "BITCOIN", "UST10Y", "USD",
    "DAX", "NIKKEI", "SHANGHAI", "HANG_SENG", "KOSPI",
    "SPX", "NDX", "RUT", "COMPQ", "DJIA",
}
addable, skip = [], []
for t in no_mfr:
    if "/" in t or t in LIKELY_NOT_MFR:
        skip.append(t)
    else:
        addable.append(t)

out_dir = r"C:\Projects\hedgeye-bot\outputs"
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "ss_to_add_to_mfr.txt")
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write("# Signal Strength tickers missing from MFR watchlist.\n")
    fh.write(f"# Generated 2026-05-29. SS bucket = {len(ss)} tickers; "
             f"{len(no_mfr)} missing from MFR.\n")
    fh.write(f"# Addable to MFR: {len(addable)}\n")
    fh.write(f"# Likely skip (composites / FX / cash indices): {len(skip)}\n\n")
    fh.write("# ── ADDABLE TO MFR WATCHLIST ──────────────────────────────\n")
    for t in addable: fh.write(t + "\n")
    fh.write("\n")
    fh.write("# ── LIKELY SKIP (FX pairs / macro composites / indices) ──\n")
    for t in skip: fh.write(t + "\n")

print(f"\n  Wrote {out_path}")
print(f"  Addable to MFR : {len(addable)}")
if addable: print(f"      {addable}")
print(f"  Likely skip    : {len(skip)}")
if skip: print(f"      {skip}")


# ─────────────────────────── B. Parser audit ───────────────────────────

print(f"\n=== B. SS parser gap audit ===\n")

TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
BLACKLIST = {
    # English junk-words that look like tickers
    "ADD","ALL","AM","AN","AND","ARE","AS","AT","BE","BUT","BUY","BY","CAN","DO",
    "FOR","FROM","GET","HAS","IF","IN","IS","IT","JUST","KEY","NEW","NO","NOT",
    "NOW","OF","OK","ON","OR","SO","TO","UP","US","WAS","WE","WHO","YES",
    # Hedgeye-specific noise
    "ADD","ADDED","BEST","BUY","BUYS","CALL","DOWN","HEDGEYE","HIGH","IDEA",
    "IDEAS","LIVE","LONG","LONGS","LOW","NEW","PA","PRO","RANK","REMOVED",
    "RISK","SECTOR","SELL","SHORT","SHORTS","SIGNAL","STOCKS","STRENGTH",
    "STOCK","TIM","TODAY","TOP","TREND","TRY","WEEK","WHEN",
    # HTML-noise
    "AM","DIV","ID","SPAN","UTF",
}


def extract_tickers(text: str) -> set[str]:
    """Pull all 1-5 char uppercase tokens that look like tickers."""
    if not text: return set()
    candidates = set(TICKER_RE.findall(text))
    return {t for t in candidates if t not in BLACKLIST and len(t) >= 1}


with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT message_id, subject, received_at, html_body
              FROM hedgeye_emails_raw
             WHERE subject ILIKE '%signal strength%'
             ORDER BY received_at DESC LIMIT 3
            """
        )
        emails = cur.fetchall()

        # Strip HTML to plain text
        from bs4 import BeautifulSoup

        print(f"  Latest 3 SS emails:")
        for mid, subj, recv, body in emails:
            soup = BeautifulSoup(body or "", "html.parser")
            for t in soup(["style","script"]): t.decompose()
            plain = soup.get_text("\n", strip=True)
            tickers = extract_tickers(plain)
            # Cross-check: how many of these tickers exist in mfr_snapshots
            # ever (a proxy for "is this a real ticker the bot knows about")?
            cur.execute(
                "SELECT COUNT(DISTINCT ticker) FROM mfr_snapshots "
                "WHERE ticker = ANY(%s)",
                (sorted(tickers),),
            )
            n_real = cur.fetchone()[0]
            print(f"    {recv}  subject='{subj}'")
            print(f"      regex-extracted tickers: {len(tickers)}")
            print(f"      of those, {n_real} appear in mfr_snapshots (probably real tickers)")
            print(f"      sample: {sorted(tickers)[:40]}")

            # Find the section headers in the email — what's the body shape?
            sections = re.findall(
                r"(Best\s+Idea[s]?\s+(?:Long|Short)[s]?|High\s+Conviction|"
                r"Trending|Best\s+Idea\s+Watch|Added:|Removed:)",
                plain, re.I,
            )
            print(f"      section headers found: {sections}")
            print()

        # What parser_signal_strength got from these same emails:
        cur.execute(
            """
            SELECT snapshot_date, ticker, is_added_today, is_removed_today
              FROM hedgeye_signal_strength
             WHERE snapshot_date >= CURRENT_DATE - interval '5 days'
             ORDER BY snapshot_date DESC, ticker
            """
        )
        parser_caught = cur.fetchall()
        print(f"  parser_signal_strength caught (last 5 days):")
        print(f"    total rows: {len(parser_caught)}")
        added_only   = [r for r in parser_caught if r[2]]
        removed_only = [r for r in parser_caught if r[3]]
        print(f"    is_added_today=True   : {len(added_only)}")
        print(f"    is_removed_today=True : {len(removed_only)}")
        if added_only[:5]: print(f"    sample added: {added_only[:5]}")
