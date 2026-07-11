"""_apply_063.py — REPORT v4.1 calibration: migrations 062+063 (idempotent)
+ SIMULATED pre-commit dry run (v4.1 FIX 1-4, operator-specced 2026-07-11).

    python _apply_063.py            # dry run: renders the POST-SEED state
                                    #   (BUXX casheq + 4 FIX2 reroutes) as an
                                    #   in-memory SIMULATION — the DB is not
                                    #   written beyond CREATE/ALTER TABLE.
    python _apply_063.py --commit   # writes the seeds for real + stores the
                                    #   first snapshot + report row.

Seeds (CONFIRM = the --commit flag, per operator spec):
  BUXX -> cash_equivalent (SHY deliberately NOT — stays an fi position)
  FUTY 4% · BNDD 10% · TUA 10% · ULS 2%  (explicit TARGET rows, dominant acct)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re
import db_pg

commit = "--commit" in sys.argv

for mig in ("062_position_targets.sql", "063_cash_equivalent.sql"):
    sql = open(os.path.join(os.path.dirname(__file__), "migrations", mig)).read()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
    print(f"applied (idempotent): {mig}")

from tools.book_direction import book_sides
from tools.position_targets import compute_fills
from tools.report import build_book_table, build_report_v4, store_report

sides = book_sides()
REROUTES = {"FUTY": 4.0, "BNDD": 10.0, "TUA": 10.0, "ULS": 2.0}
SIM_CASHEQ = {"BUXX"}

if commit:
    from tools.position_targets import set_cash_equivalent
    print(set_cash_equivalent("BUXX", True), "(FIX 1 seed)")
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        pre = compute_fills(cur, sides)
        for tkr, pct in REROUTES.items():
            acct = ((pre["agg"].get(tkr) or {}).get("acct") or "IND").split("+")[0]
            cur.execute(
                """INSERT INTO position_targets
                     (ticker, account, target_pct, set_date, note)
                   VALUES (%s,%s,%s,CURRENT_DATE,'v4.1 FIX2 reroute')
                   ON CONFLICT (ticker, account) DO UPDATE SET
                     target_pct=EXCLUDED.target_pct, set_date=EXCLUDED.set_date,
                     note=EXCLUDED.note""", (tkr, acct, pct))
            print(f"  TARGET seeded: {tkr} = {pct:g}% of {acct} (FIX 2)")
        conn.commit()
    sim_t, sim_c = None, None            # seeds are real now — no simulation
    mode = "REAL RUN (seeds written)"
else:
    sim_t, sim_c = REROUTES, SIM_CASHEQ
    mode = "DRY RUN — SIMULATED post-seed state, DB untouched"

with db_pg.get_conn() as conn, conn.cursor() as cur:
    fills = compute_fills(cur, sides, sim_targets=sim_t, sim_casheq=sim_c)

print(f"\n=== POSITION TABLE [{mode}] ===")
print(f"({len(fills['agg'])} underlyings · gross ${fills['gross']:,.0f} · "
      f"parked: {' '.join(f'{t} ${v:,.0f}' for t, v in sorted(fills['cash_equiv'].items())) or 'none'})")
print(build_book_table(fills))

# ── FIX 4 verification prints ──
over = [(t, c["fill"]) for t, c in sorted(fills["agg"].items())
        if c["bucket"] == "OVER"]
atmax = [(t, c["fill"]) for t, c in sorted(fills["agg"].items())
         if c["bucket"] == "FULL"]
print("\nOVER (>110% of target): "
      + (" ".join(f"{t} {f:.0f}%" for t, f in over) or "none"))
print("AT-MAX (80-110%):       "
      + (" ".join(f"{t} {f:.0f}%" for t, f in atmax) or "none"))
print("expected ~ (operator 7/11): OVER shrinks to genuinely oversized; "
      "BNDD ~94% AT-MAX; UNH/BRKR/WM/CLOX/LLY near max")

ts = fills["target_sum_pct"]
if ts is not None:
    warn = "  ⚠ >150% — defaults still too fat" if ts > 150 else "  ✓"
    print(f"\nTARGET-SUM SANITY: targets sum to {ts:.0f}% of total account "
          f"value{warn}")

if fills["guessed"]:
    print(f"\nGUESSED TIERS — unverified, tier from description alone "
          f"({len(fills['guessed'])}):")
    print("  " + " ".join(fills["guessed"]))

# ── FIX 2 re-scan: PROPOSE (never apply) reroutes for the two patterns ──
_STOCK_RE = re.compile(r"\bINC\b|\bCORP\b|\bCO\b|\bPLC\b|\bLTD\b|\bCLASS [A-C]\b"
                       r"|\bCL [A-C]\b|\bHOLDINGS\b|\bGROUP\b", re.I)
_SECTOR_RE = re.compile(r"\bUTILITIES\b|\bENERGY\b|\bFINANCIALS?\b|\bHEALTH\b"
                        r"|\bTECHNOLOGY\b|\bINDUSTRIALS?\b|\bMATERIALS\b"
                        r"|\bCONSUMER\b|\bSTAPLES\b|\bDISCRETIONARY\b"
                        r"|\bREAL ESTATE\b|\bCOMMUNICATION\b|\bSECTOR\b"
                        r"|\bAEROSPACE\b|\bSEMICONDUCTOR\b|\bINSURANCE\b", re.I)
proposals, seen = [], set()
for t, acct, pct, tgt, src, fill, bucket, pl, _g in fills["per_acct"]:
    if t in REROUTES or src is None or t in seen:
        continue
    seen.add(t)
    d = fills["descr"].get(t, "")
    if src in ("dflt-core", "dflt-sat") and _STOCK_RE.search(d) and \
            not re.search(r"\bETF\b|\bFUND\b|\bTRUST\b|\bINDEX\b", d, re.I):
        proposals.append(f"{t} {src}→eq 2%  [{acct}]  looks like a stock: {d[:45]}")
    elif src == "dflt-sat" and _SECTOR_RE.search(d):
        proposals.append(f"{t} {src}→core 4%  [{acct}]  sector/broad ETF: {d[:45]}")
print(f"\nPROPOSED REROUTES ({len(proposals)}) — NOT applied; confirm any via "
      f"TARGET <tkr> <pct> [acct] → CONFIRM TARGET:"
      if proposals else "\nPROPOSED REROUTES: none — no stock-routed-fund or "
                        "sector-routed-sat patterns left")
for p in proposals:
    print("  " + p)

if "DESK" in fills["agg"]:
    c = fills["agg"]["DESK"]
    f_s = f"{c['fill']:.0f}%" if c["fill"] is not None else "?"
    print(f"\nDESK CHECK: src={c['tgt_src'] or 'explicit'} tgt={c['tgt']}% "
          f"fill={f_s} acct={c['acct']} · descr: "
          f"{fills['descr'].get('DESK', 'NO DESCRIPTION')[:60]}")
if "SHY" in fills["agg"]:
    print("SHY: fi position by design — NOT cash-equivalent (⚠ stays on "
          "purpose). Later: TARGET CASHEQ SHY if wanted.")

print(f"\n=== REPORT v4.1 compact / Telegram [{mode}] ===")
body = build_report_v4(kind="on-demand", persist_snapshot=commit,
                       fills_override=fills)
print(body)
n = len(body)
print(f"\n[compact: {n} chars vs 3500 target]"
      + ("  ⚠ over — DIV auto-collapsed or needs another trim" if n > 3500
         else "  ✓"))

if commit:
    store_report(body, "on-demand")
    print("(stored: report_rows + report_snapshots; seeds live)")
else:
    up = build_report_v4(kind="upload", verbose=True, persist_snapshot=False,
                         fills_override=fills)
    print(f"[upload mode: {len(up)} chars — .txt via REPORT UPLOAD]")
    print("\nDRY RUN complete — everything above is the SIMULATED post-seed "
          "state; nothing written. If it matches expectations:")
    print("    python _apply_063.py --commit")
