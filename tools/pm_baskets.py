"""pm_baskets.py — Position Monitor baskets: trending returns by Hedgeye sector.

Operator 10/20 (from Keith on the 10/19 Macro Show: the PM's active longs /
shorts are built off Signal Strength, at least in Small Caps and Energy):
for every Hedgeye sector, the ACTIVE + TOP-IDEA long basket and short basket,
equal-weighted 1w / 1m / 3m returns, the long-minus-short spread, and how
much of each basket is on the matching Signal Strength list.

Returns: current v_screener price vs the mfr_snapshots close on/just before
each horizon (same method as MARKET FULL's TRENDING RETURNS). A name with no
history at a horizon is left out of that horizon's average (n shown).

Telegram: PM BASKETS   ·   CLI: py -m tools.pm_baskets
"""
from __future__ import annotations

SENTINELS = ("PM BASKETS", "PM BASKET", "BASKETS")
LONG_B = ("active_long", "top_idea_long")
SHORT_B = ("active_short", "top_idea_short")


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def basket_stats(names: list[str], rets: dict) -> dict:
    """Pure: equal-weight average per horizon over names that have it."""
    out = {}
    for h in ("1w", "1m", "3m"):
        vals = [rets[t][h] for t in names if t in rets and h in rets[t]]
        out[h] = (sum(vals) / len(vals), len(vals)) if vals else (None, 0)
    return out


def build() -> str:
    from tools.market_update import _returns, RET_HORIZONS
    pm = _rows("SELECT ticker, COALESCE(hedgeye_group,'(ungrouped)'), hedgeye_bucket_0629 "
               "FROM ticker_tags WHERE hedgeye_bucket_0629 = ANY(%s)", (list(LONG_B + SHORT_B),))
    tickers = sorted({t for t, _, _ in pm})
    data = {t: {"px": px} for t, px in _rows(
        "SELECT ticker, price FROM v_screener WHERE ticker = ANY(%s) AND price IS NOT NULL",
        (tickers,))}
    _returns(data)
    rets = {t: d.get("ret") or {} for t, d in data.items()}
    ss = {}
    for t, side in _rows("SELECT ticker, side FROM ss_roster_history "
                         "WHERE removed_on IS NULL AND ticker = ANY(%s)", (tickers,)):
        ss.setdefault(t, set()).add(side)
    pm_date = _rows("SELECT max(note_date) FROM doc_uploads WHERE kind='position_monitor'")[0][0]

    groups: dict = {}
    for t, g, b in pm:
        d = groups.setdefault(g, {"long": [], "short": []})
        d["long" if b in LONG_B else "short"].append(t)

    def cell(v):
        return f"{v[0]:+6.1f}%" if v[0] is not None else "    n/a"

    def ss_pct(names, side):
        if not names:
            return "  -"
        n = sum(1 for t in names if side in ss.get(t, set()))
        return f"{n}/{len(names)}"

    lines = [f"📊 PM BASKETS — Position Monitor {pm_date} · active + top-idea, equal weight",
             "  1w / 1m / 3m · spread = long basket − short basket (a working book is +) · "
             "SS = names on the matching Signal Strength list", ""]
    rows = []
    all_l, all_s = [], []
    for g, d in groups.items():
        L, S = basket_stats(d["long"], rets), basket_stats(d["short"], rets)
        spread = {h: ((L[h][0] - S[h][0]), 0) if L[h][0] is not None and S[h][0] is not None
                  else (None, 0) for h in ("1w", "1m", "3m")}
        rows.append((g, d, L, S, spread))
        all_l += d["long"]
        all_s += d["short"]
    # rank by 1m spread (sectors with only one side sort by that side)
    def key(r):
        v = r[4]["1m"][0]
        if v is None:
            v = (r[2]["1m"][0] or 0) - (r[3]["1m"][0] or 0)
        return -v
    rows.sort(key=key)
    for g, d, L, S, sp in rows:
        lines.append(f"{g}  (L {len(d['long'])} · S {len(d['short'])})")
        if d["long"]:
            lines.append(f"  L   {'  '.join(cell(L[h]) for h in ('1w','1m','3m'))}   "
                         f"SS-long {ss_pct(d['long'], 'long')}")
        if d["short"]:
            lines.append(f"  S   {'  '.join(cell(S[h]) for h in ('1w','1m','3m'))}   "
                         f"SS-short {ss_pct(d['short'], 'short')}")
        if d["long"] and d["short"]:
            lines.append(f"  L−S {'  '.join(cell(sp[h]) for h in ('1w','1m','3m'))}")
    AL, AS = basket_stats(all_l, rets), basket_stats(all_s, rets)
    lines += ["", f"ALL SECTORS  (L {len(all_l)} · S {len(all_s)})",
              f"  L   {'  '.join(cell(AL[h]) for h in ('1w','1m','3m'))}   SS-long {ss_pct(all_l, 'long')}",
              f"  S   {'  '.join(cell(AS[h]) for h in ('1w','1m','3m'))}   SS-short {ss_pct(all_s, 'short')}",
              "  L−S " + "  ".join(
                  cell(((AL[h][0] - AS[h][0]), 0) if AL[h][0] is not None and AS[h][0] is not None
                       else (None, 0)) for h in ("1w", "1m", "3m"))]
    missing = sorted(t for t in tickers if not rets.get(t))
    if missing:
        lines.append(f"\nno price history (left out): {' '.join(missing)}")
    return "\n".join(lines)


def handle_pm_baskets_command(text: str):
    if (text or "").strip().upper() not in SENTINELS:
        return None
    return build()


if __name__ == "__main__":
    print(build())
