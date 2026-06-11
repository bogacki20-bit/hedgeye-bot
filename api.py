"""
Small HTTP API for the Hedgeye Bot.

Currently exposes one ingest endpoint used by the scheduled
`spotgamma-tape-watch` task, which runs in a Cowork Linux sandbox that can't
reach the laptop's filesystem or psql directly. It POSTs the captured Tape Tool
data here, and we write it to Postgres (corpus_documents + spotgamma_tape_reports).

Runs in-process inside main.py as a daemon thread (see run_api_server). Railway
routes the service's public domain to whatever PORT we bind. There is no other
HTTP surface — keep this module tiny and boring.

Auth: shared-secret bearer token in the Authorization header, matched against
the TAPE_INGEST_SECRET env var. The secret is NEVER logged.
"""

import hmac
import logging
import os

from flask import Flask, jsonify, request

import db_pg

log = logging.getLogger(__name__)

app = Flask(__name__)


def _secret() -> str | None:
    return os.getenv("TAPE_INGEST_SECRET")


def _authorized(req) -> bool:
    """Constant-time bearer-token check. Returns False if the secret env var
    is unset (fail closed) or the header doesn't match."""
    expected = _secret()
    if not expected:
        log.error("TAPE_INGEST_SECRET not set — rejecting ingest request.")
        return False
    header = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    provided = header[len(prefix):].strip()
    return hmac.compare_digest(provided, expected)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "hedgeye-bot-api"}), 200


@app.post("/api/tape_report")
def tape_report():
    if not _authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    if not body.get("captured_at"):
        return jsonify({"error": "captured_at is required"}), 400

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
    if not _secret():
        log.warning(
            "API server starting WITHOUT TAPE_INGEST_SECRET set — "
            "/api/tape_report will reject all requests until it's configured."
        )
    log.info("API server listening on 0.0.0.0:%d", port)
    # threaded=True so a slow DB write doesn't block /health; no reloader in-thread.
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_api_server()
