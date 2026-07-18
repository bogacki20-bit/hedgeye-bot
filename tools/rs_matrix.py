"""Pairwise relative-strength matrix - 11 sectors + QQQ + IWM vs each other.
Cell = 20d momentum of the base/vs ratio; delta = its change over 3 sessions."""
from __future__ import annotations
import sys, logging, datetime as dt
from tools.relative_strength import (fetch_close_series, align_on_date,
    rs_ratio_series, roc, SECTOR_ETFS)
log = logging.getLogger(__name__)
UNIVERSE = SECTOR_ETFS + ["QQQ", "IWM"]
TREND_W = 20
DELTA_W = 3

def _cell(pa, pb):
    _d, a, b = align_on_date(pa, pb)
    r = rs_ratio_series(a, b)
    if len(r) <= TREND_W + DELTA_W:
        return None, None
    rs_now = roc(r, TREND_W)
    rs_prev = roc(r[:-DELTA_W], TREND_W)
    delta = (rs_now - rs_prev) if (rs_now is not None and rs_prev is not None) else None
    return rs_now, delta

def _compute():
    lookback = TREND_W + DELTA_W + 30
    px = {t: fetch_close_series(t, lookback) for t in UNIVERSE}
    cells = []
    for base in UNIVERSE:
        for vs in UNIVERSE:
            if base == vs or not px.get(base) or not px.get(vs):
                continue
            rs, dl = _cell(px[base], px[vs])
            if rs is None:
                continue
            cells.append({"base": base, "vs": vs, "rs_trend": round(rs, 6),
                          "rs_delta_3d": round(dl, 6) if dl is not None else None,
                          "n_obs": TREND_W})
    return cells

def render_report_block(top=4):
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT base, vs, rs_trend, rs_delta_3d FROM rs_pairwise "
                        "WHERE as_of=(SELECT max(as_of) FROM rs_pairwise)")
            rows = cur.fetchall()
        if not rows:
            return "RS PAIRS: no snapshot (run tools.rs_matrix)"
        lead = sorted(rows, key=lambda r: r[2], reverse=True)[:top]
        accel = sorted([r for r in rows if r[3] is not None], key=lambda r: r[3], reverse=True)[:top]
        f = lambda r: f"{r[0]}>{r[1]} {r[2]*100:+.1f}%"
        g = lambda r: f"{r[0]}>{r[1]} {r[3]*100:+.1f}"
        return ("RS PAIRS (20d, base>vs): leaders " + " · ".join(f(r) for r in lead)
                + " | accelerating " + " · ".join(g(r) for r in accel))
    except Exception as e:
        log.warning("rs_matrix render failed: %s", e); return f"RS PAIRS: unavailable ({e})"

def run(dry_run=False):
    cells = _compute()
    if not cells:
        print("ERROR: no pairs computed", file=sys.stderr); return 4
    print(f"computed {len(cells)} pairs")
    for c in sorted(cells, key=lambda x: x["rs_trend"], reverse=True)[:10]:
        print(f"  {c['base']}>{c['vs']} {c['rs_trend']*100:+.1f}% d3={c['rs_delta_3d']}")
    if dry_run:
        print("[dry-run] no persist"); return 0
    import db_pg
    today = dt.date.today()
    # ON CONFLICT upserts make this idempotent — a retried persist can't dupe.
    def _do():
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for c in cells:
                cur.execute("INSERT INTO rs_pairwise (as_of, base, vs, rs_trend, rs_delta_3d, n_obs) "
                            "VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (as_of, base, vs) DO UPDATE SET "
                            "rs_trend=EXCLUDED.rs_trend, rs_delta_3d=EXCLUDED.rs_delta_3d, "
                            "n_obs=EXCLUDED.n_obs, computed_at=NOW()",
                            (today, c["base"], c["vs"], c["rs_trend"], c["rs_delta_3d"], c["n_obs"]))
            conn.commit()
    try:
        db_pg.with_db_retry(_do)
        return 0
    except Exception as e:
        print(f"ERROR: persistence failed: {e}", file=sys.stderr); return 3

def _cli(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="tools.rs_matrix")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)

if __name__ == "__main__":
    raise SystemExit(_cli())
