"""
_wrapper_flag_audit.py — READ-ONLY wrapper ⚠-flag audit (open bug, 2026-07-11).

SBIT/EUO flag trend-against correctly; METD/GGLS/MSFD/YCS/SQQQ show NO ⚠ in
book_alerts/REPORT despite broken theses per SCREEN. This dumps every stage of
BOTH pipelines per held wrapper so the divergence point is visible:

  stage 1  book_sides()            raw_side / side / net / via_linkage
  stage 2  wrapper_links           underlying / inverse (link row present at all?)
  stage 3  book_alerts frame       _fetch_source_slice -> base trend -> +btcq ->
                                   +wrapper (u_trend, adjusted trend) -> rp
  stage 4  _book_rows() actual     the row detect()/REPORT really see -> against?
  stage 5  SCREEN frame            _fetch_book_slice -> same overrides ->
                                   has_range -> _thesis (ranged rows only)
  stage 6  bot_state               book_trend_state entry (explains missing
                                   trend_flip alerts: no transition = no alert)

Verdict column compares stage-4 `against` vs stage-5 `_thesis` — MISMATCH is
the bug. Writes NOTHING. Run on the Lenovo:
    python _wrapper_flag_audit.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# load .env if vars not already present (same fallback as _book_sides_verify)
if not (os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")):
    try:
        for ln in open(os.path.join(os.path.dirname(__file__), ".env")):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

from tools.book_direction import book_sides
from tools.wrapper_links import get_links
from tools.screener import (_fetch_source_slice, _fetch_book_slice,
                            _apply_btcquant_trend, _apply_wrapper_trend,
                            _underlying_trends)
from tools.book_alerts import _book_rows

BUG_FIVE = ["METD", "GGLS", "MSFD", "YCS", "SQQQ"]
KNOWN_OK = ["SBIT", "EUO"]

sides = book_sides()
links = get_links()
held = sorted(t for t, v in sides.items() if v.get("side") in ("long", "short"))
held_wrappers = [t for t in held if t in links]
focus = list(dict.fromkeys(KNOWN_OK + BUG_FIVE + held_wrappers))

print(f"book: {len(held)} sided underlyings; {len(links)} wrapper links; "
      f"{len(held_wrappers)} held wrappers: {' '.join(held_wrappers) or 'NONE'}")
missing_link = [t for t in BUG_FIVE if t not in links]
if missing_link:
    print(f"🛑 NO wrapper_links row for: {' '.join(missing_link)} "
          f"(no link = wrapper keeps its OWN thin trend everywhere)")
not_held = [t for t in BUG_FIVE + KNOWN_OK if t not in held]
if not_held:
    print(f"note: not currently sided/held per book_sides: {' '.join(not_held)}")

# ── book_alerts frame, stage by stage (same calls as _book_rows) ──
src = _fetch_source_slice(held, None)
base_td = {r["ticker"]: (r.get("trend_dir"), r.get("trend_source")) for r in src}
stage = copy.deepcopy(src)
_apply_btcquant_trend(stage)
btcq_td = {r["ticker"]: r.get("trend_dir") for r in stage}
_apply_wrapper_trend(stage)
ba_slice = {r["ticker"]: r for r in stage}

# underlying trends, resolved the way _apply_wrapper_trend resolves them
u_list = [links[w]["underlying"] for w in focus if w in links]
utrends = _underlying_trends(u_list) if u_list else {}

# ── stage 4: the rows detect()/REPORT actually consume ──
ba_rows = {r["ticker"]: r for r in _book_rows()}

# ── stage 5: SCREEN book frame (replicates run_screen_q book_mode) ──
scr = _fetch_book_slice(None)
_apply_btcquant_trend(scr)
_apply_wrapper_trend(scr)
scr_by_t = {r["ticker"]: r for r in scr}

# ── stage 6: trend-flip state ──
import db_pg, json
state = {}
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("SELECT value FROM bot_state WHERE key='book_trend_state'")
    r = cur.fetchone()
    state = json.loads(r[0]) if r and r[0] else {}

print()
hdr = (f"{'tkr':<6}{'raw':<6}{'expo':<6}{'link':<14}{'u_trend':<8}"
       f"{'base_td':<9}{'adj_td':<9}{'src':<5}{'rp':<6}{'rng':<4}"
       f"{'BA_agnst':<9}{'SCR_⚠':<7}{'state':<9}verdict")
print(hdr)
print("-" * len(hdr))
mismatches = []
for t in focus:
    v = sides.get(t) or {}
    raw, expo = v.get("raw_side") or "—", v.get("side") or "—"
    lk = links.get(t)
    link_s = (f"{lk['underlying']}{'↯inv' if lk['inverse'] else ''}" if lk else "NO-LINK")
    utd = (utrends.get(lk["underlying"]) or "?") if lk else "—"

    br = ba_slice.get(t)
    b_base = (base_td.get(t) or (None, None))[0] or "—"
    if btcq_td.get(t) not in (None, b_base):
        b_base += f"→{btcq_td[t]}(btcq)"
    adj = (br.get("trend_dir") or "—") if br else "NOT-IN-SLICE"
    src_tag = (br.get("trend_source") or "—") if br else "—"

    ar = ba_rows.get(t)
    if ar:
        s2, td2 = ar["side"], ar.get("trend_dir") or ""
        against = ((s2 == "long" and td2 == "BEARISH") or
                   (s2 == "short" and td2 == "BULLISH"))
        ba_s, rp = ("⚠YES" if against else "no"), ar.get("rp_now")
        rp_s = f"{rp:.2f}" if rp is not None else "?"
    else:
        against, ba_s, rp_s = None, "NO-ROW", "—"

    sr = scr_by_t.get(t)
    if sr:
        td3 = sr.get("trend_dir") or ""
        thesis = ((raw == "long" and td3 == "BEARISH") or
                  (raw == "short" and td3 == "BULLISH"))
        rng = "yes" if sr.get("has_range") else "NO"
        scr_s = ("⚠YES" if thesis else "no") + ("" if sr.get("has_range") else "*")
    else:
        thesis, rng, scr_s = None, "—", "NO-ROW"

    st = state.get(t) or "—"
    if against is None or thesis is None:
        verdict = "⛔ missing from a frame"
    elif against != thesis:
        verdict = "🛑 MISMATCH"
    elif thesis and st == (sr.get("trend_dir") or "?"):
        verdict = "ok (state seeded broken → flip alert can never fire)"
    else:
        verdict = "ok"
    if verdict.startswith("🛑") or verdict.startswith("⛔"):
        mismatches.append(t)
    print(f"{t:<6}{raw:<6}{expo:<6}{link_s:<14}{utd:<8}{b_base:<9}{adj:<9}"
          f"{src_tag:<5}{rp_s:<6}{rng:<4}{ba_s:<9}{scr_s:<7}{str(st):<9}{verdict}")

print("\n[*] = SCREEN computes ⚠ over RANGED rows only; has_range=NO means the "
      "wrapper renders in SCREEN's DARK section WITHOUT a thesis flag.")

# frame math on the counts disagreement (58L/5S vs 10 exposure-shorts)
raw_l = sum(1 for v in sides.values() if v.get("raw_side") == "long")
raw_s = sum(1 for v in sides.values() if v.get("raw_side") == "short")
exp_l = sum(1 for v in sides.values() if v.get("side") == "long")
exp_s = sum(1 for v in sides.values() if v.get("side") == "short")
ba_l = sum(1 for r in ba_rows.values() if r["side"] == "long")
ba_s2 = sum(1 for r in ba_rows.values() if r["side"] == "short")
print(f"\nCOUNT FRAMES  raw {raw_l}L/{raw_s}S · exposure {exp_l}L/{exp_s}S · "
      f"REPORT-line ({len(ba_rows)} rows w/ coverage) {ba_l}L/{ba_s2}S")
print("REPORT counts use _book_rows side = RAW frame; SCREEN 'book shorts' "
      "counts EXPOSURE shorts — a raw-vs-exposure gap here is labeling, not math.")

print(f"\n{'🛑 ' + str(len(mismatches)) + ' frame mismatch(es): ' + ' '.join(mismatches) if mismatches else '✅ frames agree on every focus name'}")
