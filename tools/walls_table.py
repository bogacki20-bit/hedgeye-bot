"""walls_table.py — per-day SpotGamma dealer-positioning tables for the
research packets.

Operator ask (9/20): the daily/weekly packets should carry the EquityHub
options data so the desk LLM can track the RATE OF CHANGE of dealer
positioning — walls migrating day over day — especially on held names.

build_walls_table(day)          one day: indexes/sectors + that day's HELD
                                names (point-in-time book snapshot <= day),
                                each row with day-over-day wall deltas.
build_walls_week(start, end)    per-day tables + a WEEK ROC section that
                                shows each held name's wall/ivr/dpi PATH
                                across the range (the rate-of-change view).

Data: spotgamma_snapshots (EquityHub capture, ~5.2K tickers/day). Deltas
compare against that ticker's most recent PRIOR snapshot (never forward —
point-in-time). Thin-OI caveat printed on every artifact.
"""

from __future__ import annotations

import datetime as dt

INDEXES = ["SPY", "QQQ", "IWM", "SMH"]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
           "XLU", "XLV", "XLY"]
CASH_LIKE = {"CLOX", "BUXX", "VTIP", "DBMF", "FDRXX", "SPAXX", "CORE"}

CAVEAT = ("⚠ walls are reliable on liquid options (indexes, megacaps, liquid "
          "ETFs) and NOISE on sparse-OI names — IV rank is the liquidity tell.")


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def _held_on(day: dt.date) -> list[str]:
    r = _rows("""
        SELECT DISTINCT underlying FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions
                               WHERE snapshot_date <= %s)
          AND asset_class <> 'cash'""", (day,))
    return sorted({t for (t,) in r if t and t not in CASH_LIKE})


def _snap(tickers: list[str], day: dt.date) -> dict:
    """{ticker: row} for `day` plus each ticker's most recent PRIOR row."""
    if not tickers:
        return {}
    r = _rows("""
        WITH cur AS (
            SELECT DISTINCT ON (ticker) ticker, snapshot_date, price,
                   call_wall, hedge_wall, put_wall, iv_rank, dpi,
                   put_call_oi_ratio, options_implied_move, earnings_date
            FROM spotgamma_snapshots
            WHERE ticker = ANY(%s) AND snapshot_date <= %s
            ORDER BY ticker, snapshot_date DESC),
        prev AS (
            SELECT DISTINCT ON (s.ticker) s.ticker,
                   s.call_wall pcw, s.hedge_wall phw, s.put_wall ppw
            FROM spotgamma_snapshots s JOIN cur c ON c.ticker = s.ticker
            WHERE s.snapshot_date < c.snapshot_date
            ORDER BY s.ticker, s.snapshot_date DESC)
        SELECT c.*, p.pcw, p.phw, p.ppw
        FROM cur c LEFT JOIN prev p ON p.ticker = c.ticker""",
        (tickers, day))
    return {row[0]: row for row in r}


def gate_walls(cw, hw, pw, spot):
    """(cw, hw, pw, note) — sanity-gate a dealer-wall triple (9/20 audit).

    - INVERTED MAP: call wall below put wall is structurally impossible
      (LFST cw 10 / pw 12). The whole map is untrusted -> all None.
    - HEDGE-WALL NOISE: the Vol Trigger is a dense-chain construct; on
      sparse single-name chains it parks 39-93% from spot (TXG hw 5 vs
      $76) or swings >2x in a week (DELL 40->400->500->40). Gate hw when
      it sits >35% from spot; calls/puts stay (they were stable on the
      same names). Index/sector ETF walls never tripped either test.
    """
    cw = float(cw) if cw is not None else None
    hw = float(hw) if hw is not None else None
    pw = float(pw) if pw is not None else None
    spot = float(spot) if spot else None
    if cw is not None and pw is not None and cw < pw:
        return None, None, None, "walls-inverted"
    if hw is not None and spot and abs(hw - spot) / spot > 0.35:
        return cw, None, pw, "hw-gated"
    return cw, hw, pw, None


def _f(v, fmt="{:g}"):
    return fmt.format(float(v)) if v is not None else "·"


def _delta(cur, prev):
    if cur is None or prev is None or not float(prev):
        return ""
    d = (float(cur) - float(prev)) / float(prev) * 100
    return f" ({d:+.1f}%)" if abs(d) >= 1.0 else ""


