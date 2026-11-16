"""tools/db_backup.py - full backup of the bot's Railway Postgres to Kris's private Google Drive (Kris 11/15/2026:
"everything should always be pushed in case something happens ... it is recoverable"; code is on GitHub, this covers
the data).

    railway run py -m tools.db_backup                 # back up now (capture desk, evening block), then verify
    railway run py -m tools.db_backup --verify [DIR]  # re-check a backup: every file opens, row counts match
    py -m tools.db_backup --list
    py -m tools.db_backup --restore DIR --dsn postgresql://...   # load into an EMPTY database (schema + data)

What a backup is (one folder per run, G:\\My Drive\\Hedgeye Backups\\db_<YYYYMMDD_HHMM>\\):
  schema.json      sequences, tables (columns, types, defaults, NOT NULL), constraints, indexes, views (in order)
  data\\<table>.csv.gz   every table, CSV with header, taken in ONE read-only repeatable-read snapshot
  manifest.json    row count per table, sizes, server version, finished_at
No pg_dump needed (it isn't installed and would have to match server 18). Retention (only with --prune; off by
default because deleting files needs Kris's yes): the last 7 backups, plus the newest of each of the last 4 weeks.
Each backup is ~0.6 GB; Drive had 271 GB free on 11/15. The folder holds everything in the bot (book, account names, research):
keep "Hedgeye Backups" private and unshared, and never point an outside model at it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import shutil
import sys

DEFAULT_DIR = r"G:\My Drive\Hedgeye Backups"
KEEP_DAILY, KEEP_WEEKLY = 7, 4
csv.field_size_limit(2 ** 31 - 1)


def _connect(dsn: str | None = None):
    import psycopg2
    if not dsn:
        import db_pg
        db_pg._load_dotenv_fallback()
        dsn = os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn()
    return psycopg2.connect(dsn)


def _schema(cur) -> dict:
    cur.execute("SELECT sequencename, data_type::text, start_value, increment_by, last_value FROM pg_sequences "
                "WHERE schemaname = 'public'")
    seqs = [dict(zip(["name", "type", "start", "inc", "last"], r)) for r in cur.fetchall()]
    cur.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') ORDER BY c.relname")
    tables = []
    for (t,) in cur.fetchall():
        cur.execute("""SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                              pg_get_expr(d.adbin, d.adrelid)
                       FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                       WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
                    (f'public."{t}"',))
        cols = [dict(zip(["name", "type", "notnull", "default"], r)) for r in cur.fetchall()]
        cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
                    "AND contype IN ('p','u','c') ORDER BY contype DESC", (f'public."{t}"',))
        cons = [dict(zip(["name", "def"], r)) for r in cur.fetchall()]
        cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
                    "AND contype = 'f'", (f'public."{t}"',))
        fks = [dict(zip(["name", "def"], r)) for r in cur.fetchall()]
        cur.execute("SELECT indexdef FROM pg_indexes i WHERE schemaname = 'public' AND tablename = %s AND NOT EXISTS "
                    "(SELECT 1 FROM pg_constraint c WHERE c.conname = i.indexname)", (t,))
        idx = [r[0] for r in cur.fetchall()]
        tables.append({"name": t, "columns": cols, "constraints": cons, "foreign_keys": fks, "indexes": idx})
    # views in dependency order: a view after every view it reads
    cur.execute("""SELECT c.oid, c.relname, c.relkind, pg_get_viewdef(c.oid, true) FROM pg_class c
                   JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('v','m')""")
    views = {r[0]: {"name": r[1], "materialized": r[2] == "m", "def": r[3]} for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT v.oid, d.refobjid FROM pg_rewrite rw JOIN pg_class v ON v.oid = rw.ev_class
                   JOIN pg_depend d ON d.objid = rw.oid WHERE d.refobjid <> v.oid""")
    deps: dict = {}
    for v, ref in cur.fetchall():
        if v in views and ref in views:
            deps.setdefault(v, set()).add(ref)
    ordered, done = [], set()
    def visit(o, stack=()):
        if o in done or o in stack:
            return
        for r in deps.get(o, ()):
            visit(r, stack + (o,))
        done.add(o)
        ordered.append(views[o])
    for o in views:
        visit(o)
    return {"sequences": seqs, "tables": tables, "views": ordered}


def backup(dest_root: str = DEFAULT_DIR) -> str:
    from zoneinfo import ZoneInfo
    stamp = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d_%H%M")   # ET (railway run is UTC)
    final = os.path.join(dest_root, f"db_{stamp}")
    work = final + ".partial"
    os.makedirs(os.path.join(work, "data"), exist_ok=True)
    conn = _connect()
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
    cur = conn.cursor()
    cur.execute("SHOW server_version")
    version = cur.fetchone()[0]
    schema = _schema(cur)
    with open(os.path.join(work, "schema.json"), "w", encoding="utf-8") as fh:
        json.dump(schema, fh, default=str, indent=1)
    counts, sizes = {}, {}
    t0 = dt.datetime.now()
    for t in schema["tables"]:
        name = t["name"]
        cur.execute(f'SELECT count(*) FROM public."{name}"')
        counts[name] = cur.fetchone()[0]
        path = os.path.join(work, "data", f"{name}.csv.gz")
        with gzip.open(path, "wb", compresslevel=6) as gz:
            cur.copy_expert(f'COPY public."{name}" TO STDOUT WITH (FORMAT csv, HEADER)', gz)
        sizes[name] = os.path.getsize(path)
        print(f"  {name}: {counts[name]:,} rows, {sizes[name] / 1e6:,.1f} MB", flush=True)
    conn.rollback()
    conn.close()
    manifest = {"server_version": version, "started_at": t0.isoformat(), "finished_at": dt.datetime.now().isoformat(),
                "tables": len(counts), "rows": counts, "bytes": sizes, "total_mb": round(sum(sizes.values()) / 1e6, 1)}
    with open(os.path.join(work, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    os.replace(work, final)
    return final


def verify(folder: str) -> tuple[bool, list[str]]:
    """Every table file decompresses and holds exactly the manifest's row count (CSV-aware, so embedded newlines are fine)."""
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    bad = []
    for name, n in man["rows"].items():
        path = os.path.join(folder, "data", f"{name}.csv.gz")
        try:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
                got = sum(1 for _ in csv.reader(fh)) - 1
        except Exception as e:  # noqa: BLE001
            bad.append(f"{name}: unreadable ({e})")
            continue
        if got != n:
            bad.append(f"{name}: {got} rows in file vs {n} in manifest")
    return not bad, bad


def backups(root: str = DEFAULT_DIR) -> list[str]:
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, d) for d in os.listdir(root)
                  if d.startswith("db_") and not d.endswith(".partial") and os.path.exists(os.path.join(root, d, "manifest.json")))


