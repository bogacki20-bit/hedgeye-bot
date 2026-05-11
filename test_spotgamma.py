"""End-to-end test: show SpotGamma data freshness, run latest() for a few key
tickers, and check whether the Chrome MCP refresh queue has pending work."""
import json
import sys
from datetime import datetime, timezone

out = {}

try:
    import spotgamma_client
    out["latest_per_ticker"] = {}
    for ticker in ("OIH", "SPY", "QQQ", "XOP", "IWM"):
        row = spotgamma_client.latest(ticker)
        if row:
            out["latest_per_ticker"][ticker] = {
                "captured_date":  str(row.get("captured_date")),
                "call_wall":      str(row.get("call_wall")),
                "put_wall":       str(row.get("put_wall")),
                "gamma_regime":   row.get("gamma_regime"),
                "iv_rank":        str(row.get("iv_rank")),
                "skew_rank":      str(row.get("skew_rank")),
                "source":         row.get("source"),
            }
        else:
            out["latest_per_ticker"][ticker] = None
except Exception as e:
    out["latest_err"] = str(e)

try:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MAX(captured_date) FROM spotgamma_snapshots"
            )
            r = cur.fetchone()
            out["spotgamma_snapshots_count"] = int(r[0])
            out["spotgamma_snapshots_latest"] = str(r[1]) if r[1] else None
            cur.execute(
                "SELECT ticker, captured_date FROM spotgamma_snapshots "
                "ORDER BY captured_date DESC, ticker LIMIT 12"
            )
            out["recent_sg_rows"] = [
                {"ticker": r[0], "captured_date": str(r[1])} for r in cur.fetchall()
            ]
except Exception as e:
    out["db_err"] = str(e)

# Inspect the refresh queue Python writes for the Chrome MCP SKILL to drain
try:
    from pathlib import Path
    queue_path = Path("C:/Projects/hedgeye-bot/.commands/spotgamma_refresh_queue.json")
    if queue_path.exists():
        out["refresh_queue"] = json.loads(queue_path.read_text(encoding="utf-8"))
        out["refresh_queue_mtime"] = datetime.fromtimestamp(
            queue_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    else:
        out["refresh_queue"] = "no queue file"
except Exception as e:
    out["queue_err"] = str(e)

# How many SpotGamma .md files exist on disk (these get populated into the typed table)
try:
    import os
    sg_md = "C:/Projects/hedgeye-bot/data/snapshots/spotgamma"
    counts_by_subdir = {}
    for sub in os.listdir(sg_md):
        full = os.path.join(sg_md, sub)
        if os.path.isdir(full):
            counts_by_subdir[sub] = sum(
                1 for f in os.listdir(full) if f.endswith(".md")
            )
    out["sg_md_files_by_subdir"] = counts_by_subdir
except Exception as e:
    out["sg_md_err"] = str(e)

print(json.dumps(out, indent=2, default=str), flush=True)
sys.stdout.flush()