def _line(row) -> str:
    (t, sd, px, cw, hw, pw, ivr, dpi, pc, oim, earn,
     pcw, phw, ppw) = row
    cw, hw, pw, note = gate_walls(cw, hw, pw, px)
    if note == "walls-inverted":
        wall_s = "⋄BROKEN-MAP (cw<pw — walls excluded)"
    else:
        wall_s = (f"⋄{_f(cw)}{_delta(cw, pcw)}/{_f(hw)}{_delta(hw, phw)}"
                  f"/{_f(pw)}{_delta(pw, ppw)}"
                  + (" (hw gated: >35% from spot)" if note == "hw-gated" else ""))
    bits = [f"{t:<6} px {_f(px)}", wall_s]
    if ivr is not None:
        bits.append(f"ivr {float(ivr) * 100:.0f}%")   # stored as 0-1 fraction
    if dpi is not None:
        bits.append(f"dpi {float(dpi):.2f}")
    if pc is not None:
        bits.append(f"p/c {float(pc):.2f}")
    if oim is not None:
        bits.append(f"±{float(oim):.1f}%")
    if earn:
        bits.append(f"earn {earn}")
    return "  ".join(bits)


def build_walls_table(day: dt.date) -> str:
    held = _held_on(day)
    uni = INDEXES + SECTORS + [t for t in held
                               if t not in INDEXES and t not in SECTORS]
    snap = {t: r for t, r in _snap(uni, day).items()
            if r[1] >= day - dt.timedelta(days=3)}   # no stale rows
    out = [f"SPOTGAMMA WALLS — {day}  (⋄call/hedge/put · Δ vs prior day ≥1%)",
           CAVEAT, "", "— indexes & sectors —"]
    out += [_line(snap[t]) for t in INDEXES + SECTORS if t in snap]
    out += ["", f"— held names ({sum(1 for t in held if t in snap)}"
                f"/{len(held)} covered) —"]
    out += [_line(snap[t]) for t in held if t in snap]
    miss = [t for t in held if t not in snap]
    if miss:
        out.append(f"(no EquityHub row: {' '.join(miss)})")
    return "\n".join(out)


def build_walls_week(start: dt.date, end: dt.date) -> str:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)

    out = [f"SPOTGAMMA WALLS — WEEK {start} → {end}", CAVEAT, ""]
    for d in days:
        out += [f"════ {d:%A %m/%d} ════", build_walls_table(d), ""]

    # rate-of-change paths: one line per name, walls/ivr across the days
    held = _held_on(end)
    uni = INDEXES + SECTORS + [t for t in held
                               if t not in INDEXES and t not in SECTORS]
    by_day = {d: _snap(uni, d) for d in days}
    out += ["════ WEEK RATE-OF-CHANGE (wall paths, first day → last) ════",
            "ticker: call-wall path | hedge-wall path | put-wall path | ivr path", ""]
    for t in uni:
        paths = [[], [], [], []]
        for d in days:
            r = by_day[d].get(t)
            fresh = r is not None and r[1] >= d - dt.timedelta(days=3)
            gcw, ghw, gpw, _n = (gate_walls(r[3], r[4], r[5], r[2])
                                 if fresh else (None, None, None, None))
            for i, v in enumerate((gcw, ghw, gpw,
                                   r[6] if fresh else None)):
                if v is None:
                    paths[i].append("·")
                elif i == 3:   # iv_rank stored as 0-1 fraction
                    paths[i].append(f"{float(v) * 100:.0f}")
                else:
                    paths[i].append(_f(v))
        if all(all(x == "·" for x in p) for p in paths):
            continue
        cw, hw, pw, ivr = ["→".join(p) for p in paths]
        out.append(f"{t:<6} cw {cw} | hw {hw} | pw {pw} | ivr {ivr}")
    return "\n".join(out)


def main() -> int:
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) == 2:
        text = build_walls_week(dt.date.fromisoformat(args[0]),
                                dt.date.fromisoformat(args[1]))
        name = f"walls_week_{args[0]}_{args[1]}.txt"
    else:
        day = dt.date.fromisoformat(args[0]) if args else dt.date.today()
        text = build_walls_table(day)
        name = f"walls_{day}.txt"
    print(text)
    if "--send" in sys.argv:
        import os
        from telegram_handler import _send_message
        _send_message(os.environ["TELEGRAM_BOT_TOKEN"],
                      os.environ["TELEGRAM_CHAT_ID"],
                      {"document_name": name, "document_text": text,
                       "caption": "⋄ SpotGamma walls table — dealer positioning "
                                  "rate-of-change"})
        print("\nsent", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
