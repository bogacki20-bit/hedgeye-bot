"""Full correlation matrix + book risk-cluster read.
Layer 1: full-universe pairwise return correlation over 30/60/90d, upserted daily
to correlation_matrix (latest snapshot only, not appended). Layer 2: among HELD
positions, names with |corr|>=0.70 group into risk clusters; each cluster = one
independent bet ('55 positions -> N bets'). Prices batched from volume_signal."""
from __future__ import annotations
import sys, logging, datetime as dt
from typing import Optional
log = logging.getLogger(__name__)
WINDOWS = (30, 60, 90)
CLUSTER_WINDOW = 60
CLUSTER_THRESH = 0.70

def _universe() -> list:
    try:
        from tools.volume_signal import _all_assets
        return _all_assets()
    except Exception as e:
        log.warning("correlation: universe lookup failed (%s)", e); return []

def _fetch_returns(names, lookback):
    from tools.volume_signal import fetch_ohlcv_batch
    batch = fetch_ohlcv_batch(names, lookback)
    out = {}
    for t, bars in batch.items():
        if len(bars) < min(WINDOWS) + 2: continue
        rets = {}
        for i in range(1, len(bars)):
            p0 = bars[i-1][1]
            if p0: rets[bars[i][0]] = bars[i][1]/p0 - 1.0
        if rets: out[t] = rets
    return out

def pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 5: return None
    xs, ys = xs[-n:], ys[-n:]; mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x,y in zip(xs,ys)); vx = sum((x-mx)**2 for x in xs); vy = sum((y-my)**2 for y in ys)
    if vx <= 0 or vy <= 0: return None
    return cov/(vx**0.5*vy**0.5)

def book_clusters(pairs, held, window=CLUSTER_WINDOW, thresh=CLUSTER_THRESH):
    held_set = set(held); parent = {t: t for t in held_set}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for p in pairs:
        a, b, c = p["a"], p["b"], p.get(window)
        if a in held_set and b in held_set and c is not None and abs(c) >= thresh: union(a, b)
    groups = {}
    for t in held_set: groups.setdefault(find(t), []).append(t)
    clusters = sorted((sorted(v) for v in groups.values()), key=len, reverse=True)
    return clusters, len(clusters)

def _compute():
    names = _universe()
    if not names: return {"tickers": [], "pairs": []}
    rets = _fetch_returns(names, max(WINDOWS) + 25)
    tickers = sorted(rets); pairs = []
    for i in range(len(tickers)):
        ra = rets[tickers[i]]
        for j in range(i+1, len(tickers)):
            a, b = tickers[i], tickers[j]; rb = rets[b]
            common = sorted(set(ra) & set(rb))
            if len(common) < min(WINDOWS): continue
            xa = [ra[d] for d in common]; xb = [rb[d] for d in common]
            row = {"a": a, "b": b, "n": len(common)}; has = False
            for w in WINDOWS:
                c = pearson(xa[-w:], xb[-w:]); row[w] = round(c,5) if c is not None else None; has = has or c is not None
            if has: pairs.append(row)
    return {"tickers": tickers, "pairs": pairs}

def _held_names():
    try:
        from tools.book_direction import book_sides
        return [t for t,v in book_sides().items() if v.get("side") in ("long","short")]
    except Exception as e:
        log.warning("correlation: held-names lookup failed (%s)", e); return []

def render_report_block():
    try:
        import db_pg
        held = _held_names()
        if not held: return "BOOK RISK: no book positions"
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker_a, ticker_b, correlation FROM correlation_matrix "
                        "WHERE window_days=%s AND ticker_a=ANY(%s) AND ticker_b=ANY(%s)",
                        (CLUSTER_WINDOW, held, held))
            pairs = [{"a":a,"b":b,CLUSTER_WINDOW:float(c)} for a,b,c in cur.fetchall() if c is not None]
        if not pairs: return f"BOOK RISK: {len(held)} positions (no correlation snapshot yet)"
        clusters, n_bets = book_clusters(pairs, held)
        multi = [c for c in clusters if len(c) > 1][:4]
        cl_s = " Â· ".join("+".join(c) for c in multi) if multi else "none"
        return f"BOOK RISK ({CLUSTER_WINDOW}d): {len(held)} positions â‰ˆ {n_bets} independent bets Â· clusters: {cl_s}"
    except Exception as e:
        log.warning("book-risk render failed: %s", e); return f"BOOK RISK: unavailable ({e})"

def _persist(payload, snapshot_date):
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr); return 2
    rows = []
    for p in payload["pairs"]:
        for w in WINDOWS:
            if p.get(w) is not None: rows.append((p["a"], p["b"], w, p[w], p["n"], snapshot_date))
    if not rows: print("no correlation rows", file=sys.stderr); return 0
    # ON CONFLICT upserts make this idempotent â€” a retried persist can't dupe.
    def _do():
        from psycopg2.extras import execute_values
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            execute_values(cur,
                "INSERT INTO correlation_matrix (ticker_a,ticker_b,window_days,correlation,n_obs,as_of) VALUES %s "
                "ON CONFLICT (ticker_a,ticker_b,window_days) DO UPDATE SET correlation=EXCLUDED.correlation, "
                "n_obs=EXCLUDED.n_obs, as_of=EXCLUDED.as_of, computed_at=NOW()", rows, page_size=1000)
            conn.commit()
    try:
        db_pg.with_db_retry(_do)
        return 0
    except Exception as e:
        print(f"ERROR: persistence failed: {e}", file=sys.stderr); return 3

def run(dry_run=False):
    today = dt.datetime.now(dt.timezone.utc).date()   # UTC: session-dated row; payload = _compute()
    if not payload["pairs"]: print("ERROR: no pairs computed", file=sys.stderr); return 4
    print(f"computed {len(payload['pairs'])} pairs over {len(payload['tickers'])} names, windows {WINDOWS}")
    held = _held_names()
    if held:
        clusters, n_bets = book_clusters(payload["pairs"], held)
        multi = [c for c in clusters if len(c) > 1]
        print(f"BOOK: {len(held)} positions ~ {n_bets} bets; clusters: {' Â· '.join('+'.join(c) for c in multi[:6]) or 'none'}")
    if dry_run: print("[dry-run] no persistence."); return 0
    return _persist(payload, today)

def _cli(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="tools.correlation_matrix")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)

if __name__ == "__main__":
    raise SystemExit(_cli())
