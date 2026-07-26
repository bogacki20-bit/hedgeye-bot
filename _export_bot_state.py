# -*- coding: utf-8 -*-
"""Full read-only bot-state export -> portable single markdown file for a
second-opinion AI. No DB/code mutations. Secrets redacted."""
import os, io, re, sys, glob, ast, subprocess, traceback
from datetime import date, datetime, timezone, timedelta

# Path-dynamic: run from wherever the repo actually lives (no hardcoded path).
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(_HERE, ".env"))
import psycopg2, psycopg2.extras

TODAY = date.today()
OUT = os.path.join(_HERE, f"bot_state_export_{TODAY}.md")
W = io.StringIO()
def w(s=""): W.write(s + "\n")

# ───────────────────────── secret redaction ─────────────────────────
SECRET_PATTERNS = [
    (re.compile(r"postgres(?:ql)?://[^\s'\"]+", re.I), "postgresql://<REDACTED-DSN>"),
    (re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"), "<REDACTED-API-KEY>"),
    (re.compile(r"(\d{6,12}:[A-Za-z0-9_\-]{30,})"), "<REDACTED-TELEGRAM-TOKEN>"),
    # KEY/TOKEN/PASSWORD/SECRET = "literal"
    (re.compile(r"""((?:API_KEY|TOKEN|PASSWORD|SECRET|APP_PASSWORD)\s*[:=]\s*)(['"])[^'"]+\2""", re.I),
     r"\1\2<REDACTED>\2"),
]
def redact(text):
    if not text: return text
    for pat, repl in SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception as e:
        return f"(error: {e})"

conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL")
                        or os.environ["DATABASE_URL"]); conn.autocommit = True
def q(sql, args=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, args or ()); return cur.fetchall()
    except Exception as e:
        conn.rollback(); return [{"_error": str(e)}]
    finally:
        cur.close()

def table(rows, cols=None, maxcell=200):
    if not rows:
        w("_(no rows)_"); return
    if "_error" in rows[0]:
        w("> query error: `%s`" % rows[0]["_error"]); return
    cols = cols or list(rows[0].keys())
    w("| " + " | ".join(cols) + " |")
    w("| " + " | ".join("---" for _ in cols) + " |")
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            v = "" if v is None else str(v)
            v = v.replace("\n", " ").replace("\r", " ").replace("|", "\\|")
            if len(v) > maxcell: v = v[:maxcell-3] + "..."
            cells.append(v)
        w("| " + " | ".join(cells) + " |")

def dump_file(path, lang="python"):
    rel = os.path.relpath(path, r"C:\Projects\hedgeye-bot").replace("\\", "/")
    try:
        content = open(path, encoding="utf-8", errors="replace").read()
        nlines = content.count("\n") + 1
        w(f"#### `{rel}` — {nlines} lines")
        w(f"```{lang}")
        W.write(redact(content))
        if not content.endswith("\n"): w("")
        w("```")
    except Exception as e:
        w(f"#### `{rel}` — could not read: {e}")
    w()

