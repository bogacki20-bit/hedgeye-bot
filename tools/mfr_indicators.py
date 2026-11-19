"""tools/mfr_indicators.py - pull MyFractalRange platform indicators into mfr_indicators.

Reads the same numbers the MFR chart draws, through MFR's MCP endpoint (mfr_get_indicator),
for every active ticker in the MFR account. Needs MFR_MCP_TOKEN (an MCP-scoped token; the old
MFR_API_TOKEN is REST-only and is refused at /mcp). Tokens expire 90 days after creation.

Groups (5 indicators per request = 1 rate-limit request per ticker per group):
  core  : VOLA_TT, MFR_TREND, MFR_TARGETS_M, MFR_TARGETS_Q, MFR_TARGETS_A   (the card's lines)
  track : BS_SIGNALS, BTFD, MOMENTUM, VOLA, MATRIX                           (scored before use)

Rate limit: 150 requests / 15 min per token. We pace at one request per REQ_SPACING seconds
(default 6.2) and back off on a rate-limit error. A full pass of ~1,030 tickers = ~1.8 h per group.
Getting indicators never activates a ticker or spends ticker quota.

Usage:
  py -m tools.mfr_indicators --group core --bars 5             # nightly
  py -m tools.mfr_indicators --group core --bars 250           # backfill, 1 year (max; same cost)
  py -m tools.mfr_indicators --tickers XLE,SPY --group all --bars 3 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("mfr_indicators")

MCP_URL = "https://myfractalrange.com/mcp"
UA = "Mozilla/5.0 (HedgeyeBot/1.0)"
MAX_BARS = 250  # MFR caps mfr_get_indicator at 250 bars (about 1 year of dailies)
REQ_SPACING = float(os.environ.get("MFR_MCP_SPACING_SEC", "6.2"))

GROUPS = {
    "core": ["VOLA_TT", "MFR_TREND", "MFR_TARGETS_M", "MFR_TARGETS_Q", "MFR_TARGETS_A"],
    "track": ["BS_SIGNALS", "BTFD", "MOMENTUM", "VOLA", "MATRIX"],
}

# indicator id -> {output key: column}
COLS = {
    "VOLA_TT": {"trade": "vola_trade", "trend": "vola_trend", "bear": "vola_bear"},
    "MFR_TREND": {"bull": "trend_bull", "bear": "trend_bear"},
    "MFR_TARGETS_M": {"openM": "tm_open", "up1M": "tm_up1", "up2M": "tm_up2", "dn1M": "tm_dn1", "dn2M": "tm_dn2"},
    "MFR_TARGETS_Q": {"openQ": "tq_open", "up1Q": "tq_up1", "up2Q": "tq_up2", "dn1Q": "tq_dn1", "dn2Q": "tq_dn2"},
    "MFR_TARGETS_A": {"openA": "ty_open", "up1A": "ty_up1", "up2A": "ty_up2", "dn1A": "ty_dn1", "dn2A": "ty_dn2"},
    "BS_SIGNALS": {"buy": "bs_buy", "megaBuy": "bs_mega_buy", "sell": "bs_sell", "megaSell": "bs_mega_sell"},
    "BTFD": {"buy": "btfd_buy"},
    "MOMENTUM": {"p1": "mom_p1", "p2": "mom_p2", "p3": "mom_p3"},
    "VOLA": {"vixfix": "vixfix", "trade": "vix_trade", "trend": "vix_trend", "trendDown": "vix_trend_down",
             "hi": "vix_hi", "lo": "vix_lo"},
    "MATRIX": {"wvf": "mx_wvf", "wvfHot": "mx_wvf_hot", "open": "mx_open", "close": "mx_close", "bull": "mx_bull",
               "res": "mx_res", "sup": "mx_sup", "upX": "mx_up_x", "downX": "mx_down_x"},
}
FLAG_COLS = {"vola_bear", "bs_buy", "bs_mega_buy", "bs_sell", "bs_mega_sell", "btfd_buy", "vix_trend_down",
             "mx_wvf_hot", "mx_bull"}


class RateLimited(Exception):
    def __init__(self, wait):
        super().__init__(f"rate limited, retry in {wait}s")
        self.wait = wait


class ServerError(ConnectionError):
    """MFR answered with something that isn't JSON (an HTTP 500 page, an empty body). 11/19 06:43 UTC:
    json.loads on a 500 page raised JSONDecodeError, which the retry loop didn't catch, so one bad
    reply killed the whole night. A ConnectionError subclass, so run() retries it like a network error."""


# Progress of the current run(), so the nightly loop can resume a crashed run instead of starting over.
PROGRESS: dict = {}


class McpClient:
    def __init__(self, token: str):
        self.token = token.strip()
        self.sid = None
        self.n = 0
        self.last = 0.0

    def _post(self, method, params):
        self.n += 1
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", "User-Agent": UA}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}).encode()
        req = urllib.request.Request(MCP_URL, data=body, headers=h)
        try:
            r = urllib.request.urlopen(req, timeout=180)
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
            text = r.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            text = e.read().decode(errors="replace")
        else:
            status = 200
        found = re.findall(r"^data: (.*)$", text, re.M)
        try:
            return json.loads(found[-1] if found else text)
        except ValueError:
            raise ServerError(f"MFR non-JSON reply (http {status}): {text[:80]!r}") from None

    def init(self):
        j = self._post("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "hedgeye-bot", "version": "1.0"}})
        if "error" in j:
            raise RuntimeError(f"MFR MCP init failed: {j['error'].get('message')}")
        return j

    def tool(self, name, args):
        wait = self.last + REQ_SPACING - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        j = self._post("tools/call", {"name": name, "arguments": args})
        err = j.get("error")
        if err:
            msg = str(err.get("message", ""))
            if err.get("code") == -32029 or "Retry in" in msg:
                ra = (err.get("data") or {}).get("retry_after_seconds")
                m = re.search(r"Retry in (\d+)", msg)
                raise RateLimited(int(ra or (m.group(1) if m else 60)))
            if "expired" in msg.lower() or err.get("code") == -32001:
                raise RuntimeError(f"MFR MCP auth: {msg}")
            return {"_error": err}
        res = j.get("result", {})
        if res.get("structuredContent"):
            return res["structuredContent"]
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except Exception:
                    return {"_text": c["text"]}
        return res


def _num(v, col):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f)) if col in FLAG_COLS else f


def parse(resp: dict) -> tuple[dict, list]:
    """-> ({bar_date: {col: value}}, [notes for indicators not returned])."""
    rows, notes = {}, []
    for ind in resp.get("indicators", []) or []:
        iid = ind.get("id")
        cmap = COLS.get(iid)
        if not cmap:
            continue
        if ind.get("entitled") is False or ind.get("reason"):
            notes.append(f"{iid}:{ind.get('reason') or 'notEntitled'}")
            continue
        series = ind.get("series") or {}
        times = series.get("time") or []
        if times:
            for i, t in enumerate(times):
                rec = rows.setdefault(t[:10], {})
                for key, col in cmap.items():
                    arr = series.get(key)
                    if arr is not None and i < len(arr):
                        rec[col] = _num(arr[i], col)
        else:
            t = (ind.get("lastBar") or ind.get("asOf") or "")[:10]
            if t:
                rec = rows.setdefault(t, {})
                for key, col in cmap.items():
                    rec[col] = _num((ind.get("values") or {}).get(key), col)
    return rows, notes


def upsert(conn, ticker: str, rows: dict, group: str) -> int:
    if not rows:
        return 0
    stamp_col = "core_fetched_at" if group == "core" else "track_fetched_at"
    n = 0
    with conn.cursor() as cur:
        for bar_date, rec in rows.items():
            cols = [c for c in rec]
            if not cols:
                continue
            names = ", ".join(["ticker", "bar_date"] + cols + [stamp_col])
            ph = ", ".join(["%s"] * (len(cols) + 3))
            upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols + [stamp_col])
            cur.execute(f"INSERT INTO mfr_indicators ({names}) VALUES ({ph}) "
                        f"ON CONFLICT (ticker, bar_date) DO UPDATE SET {upd}",
                        [ticker, bar_date] + [rec[c] for c in cols] + [datetime.now(timezone.utc)])
            n += 1
    conn.commit()
    return n


def active_tickers(tries: int = 6, wait_s: int = 300) -> list[str]:
    """MFR watchlist, retried over ~25 min. 11/17 06:06 UTC: one watchlist timeout killed the
    whole nightly pull (it waits a day to retry). If MFR still won't list, fall back to the
    tickers stored on the newest bar, so the night still runs."""
    import time
    import mfr_client
    for i in range(tries):
        t = mfr_client.list_watchlist()
        if t:
            return t
        if i < tries - 1:
            log.warning("MFR watchlist empty (try %d/%d); retrying in %ds", i + 1, tries, wait_s)
            time.sleep(wait_s)
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM mfr_indicators "
                        "WHERE bar_date = (SELECT max(bar_date) FROM mfr_indicators) ORDER BY 1")
            t = [r[0] for r in cur.fetchall()]
        log.warning("MFR watchlist unreachable; using %d tickers from the last stored bar", len(t))
        return t
    except Exception as e:
        log.error("MFR watchlist fallback failed: %s", e)
        return []


def run(tickers=None, groups=("core",), bars=5, dry_run=False, limit=None, start_after=None,
        deadline: float | None = None) -> dict:
    """deadline: epoch seconds; past it, stop and raise (the nightly loop must be done by 5 AM ET,
    when the capture desk takes over re-runs)."""
    token = os.environ.get("MFR_MCP_TOKEN", "")
    if not token.strip():
        raise RuntimeError("MFR_MCP_TOKEN not set")
    if not tickers:
        tickers = active_tickers()
        if not tickers:
            raise RuntimeError("MFR watchlist read returned 0 tickers; not running")
    tickers = sorted(set(tickers))           # sorted, so start_after resumes cleanly
    if start_after:
        tickers = [t for t in tickers if t > start_after]
    if limit:
        tickers = tickers[:limit]
    bars = max(1, min(int(bars), MAX_BARS))
    cli = McpClient(token)
    cli.init()
    conn = None
    if not dry_run:
        import db_pg
        import psycopg2
        conn = psycopg2.connect(db_pg._resolve_dsn())
    stats = {"tickers": len(tickers), "rows": 0, "ok": 0, "empty": [], "errors": [], "notes": {}}
    t0 = time.time()
    PROGRESS.clear()
    PROGRESS.update({"groups": tuple(groups), "last": None, "done": 0, "total": len(tickers)})
    outage = 0          # consecutive tickers that gave up on every attempt
    trips = 0
    for i, tk in enumerate(tickers, 1):
        if deadline and time.time() > deadline:
            raise RuntimeError(f"stopped at the deadline after {i - 1}/{len(tickers)} tickers (last {PROGRESS['last']})")
        gave_up_all = True
        for g in groups:
            args = {"ticker": tk, "indicators": GROUPS[g], "timeframe": "D", "bars": bars}
            for attempt in range(4):
                try:
                    resp = cli.tool("mfr_get_indicator", args)
                    break
                except RateLimited as e:
                    log.warning("rate limited; sleeping %ss", e.wait)
                    time.sleep(e.wait + 2)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                    log.warning("%s %s: network %s (attempt %d)", tk, g, e, attempt + 1)
                    time.sleep(15 * 2 ** attempt)          # 15, 30, 60, 120 s
            else:
                stats["errors"].append(f"{tk}:{g}:gave_up")
                continue
            gave_up_all = False
            err = resp.get("_error") or (resp.get("error") if isinstance(resp.get("error"), dict) else None)
            if err:
                stats["errors"].append(f"{tk}:{g}:{str(err.get('message'))[:100]}")
                continue
            rows, notes = parse(resp)
            if notes:
                stats["notes"][f"{tk}:{g}"] = notes
            if not rows:
                stats["empty"].append(f"{tk}:{g}")
                continue
            if dry_run:
                last = max(rows)
                print(tk, g, last, json.dumps(rows[last]))
            else:
                stats["rows"] += upsert(conn, tk, rows, g)
            stats["ok"] += 1
        if gave_up_all:
            outage += 1
            if outage >= 5:
                # MFR looks down, not just one bad ticker. Pause 10 min and reconnect; after ~1 h of
                # outage raise, so the nightly loop resumes from here later instead of burning the list.
                trips += 1
                if trips > 2:
                    raise RuntimeError(f"MFR server down ~1 h; stopped after {i}/{len(tickers)} (last {PROGRESS['last']})")
                log.warning("MFR outage: %d tickers in a row failed; pausing 10 min (trip %d/2)", outage, trips)
                time.sleep(600)
                cli = McpClient(token)
                try:
                    cli.init()
                except Exception as e:
                    log.warning("MFR re-init failed: %s", e)
                outage = 0
            continue                                   # not done: don't advance the resume point
        outage = 0
        PROGRESS.update({"last": tk, "done": i})
        if i % 50 == 0:
            log.info("%d/%d tickers, %d rows, %.0f min", i, len(tickers), stats["rows"], (time.time() - t0) / 60)
            print(f"progress {i}/{len(tickers)} last={tk} rows={stats['rows']}", flush=True)
    if conn:
        conn.close()
    stats["minutes"] = round((time.time() - t0) / 60, 1)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma list; default = every active MFR ticker")
    ap.add_argument("--group", default="core", choices=["core", "track", "all"])
    ap.add_argument("--bars", type=int, default=5)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--start-after", help="resume: skip tickers up to and including this one (sorted)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    groups = ("core", "track") if a.group == "all" else (a.group,)
    tickers = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None
    stats = run(tickers, groups, a.bars, a.dry_run, a.limit, a.start_after)
    short = {k: (v if not isinstance(v, (list, dict)) else (len(v), (v[:15] if isinstance(v, list) else list(v.items())[:10])))
             for k, v in stats.items()}
    print(json.dumps(short, default=str, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
