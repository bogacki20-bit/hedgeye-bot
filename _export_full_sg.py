# -*- coding: utf-8 -*-
"""COMPREHENSIVE read-only bot-state export -> single markdown file for a
second-opinion AI focused on SpotGamma integration design.
No DB/code mutations. Secrets + raw $ balances redacted/omitted."""
import os, io, re, sys, glob, json, subprocess, random, shutil, traceback
from datetime import date, datetime, timezone, timedelta

os.chdir(r"C:\Projects\hedgeye-bot")
from dotenv import load_dotenv
load_dotenv(r"C:\Projects\hedgeye-bot\.env")
import psycopg2, psycopg2.extras

random.seed(42)
TODAY = date(2026, 6, 19)
CUTOFF_30 = (TODAY - timedelta(days=30)).isoformat()
CUTOFF_7  = (TODAY - timedelta(days=7)).isoformat()
CUTOFF_14 = (TODAY - timedelta(days=14)).isoformat()
CUTOFF_90 = (TODAY - timedelta(days=90)).isoformat()

OUT_PRIMARY = r"C:\Users\bogac\Desktop\bot_state_FULL_export_2026-06-15.md"
OUT_COPIES = [
    r"C:\Users\bogac\Downloads\bot_state_FULL_export_2026-06-15.md",
    r"C:\Users\bogac\OneDrive\Desktop\bot_state_FULL_export_2026-06-15.md",
]
ERRORS = []   # collected, surfaced in Section 8
ROW_CAP = 150 # per-window safety cap to keep file pasteable (<1M tokens); caps are logged

W = io.StringIO()
def w(s=""): W.write(s + "\n")

# ───────────────────────── secret redaction ─────────────────────────
SECRET_PATTERNS = [
    (re.compile(r"postgres(?:ql)?://[^\s'\"]+", re.I), "postgresql://<REDACTED-DSN>"),
    (re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"), "<REDACTED-API-KEY>"),
    (re.compile(r"(\d{6,12}:[A-Za-z0-9_\-]{30,})"), "<REDACTED-TELEGRAM-TOKEN>"),
    (re.compile(r"""((?:API_KEY|TOKEN|PASSWORD|SECRET|APP_PASSWORD|DSN|BEARER)\s*[:=]\s*)(['"])[^'"]+\2""", re.I),
     r"\1\2<REDACTED>\2"),
]
def redact(text):
    if not text: return text
    for pat, repl in SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text

# columns that are raw $ balances — omitted per operator constraint
BALANCE_COLS = {"current_value","market_value","account_value","total_value",
                "balance","equity","net_liq","cash_balance","total_cost","cost_basis"}

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60).stdout.strip()
    except Exception as e:
        return f"(error: {e})"

DSN = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
conn = psycopg2.connect(DSN); conn.autocommit = True
def q(sql, args=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, args or ()); return cur.fetchall()
    except Exception as e:
        conn.rollback(); return [{"_error": str(e)}]
    finally:
        cur.close()

def is_err(rows): return rows and isinstance(rows[0], dict) and "_error" in rows[0]

def table(rows, cols=None, maxcell=220):
    if not rows:
        w("_(no rows)_"); return
    if is_err(rows):
        w("> query error: `%s`" % rows[0]["_error"]); return
    cols = cols or [c for c in rows[0].keys() if c.lower() not in BALANCE_COLS]
    w("| " + " | ".join(cols) + " |")
    w("| " + " | ".join("---" for _ in cols) + " |")
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            v = "" if v is None else str(v)
            v = redact(v).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
            if len(v) > maxcell: v = v[:maxcell-3] + "..."
            cells.append(v)
        w("| " + " | ".join(cells) + " |")

def dump_file(path, lang="python", base=r"C:\Projects\hedgeye-bot"):
    rel = os.path.relpath(path, base).replace("\\", "/")
    try:
        content = open(path, encoding="utf-8", errors="replace").read()
        nlines = content.count("\n") + 1
        w(f"#### `{rel}` — {nlines} lines")
        w(f"```{lang}")
        W.write(redact(content))
        if not content.endswith("\n"): w("")
        w("```")
    except Exception as e:
        ERRORS.append(f"dump_file {rel}: {e}")
        w(f"#### `{rel}` — could not read: {e}")
    w()

# ───────── generic table metadata helpers ─────────
DATE_PRIORITY = ["fired_at","captured_at","created_at","snapshot_date","signal_date",
                 "action_date","run_date","week_of","source_date","print_time","fetched_at",
                 "parsed_at","published_at","effective_from_date","snapshot_ts","ts","date",
                 "updated_at","signal_ts","source_ts"]
def cols_of(t):
    rows = q("""SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name=%s ORDER BY ordinal_position""", (t,))
    if is_err(rows): return []
    return [(r["column_name"], r["data_type"]) for r in rows]