# ════════════════════════════════════════════════════════════════════
#  TITLE
# ════════════════════════════════════════════════════════════════════
w("# Hedgeye Bot — Full State Export (portable, for second-opinion review)")
w()
w(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
w("**As-of date:** 2026-06-10  ")
w("**Purpose:** Self-contained snapshot intended to be pasted into a *different* AI chat "
  "for an independent architecture/logic review. No external file access is required — "
  "everything referenced is inlined below.")
w()
w("> This document is read-only evidence. Secrets (API keys, passwords, DB credentials, "
  "account numbers) have been redacted. Code blocks are verbatim except for redacted literals.")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 1 — PREAMBLE
# ════════════════════════════════════════════════════════════════════
w("## SECTION 1 — Preamble (orientation for the reviewer)")
w()
w("### What this is")
w(
"This is a **personal trading-signal aggregation and alerting bot** for a single operator. "
"It is **not** an auto-trader — it never sends orders to a broker. It watches a set of "
"professional research products from **Hedgeye Risk Management** plus options-positioning data, "
"resolves a daily *polling universe* of tickers, monitors live prices against published "
"**risk-range bands**, and fires **Telegram alerts** to the operator's phone when a ticker "
"breaches a meaningful price zone (range edge / breakout / breakdown). The operator then "
"decides whether to act, manually, in his brokerage accounts."
)
w()
w("### What it does (pipeline in one breath)")
w(
"Every ~15 minutes a local Python **scanner** wakes up, asks a *polling-universe resolver* for "
"the union of tickers Hedgeye currently cares about (Quad-categorized longs/shorts + ETF Pro + "
"Signal Strength + Portfolio Solutions + Investing Ideas + Risk Ranges + Keith's weekly Signals + "
"operator overrides), pulls the latest **MFR** (options/vol) snapshot and **Hedgeye risk range** "
"for each, computes which price *zone* the ticker sits in, runs a **trend+momentum gate** to "
"decide a side-aware action verb (BUY/SELL/SHORT/COVER/WATCH), and — if the zone is an actionable "
"edge and the alert isn't a recent duplicate — composes a notification (via a small Anthropic "
"**Haiku 4.5** prompt) and pushes it to Telegram. Every fired alert is persisted; the operator's "
"actual trades are later ingested from brokerage CSVs and **reconciled** against alerts to build "
"an outcomes/PnL training corpus."
)
w()
w("### Architecture in one paragraph")
w(
"A **Python scanner runs locally** on the operator's Windows workstation on a 15-minute scheduled "
"task; it talks to a **Postgres database hosted on Railway** (the single source of truth for all "
"parsed Hedgeye data, alerts, trades, and snapshots). A separate **email parser** (also deployed "
"on Railway) ingests Hedgeye emails from an iCloud inbox, classifies them, and writes product-"
"specific tables. Notifier prompts use **Anthropic Haiku 4.5**. Options-positioning data (SpotGamma / "
"MFR) and qualitative commentary (Hedgeye *Macro Show*) are captured by separate scrape **skills**. "
"The bot does not execute trades; it only recommends."
)
w()
w("### The operator's framework (north star)")
w(
"The operator trades the **Hedgeye Quad framework** — a macro regime model that classifies the "
"economy into four quadrants (Quad 1 = growth accelerating / inflation slowing, Quad 2 = both "
"accelerating, Quad 3 = growth slowing / inflation accelerating, Quad 4 = both slowing). Asset "
"selection and long/short bias flow from the prevailing **monthly** and **quarterly** Quad. The "
"**north-star principle** baked into this bot: **Hedgeye's published direction is authoritative.** "
"The bot must never invent a thesis that contradicts Hedgeye's stance. When Hedgeye says a name is "
"a SHORT (bearish TREND), the bot's job is to time entries/exits *within that bias* using price "
"zones and options positioning — not to fade Hedgeye. This principle is central to the bug in "
"Section 3.1."
)
w()
w("### What the second opinion should focus on")
w("1. **The AMZN / bias-field bug (Section 3.1)** — the bot was reading only the numeric range "
"bounds and ignoring Hedgeye's directional **bias/TREND field**, so it could emit long-biased "
"actions on names Hedgeye holds bearish. Is the fix direction correct? What's the blast radius?")
w("2. **Polling-universe correctness** — is the union/staleness logic sound? (Section 4 resolver, "
"Section 7 data).")
w("3. **The trend+momentum gate** — does the zone→action mapping respect the north-star principle "
"in every branch? (Section 4, `decision_engine`).")
w("4. **Signal-to-noise** — alert volume vs. operator follow-through (Section 5 alert data, "
"Section 6 reconciliation).")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 2 — ARCHITECTURE OVERVIEW
# ════════════════════════════════════════════════════════════════════
w("## SECTION 2 — Architecture overview")
w()
w("### Data-flow (ASCII)")
w("```")
w(r"""
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                          HEDGEYE (external)                              │
 │   Risk Ranges · Signal Strength · Portfolio Solutions · Investing Ideas  │
 │   ETF Pro · Keith's Signals · Macro Show · The Call · II Newsletter ...   │
 └───────────────┬─────────────────────────────────────────────────────────┘
                 │ emails (iCloud IMAP)                ┌── MFR API (options/vol ranges)
                 ▼                                     │
   ┌──────────────────────────┐                       │   ┌── SpotGamma (scrape skill)
   │  EMAIL PARSER (Railway)   │                      │   │
   │  classify + parser_*.py   │                      │   │   ┌── Macro Show (scrape skill)
   │  -> product tables        │                      │   │   │
   └────────────┬─────────────┘                       │   │   │
                │ writes                               │   │   │
                ▼                                      ▼   ▼   ▼
        ┌───────────────────────────────────────────────────────────────┐
        │              POSTGRES  (Railway — single source of truth)       │
        │  hedgeye_* tables · mfr_snapshots · spotgamma_snapshots ·        │
        │  alerts_fired · actions_log · outcomes_log · monitored_tickers   │
        └───────┬───────────────────────────────────────────────┬────────┘
                │ reads                                          ▲ writes alerts
                ▼                                                │
   ┌───────────────────────────────┐                            │
   │  SCANNER (LOCAL, every 15 min) │                            │
   │  proactive_scanner.py          │                            │
   │    polling_universe()  ◄── active_slice (union resolver)    │
   │    price_monitor (live px)                                  │
   │    decision_engine (zone + trend/momentum gate)            │
   │      └─ notifier prompt (Anthropic Haiku 4.5) ─────────────┘
   │    -> Telegram alert to operator's phone
   └───────────────────────────────┘
                │
                ▼  operator decides + trades manually (Fidelity / Kinesis)
        brokerage CSV export ──► actions_log ──► reconcile ──► outcomes_log (PnL corpus)
""".strip("\n"))
w("```")
w()
w("### Components / what runs where")
w("| component | where | cadence | role |")
w("| --- | --- | --- | --- |")
for row in [
 ("Email parser", "Railway", "poll ~15 min", "ingest+classify Hedgeye emails into product tables"),
 ("Postgres", "Railway", "always-on", "single source of truth for all data + alerts"),
 ("Proactive scanner", "Local workstation", "Scheduled Task, 15 min", "resolve universe, monitor prices, fire alerts"),
 ("Price monitor", "Local (in scanner)", "per scan", "live price fetch + zone computation"),
 ("Decision engine", "Local (in scanner)", "per ticker", "zone → trend/momentum gate → action verb + notifier prompt"),
 ("Notifier", "Local (in scanner)", "per alert", "Anthropic Haiku 4.5 prompt → Telegram message"),
 ("SpotGamma capture", "Scrape skill", "daily", "options walls / gamma → spotgamma_snapshots"),
 ("Macro Show capture", "Scrape skill", "per episode", "qualitative commentary → hedgeye_macro_show"),
 ("Reconciler", "Local tool", "on demand", "match trades↔alerts → outcomes_log PnL corpus"),
]:
    w(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 3 — CURRENT BUGS & OPEN ISSUES  (lead)
# ════════════════════════════════════════════════════════════════════
w("## SECTION 3 — Current bugs & open issues  ⟵ *primary focus for the second opinion*")
w()
w("### 3.1 — THE AMZN BUG: bot ignored Hedgeye's directional bias field, used only numeric bounds")
w()
w("**Severity:** High (silent, multi-week). **Status:** discovered tonight (2026-06-10), fix in progress.")
w()
w("**What happened.** Hedgeye Risk Ranges publish, per ticker, both a numeric band "
"(`buy_trade` low / `sell_trade` high / `prev_close`) **and a directional `trend` field** that "
"encodes Hedgeye's *bias* — e.g. **BEARISH @ TREND** vs **BULLISH @ TREND**. The scanner's "
"zone logic was keying off **only the numeric bounds** to decide where price sat in the band, and "
"the action mapping treated the band symmetrically. The **bias/`trend` field was not weighted into "
"the action decision.** Consequence: for a name Hedgeye holds **bearish** (e.g. **AMZN**), when "
"price drifted to the **low edge** of the band the bot could emit a **long-biased \"BUY / mean-revert "
"up\"** style action — directly contradicting Hedgeye's published short bias. That violates the "
"north-star principle (Section 1): *Hedgeye direction is authoritative; never fade it.*")
w()
w("**Why it was silent.** The numeric zone math was correct, alerts looked plausible, and the band "
"edges are exactly where alerts fire — so the wrong-sided suggestions were indistinguishable from "
"correct ones without cross-checking the `trend` bias. It persisted for **weeks**.")
w()
w("**Where it lives in the data.** The bias is the `trend` column of the risk-range table:")
rr_trend = q("""SELECT trend, count(*) AS n FROM hedgeye_risk_ranges
                WHERE signal_date = (SELECT max(signal_date) FROM hedgeye_risk_ranges)
                GROUP BY trend ORDER BY n DESC""")
table(rr_trend, ["trend", "n"])
w()
w("**AMZN's recent risk-range rows (note the `trend` bias the bot was ignoring):**")
amzn = q("""SELECT signal_date, trend, buy_trade, sell_trade, prev_close
            FROM hedgeye_risk_ranges WHERE ticker='AMZN'
            ORDER BY signal_date DESC LIMIT 15""")
table(amzn)
w()
w("**AMZN alerts the bot actually fired (did any suggest a long-side action against the bias?):**")
amzn_al = q("""SELECT fired_at, suggested_action, range_zone, boundary, framework_alignment,
                      left(recommendation_text,160) AS rec
               FROM alerts_fired WHERE ticker='AMZN' ORDER BY fired_at DESC LIMIT 30""")
table(amzn_al, ["fired_at","suggested_action","range_zone","boundary","framework_alignment","rec"])
w()
w("**Fix direction (for review).** The action gate must consult Hedgeye's `trend` bias as a hard "
"constraint: on a BEARISH-trend name, low-edge touches should map to **COVER/WATCH (short-side "
"management)**, never **BUY**; high-edge touches map to **SHORT/add**. Symmetric for BULLISH names. "
"Question for the reviewer: is a hard bias-gate the right design, or should bias be a weighted vote "
"that price-action can override at extremes? See `decision_engine` trend+momentum gate in Section 4.")
w()
w("### 3.2 — All 9 ETF Pro Shorts missing from the MFR monitored watchlist")
etf_latest = q("SELECT max(week_of) AS w FROM hedgeye_etf_pro_ranges")
ew = etf_latest[0]["w"] if etf_latest and "_error" not in etf_latest[0] else None
etf_all = q("""SELECT ticker, range_low, range_high FROM hedgeye_etf_pro_ranges
               WHERE week_of=(SELECT max(week_of) FROM hedgeye_etf_pro_ranges) ORDER BY ticker""")
mon = q("SELECT DISTINCT ticker FROM monitored_tickers")
mon_set = {r["ticker"] for r in mon if "_error" not in r}
etf_tkrs = [r["ticker"] for r in etf_all if "_error" not in r]
missing = [t for t in etf_tkrs if t not in mon_set]
w(f"- Latest ETF Pro week: **{ew}**, {len(etf_tkrs)} tickers published.")
w(f"- ETF Pro tickers **NOT** in `monitored_tickers`: **{len(missing)}** → "
  f"{', '.join(missing) if missing else '(none)'}")
w("- Operator-reported: the ETF Pro **Short** sleeve in particular never made it into the MFR "
  "watchlist, so those names were invisible to live price monitoring. Diagnostics for this gap "
  "exist as uncommitted scratch scripts.")
w()
w("### 3.3 — Scanner runs locally, not on Railway (single point of failure)")
w("- The price scanner + decision engine run on the operator's **local Windows workstation** via a "
"Scheduled Task; only the email parser + Postgres are on Railway. If the laptop is asleep/off, "
"**no alerts fire**. Quad override env vars live only in the Railway dashboard, so a *local* "
"universe resolution defaults both Quads to **Quad 1** unless the operator exports "
"`CURRENT_MONTHLY_QUAD_OVERRIDE` / `CURRENT_QUARTERLY_QUAD_OVERRIDE`.")
w()
w("### 3.4 — A malformed (corrupted-name) table exists in Postgres")
tbls = q("""SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' ORDER BY table_name""")
bad = [t["table_name"] for t in tbls if "ount(" in t["table_name"] or "count(" in t["table_name"].lower()]
w(f"- A table with a corrupted name (likely a stray `CREATE TABLE AS` typo) exists: "
  f"`{bad[0] if bad else '(see Section 6)'}` — 0 rows, safe to DROP.")
w()
w("### 3.5 — In-flight / uncommitted work tonight")
w("```")
w(sh("git status --short"))
w("```")
w("- `price_monitor.py` has uncommitted modifications. ~18 untracked `_*.py` diagnostics + "
  "`imports/`, `reports/`, `outputs/` artifacts (investigative, unshipped). "
  "`command_bridge.py.bak_pgqueue` is a leftover backup.")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 4 — QUESTIONS FOR THE SECOND OPINION
# ════════════════════════════════════════════════════════════════════
w("## SECTION 4 — What the operator wants the second opinion ON")
w()
for i, qq_ in enumerate([
 "Is the AMZN bias-gate fix (Section 3.1) directionally correct, and is a *hard* bias constraint "
 "safer than a weighted vote? What edge cases break it (e.g. Hedgeye flips bias intraday, or "
 "`trend` is null)?",
 "Does the polling-universe union + staleness logic (Section 4 code, Section 7 freshness) risk "
 "either missing live Hedgeye names or polling stale ones? Is the 3-day vs 14-day staleness split "
 "defensible?",
 "In the trend+momentum gate, does every (zone × side × trend × momentum) branch respect "
 "'never fade Hedgeye'? Are there branches that can emit a long action on a bearish name?",
 "Alert signal-to-noise: given the alert volume vs the operator's actual trades (Sections 5–6), "
 "is the bot over-alerting? What dedup/cooldown would you add?",
 "Single-point-of-failure: the scanner runs on one local laptop. Worth moving to Railway/cron? "
 "What breaks if the Quad override env vars are wrong or unset?",
 "Data model: are alerts_fired / actions_log / outcomes_log sufficient to train a useful "
 "'should I take this alert?' model, or what's missing?",
], 1):
    w(f"{i}. {qq_}")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 5 — FULL PYTHON SOURCE
# ════════════════════════════════════════════════════════════════════
w("## SECTION 5 — Full Python source (verbatim, secrets redacted)")
w()
w("> Ordered: core scanner/decision modules first, then all other top-level modules "
  "alphabetically, then the `tools/` package. `parsers/` as a directory does not exist — parsing "
  "lives in the top-level `parser_*.py` modules listed here.")
w()
CORE_FIRST = ["proactive_scanner.py", "decision_engine.py", "price_monitor.py",
              "recommender.py", "notifier.py", "db_pg.py", "mfr_client.py",
              "email_parser.py", "classifier.py", "main.py"]
top_py = sorted(glob.glob("*.py"))
top_py = [f for f in top_py if f != "_export_bot_state.py"]   # don't include this generator
ordered = [f for f in CORE_FIRST if f in top_py] + [f for f in top_py if f not in CORE_FIRST]

w("### 5A — Core + top-level modules")
w()
for f in ordered:
    dump_file(os.path.join(r"C:\Projects\hedgeye-bot", f))

w("### 5B — `tools/` package")
w()
for f in sorted(glob.glob("tools/*.py")):
    dump_file(f)
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 6 — DATABASE SCHEMA + ROW COUNTS
# ════════════════════════════════════════════════════════════════════
w("## SECTION 6 — Database schema (every table) + row counts")
w()
counts = {}
w("### Row counts")
w("| table | rows |")
w("| --- | --- |")
for r in tbls:
    t = r["table_name"]
    c = q(f'SELECT count(*) AS n FROM "{t}"')
    n = c[0].get("n","ERR") if c else "ERR"
    counts[t] = n
    w(f"| `{t}` | {n} |")
w()
w("### Full schema per table (`column · type · nullable · default`)")
w()
for r in tbls:
    t = r["table_name"]
    w(f"#### `{t}`  ({counts.get(t,'?')} rows)")
    cols = q("""SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns WHERE table_name=%s
                ORDER BY ordinal_position""", (t,))
    table(cols, ["column_name","data_type","is_nullable","column_default"])
    # indexes
    idx = q("""SELECT indexname, indexdef FROM pg_indexes WHERE tablename=%s""", (t,))
    if idx and "_error" not in idx[0]:
        for ix in idx:
            w(f"- idx `{ix['indexname']}`: `{redact(ix['indexdef'])}`")
    w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 7 — FULL DATA DUMPS
# ════════════════════════════════════════════════════════════════════
w("## SECTION 7 — Full data dumps")
w()

w("### 7.1 — EVERY alert in `alerts_fired` (full history)")
allal = q("""SELECT fired_at, ticker, suggested_action, range_zone, boundary,
                    framework_alignment, quad_regime, monthly_quad, quarterly_quad,
                    price_at_fire, suggested_dollars, left(recommendation_text,180) AS rec
             FROM alerts_fired ORDER BY fired_at DESC""")
w(f"_{0 if (allal and '_error' in allal[0]) else len(allal)} rows_")
table(allal, ["fired_at","ticker","suggested_action","range_zone","boundary",
              "framework_alignment","quad_regime","monthly_quad","quarterly_quad",
              "price_at_fire","suggested_dollars","rec"])
w()

w("### 7.2 — EVERY trade in `actions_log` (full history; account numbers omitted)")
allact = q("""SELECT run_date, account_name, action, normalized_symbol AS ticker,
                     qty, price, amount, type, source, settlement_date
              FROM actions_log ORDER BY run_date DESC, ticker""")
w(f"_{0 if (allact and '_error' in allact[0]) else len(allact)} rows_")
table(allact, ["run_date","account_name","action","ticker","qty","price","amount",
               "type","source","settlement_date"])
w()

w("### 7.3 — EVERY Keith PS action in `hedgeye_portfolio_actions` (full history)")
allpa = q("""SELECT action_date, ticker, action, bps, left(commentary_raw,200) AS commentary
             FROM hedgeye_portfolio_actions ORDER BY action_date DESC, ticker""")
w(f"_{0 if (allpa and '_error' in allpa[0]) else len(allpa)} rows_")
table(allpa, ["action_date","ticker","action","bps","commentary"])
w()

w("### 7.4 — Latest `hedgeye_signal_strength` snapshot (full)")
ssd = q("SELECT max(snapshot_date) AS d FROM hedgeye_signal_strength")
ssdate = ssd[0]["d"] if ssd and "_error" not in ssd[0] else None
w(f"_snapshot_date = {ssdate}_")
ss = q("""SELECT ticker, is_added_today, is_removed_today
          FROM hedgeye_signal_strength
          WHERE snapshot_date=(SELECT max(snapshot_date) FROM hedgeye_signal_strength)
          ORDER BY ticker""")
w(f"_{0 if (ss and '_error' in ss[0]) else len(ss)} rows_")
table(ss, ["ticker","is_added_today","is_removed_today"])
w()

w("### 7.5 — Latest `hedgeye_etf_pro_ranges` snapshot (full)")
w(f"_week_of = {ew}_")
etf = q("""SELECT ticker, range_low, range_high, description
           FROM hedgeye_etf_pro_ranges
           WHERE week_of=(SELECT max(week_of) FROM hedgeye_etf_pro_ranges)
           ORDER BY ticker""")
w(f"_{0 if (etf and '_error' in etf[0]) else len(etf)} rows_")
table(etf, ["ticker","range_low","range_high","description"])
w()

w("### 7.6 — Latest `hedgeye_portfolio_solutions` rank composition (full)")
psd = q("SELECT max(snapshot_date) AS d FROM hedgeye_portfolio_solutions")
psdate = psd[0]["d"] if psd and "_error" not in psd[0] else None
w(f"_snapshot_date = {psdate}_")
ps = q("""SELECT rank, ticker, rank_change_1w, rank_change_1m, monthly_quad, quarterly_quad
          FROM hedgeye_portfolio_solutions
          WHERE snapshot_date=(SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions)
          ORDER BY rank""")
w(f"_{0 if (ps and '_error' in ps[0]) else len(ps)} rows_")
table(ps, ["rank","ticker","rank_change_1w","rank_change_1m","monthly_quad","quarterly_quad"])
w()

w("### 7.7 — Latest `hedgeye_keiths_signals` snapshot (full)")
ksd = q("SELECT max(signal_date) AS d FROM hedgeye_keiths_signals")
ksdate = ksd[0]["d"] if ksd and "_error" not in ksd[0] else None
w(f"_signal_date = {ksdate}_")
ks = q("""SELECT ticker, side FROM hedgeye_keiths_signals
          WHERE signal_date=(SELECT max(signal_date) FROM hedgeye_keiths_signals)
          ORDER BY side, ticker""")
w(f"_{0 if (ks and '_error' in ks[0]) else len(ks)} rows_")
table(ks, ["ticker","side"])
w()

w("### 7.8 — Last 30 days of `mfr_snapshots` for major tickers")
MAJORS = ['SPY','QQQ','IWM','DIA','TLT','HYG','LQD','UUP','GLD','SLV','USO','XLE','XLF','XLK',
          'AMZN','NVDA','AAPL','MSFT','META','GOOGL','TSLA','BTC','ETH','VIX']
since30 = (TODAY - timedelta(days=30)).isoformat()
mfr = q("""SELECT snapshot_date, ticker, price, range_low, range_high, trend_signal,
                  momentum_signal, hurst, iv, rv, daily_pct_change
           FROM mfr_snapshots
           WHERE snapshot_date >= %s AND ticker = ANY(%s)
           ORDER BY ticker, snapshot_date DESC""", (since30, MAJORS))
w(f"_majors filter: {', '.join(MAJORS)}_")
w(f"_{0 if (mfr and '_error' in mfr[0]) else len(mfr)} rows_")
table(mfr, ["snapshot_date","ticker","price","range_low","range_high","trend_signal",
            "momentum_signal","hurst","iv","rv","daily_pct_change"])
w()

w("### 7.9 — Current `positions_snapshot` (latest; account numbers omitted)")
pd_ = q("SELECT max(snapshot_date) AS d FROM positions_snapshot")
pdate = pd_[0]["d"] if pd_ and "_error" not in pd_[0] else None
w(f"_snapshot_date = {pdate}_")
pos = q("""SELECT account_name, normalized_symbol AS symbol, quantity, last_price,
                  current_value, source
           FROM positions_snapshot
           WHERE snapshot_date=(SELECT max(snapshot_date) FROM positions_snapshot)
           ORDER BY account_name, symbol""")
table(pos, ["account_name","symbol","quantity","last_price","current_value","source"])
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 8 — MIGRATIONS (full content)
# ════════════════════════════════════════════════════════════════════
w("## SECTION 8 — Database migrations (full content, in order)")
w()
for f in sorted(glob.glob("migrations/*.sql")):
    dump_file(f, lang="sql")
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 9 — CONFIGURATION
# ════════════════════════════════════════════════════════════════════
w("## SECTION 9 — Configuration")
w()
w("### 9.1 — Environment variables (names + purpose; values redacted)")
ENV_DESC = {
 "ANTHROPIC_API_KEY": "Anthropic API key — notifier prompts (Haiku 4.5) + classifier",
 "OPENAI_API_KEY": "OpenAI API key — auxiliary/embedding or transcription paths",
 "DATABASE_PUBLIC_URL": "Railway Postgres DSN (public proxy) — used when running locally via railway run",
 "ICLOUD_EMAIL": "iCloud inbox address the email parser reads Hedgeye mail from",
 "ICLOUD_APP_PASSWORD": "iCloud app-specific password for IMAP (not the Apple ID password)",
 "MFR_API_TOKEN": "MenthorQ/MFR API token — options/vol range snapshots",
 "TELEGRAM_BOT_TOKEN": "Telegram bot token — pushes alerts to operator's phone",
 "TELEGRAM_CHAT_ID": "Telegram chat id the alerts are delivered to",
}
env_names = []
try:
    for ln in open(".env", encoding="utf-8", errors="replace"):
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", ln)
        if m: env_names.append(m.group(1))
except Exception:
    pass
# also document runtime-only (Railway-injected) vars referenced in code
RUNTIME_ONLY = {
 "DATABASE_URL": "Railway-internal Postgres DSN (injected in the deployed parser)",
 "CURRENT_MONTHLY_QUAD_OVERRIDE": "Forces the monthly Quad for universe resolution (Railway dashboard only)",
 "CURRENT_QUARTERLY_QUAD_OVERRIDE": "Forces the quarterly Quad (Railway dashboard only)",
 "PRICE_FEED": "Selects live price backend (e.g. alpaca / ibkr / yfinance)",
 "DB_PATH": "Legacy SQLite path (old database.py path; pre-Postgres)",
}
w("| variable | present in local .env | purpose |")
w("| --- | --- | --- |")
for k in sorted(set(env_names) | set(ENV_DESC)):
    w(f"| `{k}` | {'yes' if k in env_names else 'no'} | {ENV_DESC.get(k,'(see code)')} |")
for k, d in RUNTIME_ONLY.items():
    w(f"| `{k}` | runtime-only | {d} |")
w()
w("### 9.2 — Config files (full content)")
for cfg in sorted(glob.glob("config/*.yaml")) + sorted(glob.glob("config/*.yml")):
    dump_file(cfg, lang="yaml")
w()
w("### 9.3 — Per-source staleness windows (from the universe resolver)")
w("| source | window (days) |")
w("| --- | --- |")
for k, v in [("etf_pro",14),("signal_strength",3),("signal_strength_ks (Keith)",14),
             ("portfolio_solutions",3),("investing_ideas",3),("risk_range",3)]:
    w(f"| {k} | {v} |")
w()
w("---")
w()

# ════════════════════════════════════════════════════════════════════
#  SECTION 10 — AGENT MEMORY
# ════════════════════════════════════════════════════════════════════
w("## SECTION 10 — Agent design memory (principles being tracked)")
w()
w("> These are the persistent memory notes the build agent keeps about the operator, the "
  "framework, and design decisions. Included so the reviewer understands intent and constraints.")
w()
MEM_DIRS = [
 r"C:\Users\bogac\AppData\Roaming\Claude\local-agent-mode-sessions\e25dbe6b-a1f6-4500-84f2-c964ee155cea\d9552e59-f2a7-4327-a6a6-a9ce7033f0c8\agent\memory",
 r"C:\Users\bogac\AppData\Roaming\Claude\local-agent-mode-sessions\e25dbe6b-a1f6-4500-84f2-c964ee155cea\d9552e59-f2a7-4327-a6a6-a9ce7033f0c8\spaces\1b335f5a-88c3-4b42-a84a-c6bff8fd8bea\memory",
]
for d in MEM_DIRS:
    if not os.path.isdir(d): continue
    label = "agent/memory" if d.endswith("memory") and "spaces" not in d else "space/memory"
    w(f"### Memory store — `{label}`")
    w()
    # MEMORY.md index first
    idx = os.path.join(d, "MEMORY.md")
    files = sorted(glob.glob(os.path.join(d, "*.md")))
    for mp in ([idx] if os.path.exists(idx) else []) + [f for f in files if os.path.basename(f) != "MEMORY.md"]:
        name = os.path.basename(mp)
        try:
            content = open(mp, encoding="utf-8", errors="replace").read()
        except Exception as e:
            content = f"(could not read: {e})"
        w(f"#### `{name}`")
        w("```markdown")
        W.write(redact(content))
        if not content.endswith("\n"): w("")
        w("```")
        w()
w("---")
w()
w(f"_End of export — generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
  f"Read-only; secrets redacted._")

# ════════════════════════════════════════════════════════════════════
#  WRITE + REPORT
# ════════════════════════════════════════════════════════════════════
data = W.getvalue()
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(data)
size = os.path.getsize(OUT)
chars = len(data)
toks = chars // 4
sys.stdout.buffer.write((
    f"WROTE {OUT}\n"
    f"BYTES {size}\n"
    f"KB {round(size/1024,1)}\n"
    f"MB {round(size/1048576,2)}\n"
    f"CHARS {chars}\n"
    f"TOKEN_EST {toks}\n"
    f"FITS_200K {'YES' if toks <= 200000 else 'NO'}\n"
    f"FITS_1M {'YES' if toks <= 1000000 else 'NO'}\n"
).encode("utf-8"))
print("---TOC---")
for line in data.splitlines():
    if line.startswith("## SECTION") or line.startswith("# Hedgeye"):
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
conn.close()
