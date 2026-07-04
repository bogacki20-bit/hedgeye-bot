"""Position Monitor sync loader — the shared gate for both bucket feeds.

The bucket write-hook (tools.bucket_history.sync_buckets) is live but has no feed.
This module is the ONLY thing that calls it, so every bucket mutation passes one
gate. Two feeds:

  FEED 1 — operator upload (Mondays, the authoritative weekly ANCHOR):
      a {ticker: bucket} mapping parsed from the operator's PM file (CSV / parsed
      PDF output). Dry-run by default; a sanity guard rejects obvious parse
      failures; a CONFIRM gate precedes any write; on confirm it syncs WITH
      detect_removals=True (the anchor is the only feed allowed to remove).

  FEED 2 — PM "Changes" email deltas (Mon–Thu, PARTIAL transitions):
      parsed from the "Weekly Position Monitor | N Active Position Monitor Changes"
      digest and the single-name "SECTOR: Position Monitor Change | TKR ..." emails.
      Applied with detect_removals=False — deltas move names, they never remove the
      whole roster. NOT wired live: run dry_run_changes() and eyeball the output
      first (see module note below).

Both feeds stamp the frozen quad via bucket_history's existing hook — no changes to
bucket_history.py. Python owns all parsing and writes. No LLM.

  CLI:
      python tools/sync_position_monitor.py <file.csv>            # Feed 1 dry-run
      python tools/sync_position_monitor.py <file.csv> --commit   # Feed 1 apply (gated)
      python tools/sync_position_monitor.py --changes-dryrun [N]  # Feed 2 preview

TAXONOMY NOTE: the PM source names only four tiers (Best Idea Long/Short, Long/Short
Bench). ticker_tags additionally carries active_long/active_short — a bot-side middle
tier the deltas never express. Feed 2 therefore only ever moves names among the four
source tiers; it can't produce an active_* bucket. Feed 1 (operator file) can carry
the full six-bucket vocab.
"""
from __future__ import annotations

import logging
import re
import sys

log = logging.getLogger(__name__)

# Canonical bucket vocabulary (ticker_tags.hedgeye_bucket_0629).
VALID_BUCKETS = {
    "top_idea_long", "active_long", "long_bench",
    "top_idea_short", "active_short", "short_bench",
}

# PM source tier phrase -> canonical bucket. Order matters: match the most specific
# phrase first ("best idea long" before "long").
TIER_MAP = [
    ("best idea long", "top_idea_long"),
    ("best idea short", "top_idea_short"),
    ("active long", "active_long"),
    ("active short", "active_short"),
    ("long bench", "long_bench"),
    ("short bench", "short_bench"),
]

# Feed-1 sanity thresholds — a parse failure looks like a huge removal set or a
# mapping far smaller than the current roster.
MAX_REMOVAL_FRAC = 0.10   # refuse if removals > 10% of currently-tagged names
MIN_COVERAGE_FRAC = 0.80  # refuse if mapping has < 80% of the current tagged count


# ─────────────────────────── shared helpers ───────────────────────────

def _tier_to_bucket(phrase: str) -> str | None:
    """Map a source tier phrase (e.g. 'Best Idea Long', 'Long Bench') to a canonical
    bucket, or None if unrecognized."""
    p = re.sub(r"\s+", " ", (phrase or "").strip().lower())
    for needle, bucket in TIER_MAP:
        if needle in p:
            return bucket
    return None


def _norm_bucket(val: str) -> str | None:
    """Accept either a canonical bucket verbatim or a source tier phrase; normalize
    to a canonical bucket, else None."""
    v = (val or "").strip()
    if v.lower() in VALID_BUCKETS:
        return v.lower()
    return _tier_to_bucket(v)


