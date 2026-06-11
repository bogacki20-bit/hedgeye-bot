"""
Small HTTP API for the Hedgeye Bot.

Exposes a generalized scrape-ingest endpoint used by scheduled SKILLs that run
in a Cowork Linux sandbox and can't reach the laptop's filesystem or psql
directly. They POST captured data here and we write it to Postgres
(corpus_documents always; source-specific tables when a router exists).

  POST /api/scrape_ingest   generalized {source, captured_at, title, metadata,
                            raw_text, screenshot_path} — primary endpoint.
  POST /api/tape_report     backward-compat alias for the spotgamma-tape-watch
                            SKILL's flat body shape (source is implied tape).
  GET  /health

Runs in-process inside main.py as a daemon thread (see run_api_server). Railway
routes the service's public domain to whatever PORT we bind.

Auth: shared-secret bearer token, matched against SCRAPE_INGEST_SECRET (with a
backward-compat fallback to the older TAPE_INGEST_SECRET). NEVER logged.

Market-hours gate (2026-06-10): scrape sources flagged as session-only
(spotgamma_tape_15m and the legacy spotgamma_tape) are rejected outside
9:30-16:15 ET weekdays. Prevents the tape watcher from scraping overnight
when SG is just showing yesterday's close and the data has no signal.
"""

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import hmac
import logging
import os

from flask import Flask, jsonify, request

import db_pg

log = logging.getLogger(__name__)

app = Flask(__name__)

# Sources whose data is only meaningful during the regular US session +
# a 15-min close-window buffer. Out-of-window POSTs are rejected with 409
# so the SKILL can log/skip without retrying.
_SESSION_GATED_SOURCES = frozenset({"spotgamma_tape_15m", "spotgamma_tape"})

_ET = ZoneInfo("America/New_York")
_GATE_OPEN  = dtime(9, 30)
_GATE_CLOSE = dtime(16, 15)


def _within_market_window(now: datetime | None = None) -> bool:
    """True 9:30-16:15 ET, Mon-Fri. The 15-min post-close buffer covers
    the final scheduled capture of the session."""
    n = now or datetime.now(_ET)
    if n.weekday() >= 5:
        return False
    return _GATE_OPEN <= n.time() <= _GATE_CLOSE


def _secrets() -> list[str]:
    # SCRAPE_INGEST_SECRET is canonical; TAPE_INGEST_SECRET is also accepted so
    # the already-deployed tape SKILL keeps working during/after the rename.
    # A request authorizes if it matches EITHER configured secret.
    return [v for v in (os.getenv("SCRAPE_INGEST_SECRET"),
                        os.getenv("TAPE_INGEST_SECRET")) if v]


def _authorized(req) -> bool:
    """Constant-time bearer-token check against any configured secret. Returns
    False if no secret env var is set (fail closed) or the header matches none."""
    expected = _secrets()
    if not expected:
        log.error("No SCRAPE_INGEST_SECRET/TAPE_INGEST_SECRET set — rejecting ingest request.")
        return False
    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    provided = header[len(prefix):].strip()
    # Check all secrets without short-circuiting, to keep timing uniform.
    ok = False
    for s in expected:
        if hmac.compare_digest(provided, s):
            ok = True
    return ok


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "hedgeye-bot-api"}), 200


@app.post("/api/scrape_ingest")
def scrape_ingest():
    if not _authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    source = (body.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source is required"}), 400
    if not body.get("captured_at"):
        return jsonify({"error": "captured_at is required"}), 400

    # Session-only gate (2026-06-10). Session-gated sources outside the
    # 9:30-16:15 ET weekday window are rejected with 409 so the SKILL
    # can log/skip without retrying. Pre-fix the tape watcher could fire
    # overnight on a snapshot of yesterday's close with no signal.
    if source in _SESSION_GATED_SOURCES and not _within_market_window():
        log.info("scrape_ingest: rejecting %s — outside market hours", source)
        return jsonify({
            "error": "outside market hours",
            "source": source,
            "window_et": "9:30-16:15 Mon-Fri",
        }), 409

    try:
        result = db_pg.save_scrape_ingest(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("scrape_ingest failed")
        return jsonify({"error": f"db write failed: {type(e).__name__}: {e}"}), 500

    log.info(
        "scrape_ingest: source=%s captured_at=%s corpus_inserted=%s routed=%s",
        result.get("source"), result.get("captured_at"),
        result.get("corpus_inserted"),
        {k: v for k, v in result.items()
         if k not in ("source", "captured_at", "source_date", "corpus_inserted")},
    )
    return jsonify({"ok": True, **result}), 200


@app.post("/api/tape_report")
def tape_report():
    """Backward-compat for the spotgamma-tape-watch SKILL (flat body)."""
    if not _authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    if not body.get("captured_at"):
        return jsonify({"error": "captured_at is required"}), 400

    # Same market-hours gate as /api/scrape_ingest — spotgamma_tape is
    # in _SESSION_GATED_SOURCES so overnight POSTs get the same 409.
    if not _within_market_window():
        log.info("tape_report: rejecting — outside market hours")
        return jsonify({
            "error": "outside market hours",
            "source": "spotgamma_tape",
            "window_et": "9:30-16:15 Mon-Fri",
        }), 409

    try:
        result = db_pg.save_tape_report(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.exception("tape_report ingest failed")
        return jsonify({"error": f"db write failed: {type(e).__name__}: {e}"}), 500

    log.info(
        "tape_report ingested: captured_at=%s corpus_inserted=%s tape_inserted=%s",
        result.get("captured_at"), result.get("corpus_inserted"),
        result.get("tape_inserted"),
    )
    return jsonify({"ok": True, **result}), 200


def run_api_server():
    """Blocking — run the Flask app. Intended to be launched in a daemon thread
    from main.py. Binds to the Railway-provided PORT (default 8080)."""
    port = int(os.getenv("PORT", "8080"))
    if not _secrets():
        log.warning(
            "API server starting WITHOUT SCRAPE_INGEST_SECRET set — "
            "ingest endpoints will reject all requests until it's configured."
        )
    log.info("API server listening on 0.0.0.0:%d", port)
    # threaded=True so a slow DB write doesn't block /health; no reloader in-thread.
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_api_server()