def prune(root: str = DEFAULT_DIR) -> list[str]:
    """Keep the newest KEEP_DAILY backups plus the newest of each of the last KEEP_WEEKLY ISO weeks."""
    all_b = backups(root)
    keep = set(all_b[-KEEP_DAILY:])
    weeks: dict = {}
    for b in all_b:
        d = dt.datetime.strptime(os.path.basename(b)[3:16], "%Y%m%d_%H%M")
        weeks[d.isocalendar()[:2]] = b
    keep |= set(list(weeks.values())[-KEEP_WEEKLY:])
    gone = [b for b in all_b if b not in keep]
    for b in gone:
        shutil.rmtree(b, ignore_errors=True)
    for d in os.listdir(root):            # leftovers of a run that died midway
        if d.endswith(".partial"):
            p = os.path.join(root, d)
            if dt.datetime.now().timestamp() - os.path.getmtime(p) > 86400:
                shutil.rmtree(p, ignore_errors=True)
    return gone


def restore(folder: str, dsn: str | None, schema: str = "public", test: bool = False, max_mb: float | None = None) -> dict:
    """Load a backup into an EMPTY database: sequences, tables, data, sequence positions, constraints, indexes, views.
    test=True: build everything in a throwaway schema inside ONE transaction and roll it back (nothing is kept),
    loading only tables whose file is under max_mb, so a restore can be proven against the live server safely."""
    with open(os.path.join(folder, "schema.json"), encoding="utf-8") as fh:
        sch = json.load(fh)
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    conn = _connect(dsn)
    cur = conn.cursor()
    q = lambda n: f'{schema}."{n}"'
    commit = (lambda: None) if test else conn.commit
    res = {"tables": 0, "loaded": 0, "rows_checked": 0, "constraints": 0, "indexes": 0, "views": 0, "views_failed": []}
    try:
        if test:
            cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}, public")
        for s_ in sch["sequences"]:
            cur.execute(f'CREATE SEQUENCE IF NOT EXISTS {q(s_["name"])} AS {s_["type"]} START {s_["start"]} INCREMENT {s_["inc"]}')
        for t in sch["tables"]:
            cols = ", ".join(f'"{c["name"]}" {c["type"]}' +
                             (f' DEFAULT {c["default"].replace("public.", schema + ".")}' if c["default"] else "") +
                             (" NOT NULL" if c["notnull"] else "") for c in t["columns"])
            cur.execute(f'CREATE TABLE IF NOT EXISTS {q(t["name"])} ({cols})')
            res["tables"] += 1
        commit()
        for t in sch["tables"]:
            path = os.path.join(folder, "data", f'{t["name"]}.csv.gz')
            if max_mb is not None and os.path.getsize(path) > max_mb * 1e6:
                continue
            with gzip.open(path, "rb") as gz:
                cur.copy_expert(f'COPY {q(t["name"])} FROM STDIN WITH (FORMAT csv, HEADER)', gz)
            cur.execute(f'SELECT count(*) FROM {q(t["name"])}')
            n = cur.fetchone()[0]
            if n != man["rows"][t["name"]]:
                raise RuntimeError(f'{t["name"]}: loaded {n} rows, manifest {man["rows"][t["name"]]}')
            res["loaded"] += 1
            res["rows_checked"] += n
            commit()
            if not test:
                print("  loaded", t["name"], flush=True)
        for s_ in sch["sequences"]:
            if s_["last"] is not None:
                cur.execute("SELECT setval(%s, %s)", (q(s_["name"]), s_["last"]))
        for t in sch["tables"]:
            for c in t["constraints"]:
                cur.execute(f'ALTER TABLE {q(t["name"])} ADD CONSTRAINT "{c["name"]}" {c["def"]}')
                res["constraints"] += 1
            for ix in t["indexes"]:
                cur.execute(ix.replace(" ON public.", f" ON {schema}."))
                res["indexes"] += 1
        for t in sch["tables"]:
            for c in t["foreign_keys"]:
                cur.execute(f'ALTER TABLE {q(t["name"])} ADD CONSTRAINT "{c["name"]}" {c["def"].replace("public.", schema + ".")}' + (" NOT VALID" if test else ""))   # test skips big tables
        commit()
        pending = list(sch["views"])
        for _ in range(len(pending) + 1):          # retry until every view's dependencies exist
            left = []
            for v in pending:
                kind = "MATERIALIZED VIEW" if v["materialized"] else "VIEW"
                cur.execute("SAVEPOINT v")
                try:
                    cur.execute(f'CREATE {kind} {q(v["name"])} AS {v["def"].rstrip().rstrip(";")}' + (" WITH NO DATA" if v["materialized"] and test else ""))
                    cur.execute("RELEASE SAVEPOINT v")
                    res["views"] += 1
                except Exception as e:  # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT v")
                    v["_err"] = str(e).splitlines()[0][:160]
                    left.append(v)
            if not left:
                break
            pending = left
        res["views_failed"] = ([f'{v["name"]}: {v.get("_err", "")}' for v in pending]
                               if len(pending) and res["views"] < len(sch["views"]) else [])
        commit()
    finally:
        if test:
            conn.rollback()
        conn.close()
    return res


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=DEFAULT_DIR)
    ap.add_argument("--verify", nargs="?", const="latest")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument("--dsn")
    ap.add_argument("--prune", action="store_true",
                    help="delete backups beyond 7 nightly + 4 weekly (off by default: deleting files needs Kris's yes)")
    ap.add_argument("--test-restore", nargs="?", const="latest",
                    help="prove a backup restores: rebuild it in a throwaway schema on the live server, then roll back")
    a = ap.parse_args()
    if a.list:
        for b in backups(a.dest):
            with open(os.path.join(b, "manifest.json"), encoding="utf-8") as fh:
                m = json.load(fh)
            print(f"{os.path.basename(b)}  {m['tables']} tables  {sum(m['rows'].values()):,} rows  {m['total_mb']:,} MB")
        return 0
    if a.restore:
        if not a.dsn:
            print("--restore needs --dsn of an EMPTY target database")
            return 2
        print(restore(a.restore, a.dsn))
        return 0
    if a.test_restore:
        folder = backups(a.dest)[-1] if a.test_restore == "latest" else a.test_restore
        r = restore(folder, None, schema="restore_test_" + dt.datetime.now().strftime("%H%M%S"), test=True, max_mb=5)
        print(os.path.basename(folder), "test restore (rolled back):", r)
        return 0 if not r["views_failed"] else 1
    if a.verify:
        folder = backups(a.dest)[-1] if a.verify == "latest" else a.verify
        ok, bad = verify(folder)
        print(os.path.basename(folder), "OK" if ok else "BAD", *bad, sep="\n  ")
        return 0 if ok else 1
    folder = backup(a.dest)
    ok, bad = verify(folder)
    gone = prune(a.dest) if a.prune else []
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    print(f"backup {os.path.basename(folder)}: {m['tables']} tables, {sum(m['rows'].values()):,} rows, {m['total_mb']:,} MB, "
          f"verify {'OK' if ok else 'BAD'}" + (f"; pruned {len(gone)}" if gone else ""))
    for b in bad:
        print("  ", b)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(_cli())