def _current_tags() -> dict:
    """{ticker: bucket} currently in ticker_tags (non-null bucket). Read-only."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, hedgeye_bucket_0629 FROM ticker_tags "
                    "WHERE hedgeye_bucket_0629 IS NOT NULL")
        return {r[0].strip().upper(): r[1] for r in cur.fetchall()}


def _diff(mapping: dict, current: dict, detect_removals: bool) -> dict:
    """Classify a proposed mapping against current tags. Read-only."""
    changes, firsts = [], []
    for tk, bk in mapping.items():
        cur_bk = current.get(tk)
        if cur_bk is None:
            firsts.append((tk, bk))
        elif cur_bk != bk:
            changes.append((tk, cur_bk, bk))
    removals = []
    if detect_removals:
        removals = sorted(set(current) - set(mapping))
    return {"changes": changes, "firsts": firsts, "removals": removals,
            "total": len(mapping)}


# ─────────────────────────── FEED 1: operator upload ───────────────────────────

def load_mapping_file(path: str) -> tuple[dict, list]:
    """Parse an operator PM file into {ticker: canonical_bucket}. Accepts CSV with a
    (ticker, bucket) pair per row; bucket may be a canonical value OR a source tier
    phrase. Header row optional. Returns (mapping, errors)."""
    import csv
    mapping, errors = {}, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) < 2:
            if cells:
                errors.append(f"row {i+1}: need ticker,bucket — got {row!r}")
            continue
        tk = cells[0].upper()
        if tk.lower() in ("ticker", "symbol"):
            continue  # header
        # Accept the full ticker_tags vocabulary: US symbols AND international /
        # crypto forms — digits, dots, hyphens (005930.KS, PBR-A, EL.PA, BTCUSD).
        # Require ≥1 letter so a stray number isn't taken as a ticker.
        if not (re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", tk) and re.search(r"[A-Z]", tk)):
            errors.append(f"row {i+1}: bad ticker {cells[0]!r}")
            continue
        bk = _norm_bucket(cells[1])
        if bk is None:
            errors.append(f"row {i+1}: unrecognized bucket/tier {cells[1]!r} for {tk}")
            continue
        mapping[tk] = bk
    return mapping, errors


def sanity_guard(mapping: dict, current: dict) -> tuple[bool, str]:
    """Reject obvious parse failures BEFORE any write, even with confirm."""
    n_cur = len(current)
    if n_cur == 0:
        return True, "no current tags — first load, guard n/a"
    removals = len(set(current) - set(mapping))
    if removals > MAX_REMOVAL_FRAC * n_cur:
        return False, (f"removals {removals} exceed {int(MAX_REMOVAL_FRAC*100)}% of "
                       f"{n_cur} tagged — looks like a parse failure, not an update")
    if len(mapping) < MIN_COVERAGE_FRAC * n_cur:
        return False, (f"mapping has {len(mapping)} names, < {int(MIN_COVERAGE_FRAC*100)}% "
                       f"of {n_cur} tagged — looks truncated")
    return True, "ok"


def run_upload(path: str, confirm: bool = False) -> str:
    """Feed 1. Dry-run unless confirm=True. On confirm, passes the sanity guard then
    sync_buckets(detect_removals=True)."""
    mapping, errors = load_mapping_file(path)
    current = _current_tags()
    out = []
    if errors:
        out.append(f"⚠️  {len(errors)} unparseable row(s):")
        out += [f"     {e}" for e in errors[:20]]
    diff = _diff(mapping, current, detect_removals=True)
    out.append(_render_preview(diff, current, f"PM upload — {path}"))

    ok, reason = sanity_guard(mapping, current)
    if not ok:
        out.append(f"⛔ SANITY GUARD: {reason}")
        out.append("   Refusing to proceed. Fix the file and re-run.")
        return "\n".join(out)

    if not confirm:
        out.append("🔎 DRY-RUN — no writes. Re-run with --commit to apply "
                   "(CONFIRM gate).")
        return "\n".join(out)

    from tools.bucket_history import sync_buckets
    src = f"pm_upload:{path.rsplit('/',1)[-1].rsplit(chr(92),1)[-1]}"
    res = sync_buckets(mapping, source_email_id=src, detect_removals=True)
    out.append(f"✅ APPLIED — {res['transitions']} transition(s) recorded "
               f"(quad frozen at write time).")
    return "\n".join(out)


# ─────────────────────────── FEED 2: Changes email deltas ───────────────────────────

# The digest email body: repeated "Moved to <TIER>: <payload>" segments.
_MOVED_RE = re.compile(r"Moved to ([^:]+?):\s*(.*?)(?=Moved to |\Z)", re.I | re.S)
# The single-name email subject: "TKR - Removing from <A>, Adding to <B>".
_SINGLE_RE = re.compile(
    r"\b([A-Z]{1,6})\b\s*[-–]\s*Removing from\s+(.+?),\s*Adding to\s+(.+?)(?:$|\|)", re.I)
# The single-name body prose (used when the subject is truncated in storage):
# "... ( TKR ) from our <A> list and moving it to our <B>."
_SINGLE_PROSE_RE = re.compile(
    r"\(\s*([A-Z]{1,6})\s*\)\s+from our\s+.+?\s+and moving it to\s+(?:our\s+)?"
    r"([A-Za-z ]+?)\s*[.\|]", re.I)


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<style.*?</style>", " ", h or "")
    h = re.sub(r"(?is)<script.*?</script>", " ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", h).strip()


def parse_changes_email(subject: str, html_body: str) -> dict:
    """Parse a PM Changes email into a PARTIAL {ticker: bucket} mapping (destination
    tier only). Handles both the weekly digest and the single-name format. Explicit
    pure-removals (no destination) are NOT included — deltas never remove."""
    text = _strip_html(html_body)
    mapping: dict[str, str] = {}

    # Single-name subject (also appears verbatim in the body header).
    for m in _SINGLE_RE.finditer((subject or "") + " | " + text[:400]):
        tk, _frm, dst = m.group(1).upper(), m.group(2), m.group(3)
        bk = _tier_to_bucket(dst)
        if bk:
            mapping[tk] = bk
    # Single-name body prose fallback (subject often truncated in storage).
    for m in _SINGLE_PROSE_RE.finditer(text[:800]):
        bk = _tier_to_bucket(m.group(2))
        if bk:
            mapping[m.group(1).upper()] = bk

    # Weekly digest: "Moved to <TIER>: <ticker> (from ...), <ticker>, ..."
    for m in _MOVED_RE.finditer(text):
        bk = _tier_to_bucket(m.group(1))
        if not bk:
            continue
        payload = m.group(2)
        # cut the digest off before prose spills in (first sentence after the list)
        payload = re.split(r"\bVIEW LARGER IMAGE\b|\bOur new\b|\bTakeaway\b", payload)[0]
        for seg in payload.split(","):
            mt = re.match(r"\s*([A-Z]{1,6})\b", seg)
            if not mt:
                continue
            tk = mt.group(1).upper()
            if tk == "NONE":
                continue
            mapping[tk] = bk
    return mapping


def _recent_changes_emails(limit: int = 10) -> list:
    """Last N PM Changes emails (digest + single-name), newest first. Excludes ETF
    Pro / Model Portfolio / Investing-Ideas 'Changes'. Read-only."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT received_at::date, subject, coalesce(html_body,''), message_id
                 FROM hedgeye_emails_raw
                WHERE subject ~* 'position monitor'
                  AND subject ~* 'change'
                  AND subject !~* 'etf pro|model portfolio'
                ORDER BY received_at DESC LIMIT %s""", (limit,))
        return cur.fetchall()


def dry_run_changes(limit: int = 10) -> str:
    """Feed 2 preview. Parse the last N PM Changes emails and print every transition
    that WOULD be recorded (vs current tags), WITHOUT writing. For operator eyeball
    before the live wire is enabled."""
    emails = _recent_changes_emails(limit)
    current = _current_tags()
    out = [f"🔎 FEED 2 dry-run — last {len(emails)} PM Changes email(s), no writes:"]
    grand = 0
    for d, subj, hb, _mid in emails:
        mapping = parse_changes_email(subj, hb)
        out.append(f"\n── {d}  {subj[:64]}")
        if not mapping:
            out.append("     (no transitions parsed)")
            continue
        for tk, bk in sorted(mapping.items()):
            cur_bk = current.get(tk)
            if cur_bk == bk:
                out.append(f"     · {tk:<7} already {bk} (no-op)")
            elif cur_bk is None:
                out.append(f"     + {tk:<7} (new) → {bk}")
                grand += 1
            else:
                out.append(f"     ~ {tk:<7} {cur_bk:<14} → {bk}")
                grand += 1
    out.append(f"\n→ {grand} transition(s) would be recorded across {len(emails)} "
               f"email(s). detect_removals=False (deltas never remove).")
    out.append("   Live auto-apply is NOT enabled — review this output first.")
    return "\n".join(out)


def apply_changes_email(subject: str, html_body: str, source_email_id=None) -> dict:
    """Feed 2 write path (NOT auto-wired). Applies a single Changes email's parsed
    transitions with detect_removals=False. Enable only after the dry-run is
    reviewed."""
    mapping = parse_changes_email(subject, html_body)
    if not mapping:
        return {"transitions": 0, "detail": []}
    from tools.bucket_history import sync_buckets
    return sync_buckets(mapping, source_email_id=source_email_id, detect_removals=False)


# ─────────────────────────── preview renderer ───────────────────────────

def _render_preview(diff: dict, current: dict, title: str) -> str:
    lines = [f"📋 {title}",
             f"   mapping: {diff['total']} names   (current tagged: {len(current)})",
             f"   changes: {len(diff['changes'])}   first-sightings: "
             f"{len(diff['firsts'])}   removals: {len(diff['removals'])}"]
    for tk, a, b in sorted(diff["changes"]):
        lines.append(f"     ~ {tk:<7} {a:<14} → {b}")
    for tk, b in sorted(diff["firsts"]):
        lines.append(f"     + {tk:<7} {'(new)':<14} → {b}")
    for tk in diff["removals"]:      # individually listed
        lines.append(f"     - {tk:<7} {current.get(tk,'?'):<14} → REMOVED")
    return "\n".join(lines)


# ─────────────────────────── CLI ───────────────────────────

def _main(argv: list) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = [a for a in argv if not a.startswith("-")]
    flags = {a for a in argv if a.startswith("-")}
    if "--changes-dryrun" in flags:
        n = int(args[0]) if args and args[0].isdigit() else 10
        print(dry_run_changes(n))
        return 0
    if not args:
        print(__doc__)
        return 2
    print(run_upload(args[0], confirm=("--commit" in flags)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
