"""One-shot diagnostic: list polling-universe tickers missing MFR data.

Writes outputs/mfr_to_add.txt and prints summary + ticker list.
Read-only; not part of the production pipeline. Safe to delete after use.
"""
import os
import importlib
from collections import Counter

import tools.active_slice as al
importlib.reload(al)
al.invalidate_cache()

import db_pg

uni = set(al.polling_universe())
bd  = al.source_breakdown()

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ticker FROM mfr_snapshots "
            "WHERE snapshot_date >= CURRENT_DATE - interval '7 days'"
        )
        have_mfr = {r[0].strip().upper() for r in cur.fetchall() if r[0]}
        cur.execute(
            "SELECT DISTINCT ticker FROM hedgeye_risk_ranges "
            "WHERE signal_date >= CURRENT_DATE - interval '7 days'"
        )
        have_rr = {r[0].strip().upper() for r in cur.fetchall() if r[0]}

missing = sorted(uni - have_mfr)


def buckets_for(t):
    return [k for k in al._POLLING_INCLUDED_KEYS if t in bd.get(k, [])]


# FX pairs / macro composites / cash indices MFR typically doesn't list.
# Anything containing '/' is FX-style; the explicit set catches the
# Hedgeye macro composites (BRENT, GOLD, COPPER, BITCOIN, UST10Y, etc.)
# and the cash indices (SPX, NDX, RUT, COMPQ).
LIKELY_NOT_MFR = {
    'BRENT', 'GOLD', 'COPPER', 'SILVER', 'BITCOIN', 'UST10Y', 'USD',
    'DAX', 'NIKKEI', 'SHANGHAI', 'HANG_SENG', 'KOSPI',
    'SPX', 'NDX', 'RUT', 'COMPQ', 'DJIA',
}

out_dir = r'C:\Projects\hedgeye-bot\outputs'
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, 'mfr_to_add.txt')

mfr_addable = []
likely_skip = []
for t in missing:
    is_fx = '/' in t
    is_composite = t in LIKELY_NOT_MFR
    if is_fx or is_composite:
        likely_skip.append(t)
    else:
        mfr_addable.append(t)

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('# Polling universe minus tickers with an MFR snapshot in the last 7 days.\n')
    f.write('# Generated 2026-05-26 against the post-d430ae2 strict-current polling universe.\n')
    f.write(f'# polling universe size: {len(uni)}\n')
    f.write(f'# total missing:         {len(missing)}\n')
    f.write(f'# addable to MFR:        {len(mfr_addable)}\n')
    f.write(f'# likely skip (FX / macro composite / cash index): {len(likely_skip)}\n')
    f.write('\n')
    f.write('# ── ADDABLE TO MFR WATCHLIST ───────────────────────────────────────\n')
    for t in mfr_addable:
        f.write(t + '\n')
    f.write('\n')
    f.write('# ── LIKELY SKIP (FX pairs / macro composites / cash indices) ──────\n')
    for t in likely_skip:
        f.write(t + '\n')

print(f'Wrote {out_path}')
print()
print('=== Summary ===')
print(f'  polling universe   : {len(uni)}')
print(f'  have MFR (7d)      : {len(uni & have_mfr)}')
print(f'  MISSING MFR        : {len(missing)}')
print(f'    of those, with RR: {sum(1 for t in missing if t in have_rr)}')
print(f'    of those, no RR  : {sum(1 for t in missing if t not in have_rr)}')
print(f'  addable to MFR     : {len(mfr_addable)}')
print(f'  likely skip        : {len(likely_skip)}')

print()
print(f'=== Source-bucket distribution among the {len(missing)} missing ===')
src_counter: Counter = Counter()
for t in missing:
    for k in buckets_for(t):
        src_counter[k] += 1
for k in sorted(src_counter, key=lambda x: -src_counter[x]):
    print(f'  {k:25} {src_counter[k]:>4}')

print()
print(f'=== ADDABLE list ({len(mfr_addable)}) ===')
print('\n'.join(mfr_addable))
print()
print(f'=== LIKELY-SKIP list ({len(likely_skip)}) ===')
print('\n'.join(likely_skip))