def date_col_of(t, cols):
    names = [c for c,_ in cols]
    for p in DATE_PRIORITY:
        if p in names: return p
    for c, dt in cols:
        if "timestamp" in dt or dt == "date": return c
    return None

# ════════════════════════════════════════════════════════════════════
TITLE = "Hedgeye-Bot — COMPREHENSIVE State Export (SpotGamma integration review)"
w(f"# {TITLE}")
w()
w(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
w("**Filename:** `bot_state_FULL_export_2026-06-15.md`  ")
w("**Purpose:** Self-contained snapshot to paste into a *separate* AI chat for an "
  "independent architecture/integration review — specifically **SpotGamma data integration design**.")
w()
w("> Read-only evidence. Secrets (API keys, DB DSNs, tokens, passwords) are redacted; raw account "
  "$ balances are omitted (ticker/qty/price retained). High-volume table windows are capped at "
  f"{ROW_CAP} rows with the cap logged in Section 8. Everything else is verbatim.")
w()
w("---")
w()

# ════════════════ PREAMBLE ════════════════
w("## PREAMBLE")
w()
w("### What this bot is")
w("A **multi-source trading-signal aggregator** for a single operator. It fuses four research "
  "streams — **Hedgeye Risk Management** (north star), **MFR** (MenthorQ options/vol ranges), "
  "**SpotGamma** (dealer-gamma / options positioning), and **Tier 1 Alpha** — resolves a daily "
  "*polling universe* of tickers, monitors live prices against published **risk-range bands**, and "
  "fires **Telegram alerts** to the operator's phone. Every fired alert + the operator's later "
  "manual trades are persisted into an **ML training corpus**. The bot never executes trades.")
w()
w("### Operator's framework")
w("- **Hedgeye = north star.** Hedgeye's published direction (TREND bias) is authoritative; the bot "
  "must never invent a thesis that fades Hedgeye.")
w("- **MFR / SpotGamma / Tier 1 Alpha = supplementary context** — they refine timing/sizing within "
  "Hedgeye's bias, never override it.")
w("- **Quad regime drives routing.** The Hedgeye Quad macro model (Quad 1–4, monthly + quarterly) "
  "selects asset bias and long/short sleeves.")
w()
w("### Current architecture (one paragraph)")
w("A **Python scanner runs locally** on the operator's Windows workstation via a 15-minute Scheduled "
  "Task. It reads/writes a **Postgres database hosted on Railway** (single source of truth). A "
  "**FastAPI/Flask ingest endpoint** (`/api/scrape_ingest`, in-process inside `main.py` on Railway) "
  "accepts captures POSTed by **scheduled Cowork SKILLs** that scrape data sources (SpotGamma "
  "premarket/postclose sweeps, 15-min tape watch, Macro Show). The email parser ingests Hedgeye "
  "emails from iCloud IMAP into product-specific tables. Notifier prompts use **Anthropic Haiku 4.5**.")
w()
w("### Why this export exists")
w("The operator wants a second-opinion chat to help **redesign / improve SpotGamma integration "
  "specifically.** SG is currently captured but NOT used in the live decision path (stripped during "
  "a cost-driven rollback). See Section 0 and Section 4.")
w()
w("---")
w()

# ════════════════ SECTION 0 ════════════════
w("## SECTION 0 — Open question for the second-opinion chat")
w()
w("> \"I want help thinking through SpotGamma data integration. Currently SG is partially captured but "
  "the trading bot does not use it for decisions — it was stripped from the live decision path during "
  "a rollback. I want to bring it back smarter. Here's everything that exists today. Help me design "
  "the integration so SG data MEANINGFULLY informs the bot's alerts without overwhelming the API "
  "cost. Be opinionated.\"")
w()
w("---")
w()

# ════════════════ SECTION 1 — CODE STATE ════════════════
w("## SECTION 1 — Code state")
w()
w("### 1.1 — Git")
w(f"- **master HEAD:** `{sh('git rev-parse master')}`")
w(f"- **main HEAD:** `{sh('git rev-parse main')}`")
w(f"- **current branch:** `{sh('git rev-parse --abbrev-ref HEAD')}`")
w()
w("**Last 30 commits:**")
w("```text")
w(sh('git log -30 --pretty=format:"%h %ad %s" --date=short'))
w("```")
w()
w("**`git status` (uncommitted / untracked):**")
w("```text")
w(sh("git status"))
w("```")
w()

w("### 1.2 — Top-level Python modules (verbatim)")
w()
CORE_FIRST = ["main.py","api.py","proactive_scanner.py","decision_engine.py","price_monitor.py",
              "recommender.py","db_pg.py","mfr_client.py","yfinance_client.py","spotgamma_client.py",
              "email_parser.py","classifier.py","notifier.py","telegram_handler.py",
              "monitor_context.py","corpus_ingest.py","corpus_rag.py","unified_refresh.py"]
top_py = sorted(glob.glob("*.py"))
top_py = [f for f in top_py if f not in ("_export_full_sg.py","_export_bot_state.py")]
ordered = [f for f in CORE_FIRST if f in top_py] + [f for f in top_py if f not in CORE_FIRST]
for f in ordered:
    dump_file(os.path.join(r"C:\Projects\hedgeye-bot", f))

w("### 1.3 — `parser_*.py` (Hedgeye product parsers)")
w("> Note: parsers live as top-level `parser_*.py` modules (already included in 1.2). No separate "
  "`parsers/` package exists.")
w()

w("### 1.4 — `tools/` package (verbatim)")
w()
for f in sorted(glob.glob("tools/*.py")):
    dump_file(f)

w("### 1.5 — `migrations/` (verbatim, in order)")
w()
for f in sorted(glob.glob("migrations/*.sql")):
    dump_file(f, lang="sql")

w("### 1.6 — `config/*.yaml` (verbatim)")
w()
for cfg in sorted(glob.glob("config/*.yaml")) + sorted(glob.glob("config/*.yml")):
    dump_file(cfg, lang="yaml")

w("### 1.7 — Deploy / infra files")
w()
dump_file(os.path.join(r"C:\Projects\hedgeye-bot","requirements.txt"), lang="text")
dump_file(os.path.join(r"C:\Projects\hedgeye-bot","railway.toml"), lang="toml")
for ps in sorted(glob.glob("*.ps1")):
    dump_file(os.path.join(r"C:\Projects\hedgeye-bot", ps), lang="powershell")
for f in sorted(glob.glob("deploy/ibkr-gateway/*")):
    if os.path.isfile(f):
        lang = "dockerfile" if f.lower().endswith("dockerfile") else ("toml" if f.endswith(".toml") else "markdown")
        dump_file(f, lang=lang)
w("---")
w()

# ════════════════ SECTION 2 — DATABASE STATE ════════════════
w("## SECTION 2 — Database state")
w()
tbls = q("""SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' ORDER BY table_name""")
tnames = [r["table_name"] for r in tbls if not is_err([r])]

# overview counts
w("### 2.0 — Table inventory")
w("| table | rows | date column | most recent |")
w("| --- | --- | --- | --- |")
META = {}
for t in tnames:
    cols = cols_of(t)
    cnt = q(f'SELECT count(*) AS n FROM "{t}"')
    n = cnt[0]["n"] if not is_err(cnt) else "ERR"
    dc = date_col_of(t, cols)
    recent = ""
    if dc:
        mr = q(f'SELECT max("{dc}") AS m FROM "{t}"')
        recent = str(mr[0]["m"]) if not is_err(mr) else ""
    META[t] = {"cols": cols, "n": n, "dc": dc, "recent": recent}
    w(f"| `{t}` | {n} | {dc or '—'} | {recent} |")
w()

def schema_block(t):
    cols = META[t]["cols"]
    w("| column | type | nullable | default |")
    w("| --- | --- | --- | --- |")
    raw = q("""SELECT column_name, data_type, is_nullable, column_default
               FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position""",(t,))
    for r in raw:
        if is_err([r]): continue
        w(f"| {r['column_name']} | {r['data_type']} | {r['is_nullable']} | "
          f"{redact(str(r['column_default'])) if r['column_default'] else ''} |")

def dump_table(t):
    m = META[t]; n = m["n"]; dc = m["dc"]
    w(f"#### `{t}` — {n} rows" + (f", most recent `{m['recent']}` (`{dc}`)" if dc else ""))
    schema_block(t)
    if n == "ERR":
        w("> count errored; skipping dump"); w(); return
    if n == 0:
        w("_(table empty)_"); w(); return
    disp = [c for c,_ in m["cols"] if c.lower() not in BALANCE_COLS]
    sel = ", ".join(f'"{c}"' for c in disp)
    order = f' ORDER BY "{dc}" DESC' if dc else ""
    if n < 500:
        rows = q(f'SELECT {sel} FROM "{t}"{order}')
        w(f"_all {len(rows) if not is_err(rows) else 0} rows:_")
        table(rows)
    elif n <= 10000:
        # last 30 days + random 100 of older
        if dc:
            recent = q(f'SELECT {sel} FROM "{t}" WHERE "{dc}" >= %s{order} LIMIT {ROW_CAP}', (CUTOFF_30,))
            nrec = 0 if is_err(recent) else len(recent)
            if nrec >= ROW_CAP: ERRORS.append(f"{t}: 30-day window capped at {ROW_CAP} rows")
            w(f"_last 30 days (since {CUTOFF_30}): {nrec} rows_")
            table(recent)
            older = q(f'SELECT {sel} FROM "{t}" WHERE "{dc}" < %s ORDER BY random() LIMIT 100',(CUTOFF_30,))
            w(); w(f"_random 100-row sample of older data:_")
            table(older)
        else:
            rows = q(f'SELECT {sel} FROM "{t}" LIMIT {ROW_CAP}')
            w(f"_(no date column) first {ROW_CAP} rows:_")
            table(rows)
    else:
        if dc:
            recent = q(f'SELECT {sel} FROM "{t}" WHERE "{dc}" >= %s{order} LIMIT {ROW_CAP}', (CUTOFF_30,))
            nrec = 0 if is_err(recent) else len(recent)
            if nrec >= ROW_CAP: ERRORS.append(f"{t}: 30-day window capped at {ROW_CAP} rows")
            w(f"**This table is large — {n} total rows historical.** Showing last 30 days "
              f"(since {CUTOFF_30}): {nrec} rows.")
            table(recent)
        else:
            rows = q(f'SELECT {sel} FROM "{t}" LIMIT {ROW_CAP}')
            w(f"**Large table — {n} rows, no date column.** First {ROW_CAP}:")
            table(rows)
    w()

# corpus_documents gets special treatment (top 5 per source)
w("### 2.1 — `corpus_documents` (sampled by source)")
cd_counts = q("""SELECT source, count(*) AS n, max(source_date) AS latest
                 FROM corpus_documents GROUP BY source ORDER BY n DESC""")
w("**Per-source counts:**")
table(cd_counts, ["source","n","latest"])
w()
w("**Top 5 most-recent rows per source (full_text truncated to 600 chars):**")
if not is_err(cd_counts):
    for r in cd_counts:
        src = r["source"]
        w(f"##### source = `{src}` ({r['n']} docs)")
        rows = q("""SELECT id, source_date, document_type, title,
                           left(full_text,400) AS full_text_excerpt, captured_at
                    FROM corpus_documents WHERE source=%s
                    ORDER BY COALESCE(source_date::text, captured_at::text) DESC LIMIT 5""",(src,))
        table(rows, ["id","source_date","document_type","title","full_text_excerpt","captured_at"], maxcell=420)
        w()
w()

# every other table generically
w("### 2.2 — All other tables (schema + tiered data dump)")
w()
SKIP_FULLDUMP = {"corpus_documents"}
for t in tnames:
    if t in SKIP_FULLDUMP:
        continue
    try:
        dump_table(t)
    except Exception as e:
        ERRORS.append(f"dump_table {t}: {e}")
        w(f"#### `{t}` — DUMP ERROR: {e}"); w()
w("---")
w()

# ════════════════ SECTION 3 — POLLING UNIVERSE ════════════════
w("## SECTION 3 — Polling universe + active slice")
w()
quad_env = {k: os.environ.get(k) for k in
            ["CURRENT_MONTHLY_QUAD_OVERRIDE","CURRENT_QUARTERLY_QUAD_OVERRIDE","QUAD_OVERRIDE"]}
bot_state_quads = q("SELECT key, value, updated_at FROM bot_state ORDER BY key")
w("### 3.1 — Quad env vars + effective values")
w("| var | local .env value |")
w("| --- | --- |")
for k,v in quad_env.items():
    w(f"| `{k}` | {v if v else '(unset)'} |")
w()
w("**`bot_state` table (effective quad source of truth):**")
table(bot_state_quads, ["key","value","updated_at"])
w()
try:
    sys.path.insert(0, r"C:\Projects\hedgeye-bot")
    from tools import active_slice as _as
    pu = _as.polling_universe()
    w(f"### 3.2 — `polling_universe()` — {len(pu)} tickers")
    w("```text")
    w(", ".join(pu))
    w("```")
    w()
    try:
        brk = _as.source_breakdown()
        w("### 3.3 — `source_breakdown()` (tickers by source)")
        for k, v in brk.items():
            vv = v if isinstance(v, list) else [v]
            w(f"- **{k}** ({len(vv)}): {', '.join(map(str, vv))}")
        w()
    except Exception as e:
        ERRORS.append(f"source_breakdown: {e}")
        w(f"_source_breakdown() error: {e}_"); w()
    # both sides / active slice
    try:
        mq = (bot_state_quads[0] and None)
        bs = _as.both_sides()
        w("### 3.4 — `both_sides()` active slice (long / short)")
        w("```text")
        w(str(bs)[:6000])
        w("```")
        w()
    except Exception as e:
        ERRORS.append(f"both_sides: {e}")
        w(f"_both_sides() error: {e}_"); w()
except Exception as e:
    ERRORS.append(f"active_slice import/polling_universe: {e}")
    w(f"### 3.2 — polling_universe() could not be computed: {e}")
    w("```text"); w(traceback.format_exc()[:2000]); w("```"); w()

# mfr_quad_map classification breakdown
w("### 3.5 — `config/mfr_quad_map.yaml` — ticker count by classification")
try:
    import yaml
    with open("config/mfr_quad_map.yaml", encoding="utf-8") as fh:
        mqm = yaml.safe_load(fh)
    def walk_count(obj, acc):
        if isinstance(obj, dict):
            for k,v in obj.items():
                if isinstance(v, (dict,list)): walk_count(v, acc)
        elif isinstance(obj, list):
            acc["_list_items"] = acc.get("_list_items",0)+len(obj)
    # tally by top-level structure
    if isinstance(mqm, dict):
        w("| top-level key | entries |")
        w("| --- | --- |")
        for k, v in mqm.items():
            if isinstance(v, dict): w(f"| {k} | {len(v)} (dict) |")
            elif isinstance(v, list): w(f"| {k} | {len(v)} (list) |")
            else: w(f"| {k} | scalar |")
    w()
except Exception as e:
    ERRORS.append(f"mfr_quad_map parse: {e}")
    w(f"_could not parse mfr_quad_map.yaml: {e}_"); w()
w("---")
w()

# ════════════════ SECTION 4 — SPOTGAMMA DEEP DIVE ════════════════
w("## SECTION 4 — SpotGamma integration deep dive (CRITICAL)")
w()
w("### 4.1 — Current state: stripped from the live decision path")
w("SpotGamma was **removed from the live decision path on 2026-05-24** (commit `efaec0c` "
  "*strip(sg): remove spotgamma from live decision path; preserve snapshots*). A later commit "
  "`5ffe4cb` *feat(sg): re-introduce SpotGamma into decision path — deterministic only* began "
  "bringing it back deterministically. Ingestion never stopped — `spotgamma_snapshots`, `sg_tape`, "
  "and `corpus_documents` rows are still written for future ML training.")
w()
w("**Relevant git history (decision_engine.py, SpotGamma):**")
w("```text")
w(sh('git log --oneline -S "spotgamma" -- decision_engine.py'))
w("```")
w()
w("### 4.2 — The stub that USED to read SG (now always None)")
w("From `decision_engine.py` — the live SG reader is stubbed out:")
seg = sh('git show HEAD:decision_engine.py')
# extract the _get_spotgamma_latest stub region
m = re.search(r"def _get_spotgamma_latest.*?(?=\ndef )", seg, re.S)
if m:
    w("```python"); w(redact(m.group(0).rstrip())); w("```")
else:
    w("_(stub region not located; see decision_engine.py in Section 1.2)_")
w()
# the SpotGamma framing + flow patrol funcs that still exist
m2 = re.search(r"def _spotgamma_framing_line.*?(?=\ndef )", seg, re.S)
if m2:
    w("**Deterministic SG framing helper still present (`_spotgamma_framing_line`):**")
    w("```python"); w(redact(m2.group(0).rstrip())); w("```")
w()
w("### 4.3 — What lands in `spotgamma_snapshots`")
w("Schema + recent rows (last 14 days — the window most relevant to SG design):")
schema_block("spotgamma_snapshots")
w()
sg_cols = [c for c,_ in META["spotgamma_snapshots"]["cols"]
           if c.lower() not in BALANCE_COLS and c not in ("raw_text","full_payload")]
sgrows = q(f'''SELECT {", ".join(f'"{c}"' for c in sg_cols)} FROM spotgamma_snapshots
              WHERE snapshot_date >= %s ORDER BY snapshot_date DESC, ticker LIMIT {ROW_CAP}''',(CUTOFF_14,))
w(f"_last 14 days (since {CUTOFF_14}), raw_text/full_payload omitted: "
  f"{0 if is_err(sgrows) else len(sgrows)} rows_")
table(sgrows, sg_cols)
w()
w("**Distinct `capture_type` values + counts (premarket vs postclose etc.):**")
sgct = q("SELECT capture_type, count(*) n, max(snapshot_date) latest FROM spotgamma_snapshots GROUP BY capture_type ORDER BY n DESC")
table(sgct, ["capture_type","n","latest"])
w()
w("### 4.4 — What lands in `sg_tape` (15-min tape watch — option flow prints)")
schema_block("sg_tape")
w()
tps = q(f'''SELECT id, print_time, symbol, side, buysell, cp, strike, volume, oi, expiration,
                   legs, premium, spot, optprice, schema_version, fetched_at
            FROM sg_tape WHERE print_time >= %s OR fetched_at >= %s
            ORDER BY COALESCE(print_time::text, fetched_at::text) DESC LIMIT {ROW_CAP}''',
        (CUTOFF_14, CUTOFF_14))
w(f"_last 14 days: {0 if is_err(tps) else len(tps)} rows (capped at {ROW_CAP})_")
table(tps)
w()
w("### 4.5 — `spotgamma_tape_reports` (structured tape report table)")
schema_block("spotgamma_tape_reports")
w(f"_row count: {META['spotgamma_tape_reports']['n']}_ — "
  "NOTE: the structured report table is **empty**; the 15-min tape watcher currently writes flat "
  "option prints to `sg_tape` instead. This is a key integration-design data point.")
w()
w("### 4.6 — `sg_levels` (deterministic SG levels table, migration 034)")
schema_block("sg_levels")
w(f"_row count: {META['sg_levels']['n']}_ — NOTE: **empty**. The deterministic levels table created "
  "for the SG re-introduction is not being populated.")
w()
w("### 4.7 — SpotGamma in `corpus_documents`")
sgcorp = q("""SELECT source, count(*) n, max(source_date) latest FROM corpus_documents
              WHERE source ILIKE '%spotgamma%' OR source ILIKE '%flowpatrol%' OR source ILIKE '%sg_%'
              GROUP BY source ORDER BY n DESC""")
table(sgcorp, ["source","n","latest"])
w()
w("### 4.8 — `spotgamma_client.py` (the dormant live client)")
w("> Full source is in Section 1.2. It exposes `latest(ticker)` which the decision-engine stub no "
  "longer calls. Re-enabling SG in the live path means re-wiring this.")
w()
w("### 4.9 — The architectural question")
w("> **How should SG data flow back into trade decisions without the cost spiral that triggered the "
  "rollback?** Previously SG context was loaded into *every* alert's LLM decision prompt, which (with "
  "the per-ticker Anthropic calls) ran the API bill toward ~$400/mo. The capture pipelines "
  "(premarket/postclose sweeps + 15-min tape watch) already populate Postgres cheaply via the "
  "scrape SKILLs → `/api/scrape_ingest`. The design problem is the *read* side: turning stored SG "
  "rows into a small number of **deterministic, actionable signals** (HIRO zero-cross, compression "
  "spike, gamma flip, skew widening, dealer hedge-flow shift, FlowPatrol narrative) that gate or "
  "annotate alerts — without putting raw SG into every LLM call.")
w()
w("---")
w()

# ════════════════ SECTION 5 — RECENT ACTIVITY ════════════════
w("## SECTION 5 — Recent alerts + trades activity")
w()
w(f"### 5.1 — Alerts fired, last 7 days (since {CUTOFF_7})")
al = q(f"""SELECT fired_at, ticker, suggested_action, range_zone, boundary, framework_alignment,
                  quad_regime, monthly_quad, quarterly_quad, price_at_fire,
                  left(recommendation_text,200) AS rec
           FROM alerts_fired WHERE fired_at >= %s ORDER BY fired_at DESC LIMIT {ROW_CAP}""",(CUTOFF_7,))
w(f"_{0 if is_err(al) else len(al)} rows_")
table(al, ["fired_at","ticker","suggested_action","range_zone","boundary","framework_alignment",
           "quad_regime","monthly_quad","quarterly_quad","price_at_fire","rec"])
w()
w(f"### 5.2 — `actions_log`, last 30 days (since {CUTOFF_30}; $ amounts/balances omitted)")
act = q(f"""SELECT run_date, account_name, action, normalized_symbol AS ticker, qty, price,
                   type, source, settlement_date
            FROM actions_log WHERE run_date >= %s ORDER BY run_date DESC, ticker LIMIT {ROW_CAP}""",(CUTOFF_30,))
w(f"_{0 if is_err(act) else len(act)} rows_")
table(act, ["run_date","account_name","action","ticker","qty","price","type","source","settlement_date"])
w()
w("### 5.3 — Current `positions_snapshot` (latest; $ values omitted)")
pdate = q("SELECT max(snapshot_date) d FROM positions_snapshot")
pdv = pdate[0]["d"] if not is_err(pdate) else None
w(f"_snapshot_date = {pdv}_")
pos = q("""SELECT account_name, normalized_symbol AS symbol, quantity, last_price, source
           FROM positions_snapshot
           WHERE snapshot_date=(SELECT max(snapshot_date) FROM positions_snapshot)
           ORDER BY account_name, symbol""")
table(pos, ["account_name","symbol","quantity","last_price","source"])
w()
w("---")
w()

# ════════════════ SECTION 6 — HEDGEYE STATE PER PRODUCT ════════════════
w("## SECTION 6 — Hedgeye state per product")
w()
hedgeye_tables = [t for t in tnames if t.startswith("hedgeye_")]
w("### 6.1 — Latest publication date + staleness per product")
w("| table | date column | latest | days stale | flag |")
w("| --- | --- | --- | --- | --- |")
stale_detail = []
for t in hedgeye_tables:
    dc = META[t]["dc"]; recent = META[t]["recent"]
    days = ""; flag = ""
    if recent and dc:
        try:
            d = datetime.fromisoformat(str(recent)[:10]).date()
            days = (TODAY - d).days
            flag = "FRESH" if days <= 3 else ("AGING" if days <= 14 else "STALE")
        except Exception:
            pass
    if META[t]["n"] == 0:
        flag = "EMPTY"
    w(f"| `{t}` | {dc or '—'} | {recent or '—'} | {days} | {flag} |")
    stale_detail.append((t, dc))
w()
w("### 6.2 — 3 most-recent rows per Hedgeye product")
w()
for t, dc in stale_detail:
    n = META[t]["n"]
    w(f"#### `{t}` ({n} rows)")
    if n == 0:
        w("_(empty)_"); w(); continue
    disp = [c for c,_ in META[t]["cols"] if c.lower() not in BALANCE_COLS]
    sel = ", ".join(f'"{c}"' for c in disp)
    order = f' ORDER BY "{dc}" DESC' if dc else ""
    rows = q(f'SELECT {sel} FROM "{t}"{order} LIMIT 3')
    table(rows)
    w()
w("---")
w()

# ════════════════ SECTION 7 — MEMORY ════════════════
w("## SECTION 7 — Memory / design decisions (verbatim)")
w()
MEM_DIR = (r"C:\Users\bogac\AppData\Roaming\Claude\local-agent-mode-sessions"
           r"\e25dbe6b-a1f6-4500-84f2-c964ee155cea"
           r"\d9552e59-f2a7-4327-a6a6-a9ce7033f0c8\agent\memory")
if os.path.isdir(MEM_DIR):
    idx = os.path.join(MEM_DIR, "MEMORY.md")
    files = sorted(glob.glob(os.path.join(MEM_DIR, "*.md")))
    ordered_mem = ([idx] if os.path.exists(idx) else []) + \
                  [f for f in files if os.path.basename(f) != "MEMORY.md"]
    for mp in ordered_mem:
        name = os.path.basename(mp)
        try:
            content = open(mp, encoding="utf-8", errors="replace").read()
        except Exception as e:
            ERRORS.append(f"memory {name}: {e}"); content = f"(could not read: {e})"
        w(f"### `{name}`")
        w("```markdown")
        W.write(redact(content))
        if not content.endswith("\n"): w("")
        w("```")
        w()
else:
    ERRORS.append(f"memory dir not found: {MEM_DIR}")
    w(f"_memory directory not found: {MEM_DIR}_")
w("---")
w()

# ════════════════ SECTION 8 — OPEN ISSUES ════════════════
w("## SECTION 8 — Open issues + known bugs")
w()
ISSUES = [
 ("Quad parser reads tilt-FROM instead of tilt-TO",
  "Historically the quad extractor read the *origin* quad of a tilt instead of the *destination*. "
  "Commit `0f90f2d` claims a fix (reads tilt-TO); verify against `hedgeye_research_notes."
  "tilt_target_quads` + `tools/quad_regime.py`. If any path still reads tilt-FROM, regime routing "
  "is inverted."),
 ("Regime updater ignores Quad 4 signals from research_notes",
  "The regime updater historically acted only on certain products and ignored Quad-4 tilt signals "
  "arriving via `hedgeye_research_notes`. Commit `0f90f2d` claims 'regime updater acts on research "
  "notes' — confirm Quad 4 is no longer dropped."),
 ("research_notes never reached corpus_documents (RAG-blind)",
  "`hedgeye_research_notes` rows were parsed but never wired into `corpus_documents`, so RAG/ML "
  "could not see them. Commit `0f90f2d` claims a wire-up; verify corpus_documents now has a "
  "research-notes source (see Section 2.1 per-source counts)."),
 ("hedgeye_quad_nowcast empty (probability parser dead)",
  "`hedgeye_quad_nowcast` has 0 rows — the GIP/quad-probability nowcast parser is not producing "
  "output. Quad probabilities are therefore unavailable to the regime logic."),
 ("PDF deck downloader",
  "Research-note PDFs (full_report_url in hedgeye_research_notes) were historically never fetched. "
  "`tools/download_research_pdf.py` now exists; confirm it runs and populates decks."),
 ("HedgeyeBotEventAlerts scheduled task LastTaskResult",
  "The Windows Scheduled Task for event alerts has had LastTaskResult issues historically "
  "(launcher: event_alerts_launcher.ps1, installer: install_event_alerts_task.py). Confirm current "
  "state on the workstation."),
 ("IBKR gateway broken (Railway build failed)",
  "The IBKR market-data gateway (deploy/ibkr-gateway) has had a build-failed status on Railway; "
  "PRICE_FEED stays on yfinance/alpaca until it is healthy."),
 ("spotgamma_tape_reports + sg_levels empty",
  "The structured SG tape-report table and deterministic sg_levels table are both empty (Sections "
  "4.5/4.6); SG capture currently only lands flat prints in sg_tape and snapshots in "
  "spotgamma_snapshots."),
]
for i,(t,d) in enumerate(ISSUES,1):
    w(f"{i}. **{t}.** {d}")
w()
w("### 8.1 — In-flight / uncommitted work")
w("```text")
w(sh("git status --short"))
w("```")
w("Untracked `check_*.py`, `inventory.py`, `peek_state.py` are scratch diagnostics. "
  "`migrations/036_quad_trusted.sql` is an untracked migration (quad trusted-source work).")
w()
w("### 8.2 — Export-generation errors / caps logged this run")
if ERRORS:
    for e in ERRORS:
        w(f"- {e}")
else:
    w("_(none — all sections generated cleanly)_")
w()
w("---")
w()

# ════════════════ SECTION 9 — FRAMEWORK + CONSTRAINTS ════════════════
w("## SECTION 9 — Operator's framework + constraints")
w()
w("- **Trading style:** Style B sizing with a **$1K position ceiling** (small, even sizing — the bot "
  "recommends, the operator sizes).")
w("- **Cost discipline:** ~**$400/mo** hard upper bound while pre-revenue; current target ~**$1–5/day** "
  "of Anthropic spend. The SG rollback was a *cost* decision — loading SG into every LLM alert blew "
  "the budget. See `migrations/033_llm_calls.sql` + the `llm_calls` cost ledger (one row per call, "
  "est USD stamped at insert).")
w("- **ADHD-friendly collaboration:** one copy-box answers, decisions-not-clicks, no addiction "
  "metaphors, verify-before-alarm, proactive reporting. (See Section 7 feedback memories.)")
w("- **Hedgeye = north star:** Hedgeye's published TREND direction is authoritative; MFR/SG/T1A are "
  "supplementary context that may refine timing/sizing but must never flip the side.")
w()
w("---")
w()

# ════════════════ SECTION 10 — THE ASK ════════════════
w("## SECTION 10 — What the operator is asking the second-opinion AI to help with")
w()
w("> \"Help me design a SpotGamma data integration architecture that:")
w("> 1. Brings SG signals back into trade decisions in a structured way (not just stored in DB unused)")
w("> 2. Respects the cost constraint (~$1-5/day API budget)")
w("> 3. Hedgeye remains north star — SG is supplementary")
w("> 4. Avoids the failure mode where SG context was loaded into every alert decision and burned $400/mo")
w("> 5. Works with the existing capture pipelines (premarket/postclose sweeps + 15-min tape watch)")
w("> 6. Provides specific actionable signals (HIRO crosses zero, compression spike, key gamma flip, "
  "vol skew widening, dealer hedge flow shift, FlowPatrol intraday narrative) — not just raw snapshots")
w("> ")
w("> What would you recommend?\"")
w()
w(f"_End of export — generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
  "Read-only; secrets redacted; $ balances omitted._")

# ════════════════ WRITE + COPY + REPORT ════════════════
data = W.getvalue()
os.makedirs(os.path.dirname(OUT_PRIMARY), exist_ok=True)
with open(OUT_PRIMARY, "w", encoding="utf-8") as fh:
    fh.write(data)
landed = [OUT_PRIMARY]
for dst in OUT_COPIES:
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(OUT_PRIMARY, dst)
        landed.append(dst)
    except Exception as e:
        ERRORS.append(f"copy to {dst}: {e}")

size = os.path.getsize(OUT_PRIMARY)
chars = len(data)
lines = data.count("\n")+1

# fence-aware top-level section detection
ls = data.splitlines()
secs=[]; cur_sec=None; start=0; in_fence=False
for i,line in enumerate(ls):
    s = line.lstrip()
    if s.startswith("```"):
        in_fence = not in_fence
        continue
    if not in_fence and (line.startswith("## ") or line.startswith("# Hedgeye")):
        if cur_sec is not None: secs.append((cur_sec, i-start))
        cur_sec=line.lstrip("# ").strip(); start=i
if cur_sec is not None: secs.append((cur_sec, len(ls)-start))

out = []
out.append("=== EXPORT COMPLETE ===")
for p in landed: out.append("LANDED " + p)
out.append(f"BYTES {size}  MB {round(size/1048576,2)}  CHARS {chars}  LINES {lines}  "
           f"TOKEN_EST {chars//4}  FITS_1M {'YES' if chars//4 <= 1000000 else 'NO'}")
out.append("=== SECTION LINE COUNTS (top-level, fence-aware) ===")
for name,ln in secs: out.append(f"{ln:>6}  {name[:80]}")
out.append("=== ERRORS / CAPS ===")
out += (["ERR " + e for e in ERRORS] if ERRORS else ["(none)"])
sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
conn.close()
