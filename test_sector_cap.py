"""Sector cap tests. Thresholds, fail-closed routing, and THE ENERGY TEST.

Groups 1-5 are PURE (evaluate() takes numbers, no DB). Group 6 needs the
database for classification and is counted as a FAILURE if it cannot run — an
unreachable DB must never make a cap test look green.

Run: python test_sector_cap.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_pg
db_pg._load_dotenv_fallback()          # BEFORE anything reading DATABASE_*_URL

from tools.sector_cap import (ALLOW, WARN, REJECT, REFUSE, WARN_PCT,
                              REJECT_PCT, evaluate, doctrine_pct)

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


AV = 100_000.0     # round account so percentages read directly


def ev(**kw):
    base = dict(ticker="X", side="long", asset_class="equity", sector="ENERGY",
                add_dollars=0.0, current_position_value=0.0,
                current_sector_value=0.0, account_value=AV)
    base.update(kw)
    return evaluate(**base)


# ── 1. thresholds ───────────────────────────────────────────────────────────
print("1. thresholds 8%% warn / 12%% reject:")
check("threshold constants", (WARN_PCT, REJECT_PCT), (8.0, 12.0))
r = ev(current_sector_value=7_900.0)
check("7.9%% -> no warn", r["decision"], ALLOW)
check("7.9%% binding", r["binding"], None)
r = ev(current_sector_value=8_100.0)
check("8.1%% -> WARN", r["decision"], WARN)
check("8.1%% binding", r["binding"], "sector_concentration")
r = ev(current_sector_value=12_100.0)
check("12.1%% -> REJECT", r["decision"], REJECT)
check("12.1%% binding", r["binding"], "sector_concentration")
check("exactly 8.0%% does NOT warn", ev(current_sector_value=8_000.0)["decision"], ALLOW)
check("exactly 12.0%% does NOT reject", ev(current_sector_value=12_000.0)["decision"], WARN)
# the add is what tips it
r = ev(current_sector_value=7_900.0, add_dollars=200.0)
check("7.9%% + 200 add -> WARN", r["decision"], WARN)

# ── 2. equity with NO sector is REFUSED ─────────────────────────────────────
print("\n2. equity with NULL sector is REFUSED (assert on the refusal):")
r = ev(sector=None, current_sector_value=0.0)
check("decision", r["decision"], REFUSE)
check("binding names the cause", r["binding"], "unclassified_sector")
check("reason says REFUSED", "REFUSED" in r["reason"], True)
check("not silently allowed", r["decision"] == ALLOW, False)
check("sector_pct not fabricated", r["sector_pct"], None)

# ── 3. fixed income routes to the 10% doctrine cap, NOT the sector cap ──────
print("\n3. BUXX / CLOX route to the fixed-income ceiling:")
check("doctrine fixed_income pct", doctrine_pct("fixed_income"), 10.0)
check("doctrine equity long pct", doctrine_pct("equity", "long"), 6.0)
check("doctrine equity short pct", doctrine_pct("equity", "short"), 3.0)
check("doctrine commodity pct", doctrine_pct("commodity"), 4.0)
check("doctrine currency pct", doctrine_pct("currency"), 12.0)
for name, val in (("BUXX", 9_000.0), ("CLOX", 2_000.0)):
    r = ev(ticker=name, asset_class="fixed_income", sector=None,
           current_position_value=val)
    check(f"{name} not refused", r["decision"] != REFUSE, True)
    check(f"{name} allowed under 10%", r["decision"], ALLOW)
    check(f"{name} binding is the asset-class cap", r["binding"], "asset_class_cap")
    check(f"{name} NOT caught by sector cap",
          r["binding"] == "sector_concentration", False)
# and it does bind when it should
r = ev(ticker="BUXX", asset_class="fixed_income", sector=None,
       current_position_value=10_500.0)
check("BUXX at 10.5% -> REJECT on the fixed-income cap", r["decision"], REJECT)
check("BUXX reject binding", r["binding"], "asset_class_cap")

# ── 4. neither class nor sector -> REFUSED ──────────────────────────────────
print("\n4. HEFT: no asset class and no sector -> REFUSED:")
r = ev(ticker="HEFT", asset_class="unknown", sector=None)
check("decision", r["decision"], REFUSE)
check("binding", r["binding"], "unclassified_asset")
check("reason mentions default-to-equity", "default" in r["reason"].lower(), True)
# a non-equity class doctrine has no ceiling for must also refuse
r = ev(ticker="ZZZ", asset_class="cash", sector=None)
check("class with no doctrine ceiling -> REFUSED", r["decision"], REFUSE)
check("binding", r["binding"], "no_asset_class_cap")
# no denominator -> refuse
check("zero account value -> REFUSED", ev(account_value=0)["decision"], REFUSE)
check("zero account binding", ev(account_value=0)["binding"], "no_denominator")

# ── 5. per-position vs sector: WHICH RULE BOUND ─────────────────────────────
print("\n5. binding rule is named (position size vs sector concentration):")
# 7% single position, sector otherwise empty -> position cap binds, not sector
r = ev(current_position_value=7_000.0, current_sector_value=7_000.0)
check("7% one name -> REJECT", r["decision"], REJECT)
check("bound by POSITION SIZE", r["binding"], "position_size")
check("reason says so", "POSITION SIZE" in r["reason"], True)
# many small names, none over 6%, sector over 12% -> sector binds
r = ev(current_position_value=1_000.0, current_sector_value=12_500.0)
check("small name, fat sector -> REJECT", r["decision"], REJECT)
check("bound by SECTOR", r["binding"], "sector_concentration")
check("shorts concentrate too (abs exposure)",
      ev(side="short", current_sector_value=-12_500.0)["decision"], REJECT)

# ── 6. THE ENERGY TEST ──────────────────────────────────────────────────────
print("\n6. THE ENERGY TEST — USO UGA XOP OIH HAL, and WHICH rule catches each:")
print("   (a pass where everything is merely 'unclassified' is a FAILURE)")
try:
    from tools.asset_classifier import classify
    ENERGY_BOOK = {"USO": 5_000.0, "UGA": 4_000.0, "XOP": 5_000.0,
                   "OIH": 4_000.0, "HAL": 4_000.0}
    cls = {t: classify(t) for t in ENERGY_BOOK}
    for t in ENERGY_BOOK:
        check(f"{t} classifies (not unknown)",
              cls[t]["asset_class"] != "unknown", True)

    # the three equities must be ENERGY-sectored, not unclassified
    for t in ("XOP", "OIH", "HAL"):
        check(f"{t} asset_class", cls[t]["asset_class"], "equity")
        check(f"{t} sector", cls[t]["sector"], "ENERGY")
    for t in ("USO", "UGA"):
        check(f"{t} asset_class", cls[t]["asset_class"], "commodity")

    # equity leg: XOP+OIH+HAL = 13,000 = 13% -> ENERGY sector cap rejects
    eq_total = sum(ENERGY_BOOK[t] for t in ("XOP", "OIH", "HAL"))
    for t in ("XOP", "OIH", "HAL"):
        r = ev(ticker=t, asset_class="equity", sector="ENERGY",
               current_position_value=ENERGY_BOOK[t],
               current_sector_value=eq_total)
        check(f"{t} REJECTED", r["decision"], REJECT)
        check(f"{t} caught by the ENERGY SECTOR cap", r["binding"],
              "sector_concentration")
        check(f"{t} NOT merely unclassified",
              r["binding"] in ("unclassified_asset", "unclassified_sector"),
              False)
    # commodity leg: USO 5% and UGA 4% vs the 4% commodity ceiling
    r = ev(ticker="USO", asset_class="commodity", sector=None,
           current_position_value=ENERGY_BOOK["USO"])
    check("USO REJECTED", r["decision"], REJECT)
    check("USO caught by the 4% COMMODITY cap", r["binding"], "asset_class_cap")
    check("USO reason names commodity", "commodity" in r["reason"], True)
    r = ev(ticker="UGA", asset_class="commodity", sector=None,
           current_position_value=4_100.0)
    check("UGA at 4.1% REJECTED", r["decision"], REJECT)
    check("UGA caught by the 4% COMMODITY cap", r["binding"], "asset_class_cap")

    # the whole point: the book is caught, and by TWO DIFFERENT rules
    bindings = set()
    for t in ("XOP", "OIH", "HAL"):
        bindings.add(ev(ticker=t, asset_class="equity", sector="ENERGY",
                        current_position_value=ENERGY_BOOK[t],
                        current_sector_value=eq_total)["binding"])
    for t in ("USO", "UGA"):
        bindings.add(ev(ticker=t, asset_class="commodity", sector=None,
                        current_position_value=ENERGY_BOOK[t] + 1_000.0)["binding"])
    check("caught by BOTH rules, not one catch-all",
          sorted(bindings), ["asset_class_cap", "sector_concentration"])
except Exception as e:
    print(f"  !! ENERGY TEST COULD NOT RUN ({e}) — counted as FAILURE.")
    FAIL += 1

# ── 7. F1 taxonomy: XLU is capped, broad market is exempt ONLY by membership ─
print("\n7. XLU is a SECTOR BET and must be capped, not exempted:")
try:
    from tools.asset_classifier import classify, BROAD_MARKET, COUNTRY_FUND
    x = classify("XLU")
    check("XLU asset_class", x["asset_class"], "equity")
    check("XLU sector is UTILITIES", x["sector"], "UTILITIES")
    check("XLU bucket_kind is sector", x["bucket_kind"], "sector")
    check("XLU is NOT broad-market", "XLU" in BROAD_MARKET, False)
    r = ev(ticker="XLU", sector="UTILITIES", bucket_kind="sector",
           bucket="UTILITIES", current_sector_value=12_500.0)
    check("XLU over 12% -> REJECT", r["decision"], REJECT)
    check("XLU binds on sector_concentration", r["binding"], "sector_concentration")
    check("XLU not exempted", r["decision"] == ALLOW, False)
    r = ev(ticker="XLU", sector="UTILITIES", bucket_kind="sector",
           bucket="UTILITIES", current_sector_value=9_000.0)
    check("XLU at 9% -> WARN", r["decision"], WARN)
except Exception as e:
    print(f"  !! could not run ({e})")
    FAIL += 1

print("\n8. THE EXEMPTION GUARD — no sector must NOT mean exempt:")
# a name that is NOT in BROAD_MARKET and has no sector must be REFUSED
r = ev(ticker="MYSTERY", sector=None, bucket_kind=None, bucket=None)
check("unlisted sectorless name -> REFUSE", r["decision"], REFUSE)
check("binding", r["binding"], "unclassified_sector")
check("NOT allowed", r["decision"] == ALLOW, False)
check("reason says membership is required",
      "explicit" in r["reason"].lower(), True)
# and a real BROAD_MARKET member IS exempt from concentration
r = ev(ticker="NOBL", sector=None, bucket_kind="broad", bucket=None,
       current_position_value=3_000.0, current_sector_value=99_000.0)
check("NOBL exempt from concentration despite a huge 'sector' figure",
      r["decision"], ALLOW)
check("NOBL binding is not concentration", r["binding"], None)
# but the per-position cap STILL applies to broad market
r = ev(ticker="NOBL", sector=None, bucket_kind="broad", bucket=None,
       current_position_value=7_000.0)
check("NOBL at 7% still hits the 6% position cap", r["decision"], REJECT)
check("NOBL binding is position_size", r["binding"], "position_size")
try:
    check("every BROAD_MARKET member is explicitly listed",
          sorted(BROAD_MARKET),
          ["FAB", "HDV", "NOBL", "RSP", "SPLV", "VYM"])
    for t in ("XLU", "EPHE", "LMT", "CBRL", "HEFT", "QTUM", "WOOD", "TSLQ"):
        check(f"{t} is NOT in BROAD_MARKET", t in BROAD_MARKET, False)
except Exception as e:
    print(f"  !! {e}")
    FAIL += 1

print("\n9. COUNTRY funds get their own rule, not a silent exemption:")
try:
    c = classify("EPHE")
    check("EPHE bucket_kind", c["bucket_kind"], "country")
    check("EPHE bucket", c["bucket"], "PHILIPPINES")
    r = ev(ticker="EPHE", sector=None, bucket_kind="country",
           bucket="PHILIPPINES", current_sector_value=12_500.0)
    check("country over 12% -> REJECT", r["decision"], REJECT)
    check("binds on country_concentration", r["binding"], "country_concentration")
    check("NOT counted as a sector", r["binding"] == "sector_concentration", False)
    r = ev(ticker="EPHE", sector=None, bucket_kind="country",
           bucket="PHILIPPINES", current_sector_value=5_000.0)
    check("country at 5% -> allow", r["decision"], ALLOW)
except Exception as e:
    print(f"  !! could not run ({e})")
    FAIL += 1

print("\n10. F1d/F1e assignments resolve:")
try:
    for t, want in (("QTUM", "GLOBAL TECH"), ("WOOD", "MATERIALS"),
                    ("TSLQ", "INDUSTRIALS"), ("LMT", "INDUSTRIALS"),
                    ("CBRL", "RESTAURANTS")):
        c = classify(t)
        check(f"{t} sector", c["sector"], want)
        check(f"{t} participates in the cap", c["bucket_kind"], "sector")
    h = classify("HEFT")
    check("HEFT still unknown", h["asset_class"], "unknown")
    check("HEFT has no bucket", h["bucket"], None)
    check("HEFT refused by the cap",
          ev(ticker="HEFT", asset_class="unknown", sector=None,
             bucket_kind=None, bucket=None)["decision"], REFUSE)
except Exception as e:
    print(f"  !! could not run ({e})")
    FAIL += 1

# ── 11. F2: no fabricated denominator ───────────────────────────────────────
print("\n11. decision_engine declines to size rather than assuming 50,000:")
import inspect
import decision_engine as de
src = inspect.getsource(de)
check("no 'account_value_usd = 50_000.0' fallback remains",
      "account_value_usd = 50_000.0" in src, False)
check("UnresolvedAccountValue is handled by name",
      "UnresolvedAccountValue" in src, True)
check("declines by setting None", "account_value_usd = None" in src, True)
from portfolio import account_value, UnresolvedAccountValue
try:
    account_value("KM13868186")
    check("closed account raises", False, True)
except UnresolvedAccountValue:
    check("closed account raises UnresolvedAccountValue", True, True)


# ── 12. G5: THE WIRING — assert on the LIVE call sites, not the library ─────
print("\n12. WIRING: both caps evaluate, the tighter binds, verdict names it:")
from tools.sector_cap import clamp_size, handle_cap_command

# 1. breaches BOTH -> the TIGHTER one binds, and BOTH are named.
# "Tighter" = smaller RAW headroom (ceiling - current), which may be negative.
# NOTE: the first version of this case used pos=7000/sector=13000, which is a
# degenerate TIE (both -1000 of room) -- "tighter" is undefined there, so that
# was a bad test case. These two are unambiguous in opposite directions.
r = ev(current_position_value=9_000.0, current_sector_value=13_000.0)
check("breaches both -> REJECT", r["decision"], REJECT)
check("position room -3000 vs sector -1000 -> position_size is tighter",
      r["binding"], "position_size")
check("reason names BOTH ceilings", "BOTH ceilings breached" in r["reason"], True)
check("reason warns that fixing one is not enough",
      "will not unblock" in r["reason"], True)
r = ev(current_position_value=6_500.0, current_sector_value=20_000.0)
check("sector room -8000 vs position -500 -> sector is tighter",
      r["binding"], "sector_concentration")
check("verdict still reports the sector figure", round(r["sector_pct"], 1), 20.0)
# 2. sector only
r = ev(current_position_value=1_000.0, current_sector_value=12_500.0)
check("sector only -> REJECT", r["decision"], REJECT)
check("binding is sector_concentration", r["binding"], "sector_concentration")
# 3. per-position only
r = ev(current_position_value=6_500.0, current_sector_value=6_500.0)
check("position only -> REJECT", r["decision"], REJECT)
check("binding is position_size", r["binding"], "position_size")
check("reason distinguishes it from concentration",
      "POSITION SIZE" in r["reason"], True)

print("\n13. LIVE PATH: recommender.size_for is actually gated:")
import inspect
import recommender as _rc
_src = inspect.getsource(_rc.size_for)
check("size_for calls the sector cap", "clamp_size" in _src, True)
check("doctrine cap still present (alongside, not instead of)",
      "check_position_cap" in _src, True)
check("binding rule recorded", 'debug["clamped_by"]' in _src, True)
import decision_engine as _de
_dsrc = inspect.getsource(_de)
check("decision_engine sizing is gated too", _dsrc.count("clamp_size") >= 2, True)

print("\n14. PSX add is REJECTED through the WIRED path (needs DB):")
try:
    allowed, v = clamp_size("PSX", 500.0, side="long", account="Individual")
    check("clamp_size allows 0 extra dollars", allowed, 0.0)
    check("decision is REJECT", v["decision"], REJECT)
    check("bound by sector concentration", v["binding"], "sector_concentration")
    check("bucket is ENERGY", v["bucket"], "ENERGY")
    # and the sizing function itself returns a clamped size
    dollars, dbg = _rc.size_for("Adding", "Individual", ticker="PSX", side="long")
    check("size_for returns 0 for PSX", float(dollars or 0.0), 0.0)
    check("size_for names the binding rule", dbg.get("clamped_by"),
          "sector_concentration")
    check("size_for carries the cap verdict", "sector_cap" in dbg, True)
    # contrast: a name with headroom is NOT clamped to zero
    ok_d, ok_v = clamp_size("OKTA", 500.0, side="long", account="Individual")
    check("OKTA is allowed", ok_d, 500.0)
    check("OKTA decision", ok_v["decision"], ALLOW)
    # the CAP command answers
    out = handle_cap_command("CAP PSX 500")
    check("CAP command returns a verdict", "REJECT" in out, True)
    check("CAP names the sector", "ENERGY" in out, True)
    check("CAP shows projected pct", "projected" in out, True)
    check("CAP declines non-CAP text", handle_cap_command("SCREEN energy longs"),
          None)
except Exception as e:
    print(f"  !! WIRED-PATH TEST COULD NOT RUN ({e}) — counted as FAILURE.")
    FAIL += 1

print("\n" + ("ALL PASS" if FAIL == 0 else f"{FAIL} FAILURE(S)"))
sys.exit(1 if FAIL else 0)
