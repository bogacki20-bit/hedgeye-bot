"""TradingView TRADE/TREND levels — the bot's Alpha Code blend (Kris, 9/26).

    TREND = MFR Trend daily band   (bullish: lower edge · bearish: upper edge)
    TRADE = Vola2vola daily line   (bullish: upper line · bearish: lower line)

Tested 9/26 against 11 of Keith's published levels: TREND exact on SPX/Gold,
TRADE exact on SPX. Every tested code was bullish; the bearish mirror is an
assumption until the Alpha Code scorecard says otherwise.

Overnight path (docs/TV_LEVELS.md): scheduled task injects tools/tv_levels.js
into Kris's TradingView tab, collects rows into data/tv_levels/<date>.jsonl,
then:
    py -m tools.tv_levels --pairs            # JSON [[ticker, tv_symbol], ...] for the job
    py -m tools.tv_levels --ingest <jsonl>   # upsert tv_levels + misses, print coverage
    py -m tools.tv_levels --show XLE SPX     # latest derived TRADE/TREND
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "tv_levels"
MISMATCH_PCT = 0.25
STALE_BAR_DAYS = 10


def pairs() -> list[list[str]]:
    import db_pg
    from tools.tv_symbols import tv_symbol
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM v_screener ORDER BY held DESC NULLS LAST, ticker")
        tickers = [t for (t,) in cur.fetchall()]
        # + Hedgeye Risk Range names (SPX, BRENT, UST10Y, ...) — Keith's Alpha
        # Codes are quoted on these, and MARKET prints VIX / UST10Y from them
        cur.execute("SELECT DISTINCT ticker FROM hedgeye_risk_ranges "
                    "WHERE signal_date >= CURRENT_DATE - 30 ORDER BY 1")
        tickers += [t for (t,) in cur.fetchall() if t not in set(tickers)]
    return [[t, tv_symbol(t)] for t in tickers if tv_symbol(t)]


def _parse_line(ln: str) -> dict:
    """JSON object, or the compact pipe form __tvLines() emits:
    t|bar|close|mu|ml|va|vb|tvsym   or   t|MISS|tvsym|reason"""
    ln = ln.strip()
    if ln.startswith("{"):
        return json.loads(ln)
    p = ln.split("|")
    if len(p) >= 2 and p[1] == "MISS":
        return {"t": p[0], "s": p[2] if len(p) > 2 else None, "miss": "|".join(p[3:]) or "miss"}
    num = lambda x: None if x in ("", "null", "None", "undefined") else float(x)
    return {"t": p[0], "bar": p[1], "close": num(p[2]), "mu": num(p[3]), "ml": num(p[4]),
            "va": num(p[5]), "vb": num(p[6]), "s": p[7] if len(p) > 7 else None}


def ingest(path: str) -> dict:
    import db_pg
    rows = [_parse_line(ln) for ln in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    today = dt.date.today()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        # symbol sanity (9/26: bare "CORN" resolved to a corn-futures CFD at 533,
        # not the $19.70 Teucrium ETF). A close > MISMATCH_PCT away from the
        # bot's own price is the wrong instrument -> miss, never stored.
        cur.execute("SELECT ticker, COALESCE(mfr_price, price) FROM v_screener "
                    "WHERE ticker = ANY(%s)", ([r["t"] for r in rows],))
        ref = {t: float(p) for t, p in cur.fetchall() if p}
        for r in rows:
            p, c = ref.get(r["t"]), r.get("close")
            if not r.get("miss") and p and c and abs(float(c) / p - 1) > MISMATCH_PCT:
                r["miss"] = f"symbol mismatch: {r.get('s')} closed {c} vs bot price {p:g}"
            # a delisted symbol still resolves, on a bar from months ago (NKLA 2025-02-25)
            if not r.get("miss") and r.get("bar") and \
                    (today - dt.date.fromisoformat(r["bar"])).days > STALE_BAR_DAYS:
                r["miss"] = f"stale bar {r['bar']} (delisted/halted?)"
        ok, miss = [r for r in rows if not r.get("miss")], [r for r in rows if r.get("miss")]
        for r in ok:
            cur.execute(
                "INSERT INTO tv_levels (ticker, bar_date, tv_symbol, mfr_trend_upper, mfr_trend_lower, "
                "vola_a, vola_b, bar_close) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (ticker, bar_date) DO UPDATE SET tv_symbol=EXCLUDED.tv_symbol, "
                "mfr_trend_upper=EXCLUDED.mfr_trend_upper, mfr_trend_lower=EXCLUDED.mfr_trend_lower, "
                "vola_a=EXCLUDED.vola_a, vola_b=EXCLUDED.vola_b, bar_close=EXCLUDED.bar_close, captured_at=now()",
                (r["t"], r["bar"], r.get("s"), r.get("mu"), r.get("ml"), r.get("va"), r.get("vb"), r.get("close")))
        for r in miss:
            cur.execute(
                "INSERT INTO tv_levels_misses (ticker, run_date, tv_symbol, reason) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (ticker, run_date) DO UPDATE SET tv_symbol=EXCLUDED.tv_symbol, reason=EXCLUDED.reason",
                (r["t"], today, r.get("s"), r["miss"][:200]))
        conn.commit()
    return {"rows": len(rows), "stored": len(ok), "misses": len(miss),
            "missed": [r["t"] for r in miss][:40]}


def derive(mu, ml, va, vb, trend_dir: str | None) -> dict:
    """Pure: raw indicator values + trend direction -> {trend, trade, basis}."""
    vals = [x for x in (va, vb) if x is not None]
    bear = (trend_dir or "").upper().startswith("BEAR")
    out = {"trend": None, "trade": None, "basis": "bear-mirror" if bear else "bull"}
    if mu is not None and ml is not None:
        out["trend"] = float(mu) if bear else float(ml)
    if vals:
        out["trade"] = float(min(vals)) if bear else float(max(vals))
    return out


def latest(tickers: list[str]) -> dict:
    """{ticker: {trend, trade, basis, bar_date}} from the newest tv_levels row."""
    import db_pg
    if not tickers:
        return {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (l.ticker) l.ticker, l.bar_date, l.mfr_trend_upper, l.mfr_trend_lower, "
            "l.vola_a, l.vola_b, s.trend_dir FROM tv_levels l LEFT JOIN v_screener s USING (ticker) "
            "WHERE l.ticker = ANY(%s) ORDER BY l.ticker, l.bar_date DESC", (tickers,))
        out = {}
        for t, bd, mu, ml, va, vb, td in cur.fetchall():
            d = derive(mu, ml, va, vb, td)
            d["bar_date"] = bd
            out[t] = d
        return out


def _cli() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pairs", action="store_true")
    g.add_argument("--ingest")
    g.add_argument("--show", nargs="+")
    a = ap.parse_args()
    import db_pg
    db_pg._load_dotenv_fallback()
    if a.pairs:
        print(json.dumps(pairs(), separators=(",", ":")))
    elif a.ingest:
        print(json.dumps(ingest(a.ingest)))
    else:
        for t, d in latest([x.upper() for x in a.show]).items():
            print(f"{t:8} TREND {d['trend']:.2f}  TRADE {d['trade']:.2f}  ({d['basis']}, bar {d['bar_date']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
