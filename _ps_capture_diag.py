"""Audit Portfolio Solutions capture for ML-training readiness.

Read-only — checks corpus ingest, typed table coverage, commentary
actions, delta detection, and the gap between raw text and structured
trade events.
"""
import db_pg

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        print("=" * 78)
        print("1) corpus_documents PS capture")
        print("=" * 78)
        cur.execute("""
            SELECT MAX(source_date) AS most_recent,
                   COUNT(DISTINCT source_date) AS distinct_pub_days_last_90d,
                   COUNT(*) AS total_rows
              FROM corpus_documents
             WHERE source IN ('portfolio_solutions', 'hedgeye_portfolio_solutions')
               AND source_date >= CURRENT_DATE - interval '90 days'
        """)
        r = cur.fetchone()
        print(f"  most_recent_ps_publication:  {r[0]}")
        print(f"  distinct_pub_days_last_90d:  {r[1]}")
        print(f"  total_ps_rows_last_90d:      {r[2]}")

        cur.execute("""
            SELECT source, COUNT(*), MIN(source_date), MAX(source_date)
              FROM corpus_documents
             WHERE source ILIKE '%portfolio%' OR source ILIKE '%ps%'
             GROUP BY source ORDER BY COUNT(*) DESC
        """)
        print("\n  by source label (anything portfolio/PS-flavored):")
        for src, cnt, mn, mx in cur.fetchall():
            print(f"    {src:35} rows={cnt:>4}  range=[{mn} .. {mx}]")

        print()
        print("=" * 78)
        print("2) Structured PS history — does a dedicated table exist?")
        print("=" * 78)
        cur.execute("""
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND (table_name ILIKE '%portfolio_solution%'
                    OR table_name ILIKE '%ps_history%'
                    OR table_name ILIKE '%hedgeye_portfolio%')
             ORDER BY table_name
        """)
        tables = [r[0] for r in cur.fetchall()]
        print(f"  matching tables: {tables}")

        for tbl in tables:
            cur.execute("""
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema='public' AND table_name=%s
                 ORDER BY ordinal_position
            """, (tbl,))
            cols = cur.fetchall()
            print(f"\n  {tbl} columns:")
            for c in cols: print(f"    {c[0]:25} {c[1]}")

            # Row counts + recency
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            n = cur.fetchone()[0]
            print(f"  {tbl} row count: {n}")
            # Most recent date column varies — try common names
            for date_col in ("snapshot_date", "action_date", "captured_at",
                              "parsed_at", "created_at"):
                try:
                    cur.execute(f"SELECT MAX({date_col}) FROM {tbl}")
                    mx = cur.fetchone()[0]
                    print(f"  {tbl} MAX({date_col}) = {mx}")
                    break
                except Exception:
                    conn.rollback()
                    continue

        print()
        print("=" * 78)
        print("3) hedgeye_portfolio_solutions (the daily rank snapshot)")
        print("=" * 78)
        cur.execute("""
            SELECT snapshot_date, COUNT(*),
                   COUNT(*) FILTER (WHERE rank_change_1w IS NOT NULL) AS has_1w,
                   COUNT(*) FILTER (WHERE rank_change_1m IS NOT NULL) AS has_1m
              FROM hedgeye_portfolio_solutions
             WHERE snapshot_date >= CURRENT_DATE - interval '30 days'
             GROUP BY snapshot_date
             ORDER BY snapshot_date DESC LIMIT 12
        """)
        rows = cur.fetchall()
        for r in rows:
            print(f"  {r[0]} positions={r[1]} with_1w_change={r[2]} with_1m_change={r[3]}")

        cur.execute("""
            SELECT COUNT(DISTINCT snapshot_date)
              FROM hedgeye_portfolio_solutions
             WHERE snapshot_date >= CURRENT_DATE - interval '90 days'
        """)
        print(f"\n  distinct snapshot_dates in last 90d: {cur.fetchone()[0]}")

        print()
        print("=" * 78)
        print("4) hedgeye_portfolio_actions (Keith's literal trade actions)")
        print("=" * 78)
        cur.execute("""
            SELECT COUNT(*), MIN(action_date), MAX(action_date)
              FROM hedgeye_portfolio_actions
        """)
        cnt, mn, mx = cur.fetchone()
        print(f"  total actions: {cnt}  date range: [{mn} .. {mx}]")

        cur.execute("""
            SELECT action, COUNT(*),
                   COUNT(*) FILTER (WHERE bps IS NOT NULL) AS with_bps
              FROM hedgeye_portfolio_actions
             GROUP BY action ORDER BY COUNT(*) DESC
        """)
        print("\n  by action type (and whether bps was parsed):")
        for a, n, b in cur.fetchall():
            print(f"    {a:12} n={n:>4} with_bps={b}")

        cur.execute("""
            SELECT action_date, ticker, action, bps,
                   LEFT(COALESCE(commentary_raw,''), 120) AS commentary
              FROM hedgeye_portfolio_actions
             WHERE action_date >= CURRENT_DATE - interval '30 days'
             ORDER BY action_date DESC, ticker LIMIT 30
        """)
        print("\n  most recent 30 actions:")
        for r in cur.fetchall():
            print(f"    {r[0]} {r[1]:6} {r[2]:10} bps={r[3]}  {r[4]}")

        print()
        print("=" * 78)
        print("5) PS delta detection — have any ps_delta alerts EVER fired?")
        print("=" * 78)
        cur.execute("""
            SELECT boundary, COUNT(*), MIN(signal_date), MAX(signal_date)
              FROM alerts_fired
             WHERE boundary IN ('ps_delta','portfolio_solutions_delta',
                                'ps_add','ps_trim','ps_exit')
             GROUP BY boundary
        """)
        rows = cur.fetchall()
        if not rows:
            print("  *** ZERO PS delta alerts ever fired ***")
        else:
            for r in rows:
                print(f"  boundary={r[0]:25} count={r[1]} range=[{r[2]} .. {r[3]}]")

        # Same check across all delta boundaries to spot whether any deltas
        # have fired at all
        cur.execute("""
            SELECT boundary, COUNT(*), MAX(signal_date)
              FROM alerts_fired
             WHERE boundary ILIKE '%delta%'
                OR boundary ILIKE '%ps_%'
                OR boundary ILIKE '%ss_%'
                OR boundary ILIKE '%etf_pro_%'
                OR boundary ILIKE '%ii_%'
             GROUP BY boundary ORDER BY COUNT(*) DESC
        """)
        print("\n  any delta-flavored boundaries in alerts_fired:")
        for r in cur.fetchall():
            print(f"    {r[0]:25} n={r[1]} latest={r[2]}")

        print()
        print("=" * 78)
        print("6) Quality of latest PS corpus row — metadata vs raw text")
        print("=" * 78)
        cur.execute("""
            SELECT id, source, source_date, title, metadata,
                   LEFT(COALESCE(full_text,''), 800) AS preview
              FROM corpus_documents
             WHERE source IN ('portfolio_solutions','hedgeye_portfolio_solutions')
             ORDER BY source_date DESC, id DESC LIMIT 1
        """)
        r = cur.fetchone()
        if not r:
            print("  (no PS corpus rows)")
        else:
            print(f"  id={r[0]} source={r[1]} source_date={r[2]}")
            print(f"  title: {r[3]}")
            md = r[4] or {}
            print(f"  metadata keys: {list(md.keys()) if isinstance(md,dict) else type(md).__name__}")
            if isinstance(md, dict):
                # Try to extract any structured fields
                interesting = ('tickers','positions','actions','rank','adds',
                               'trims','exits','rank_changes','side')
                for k in interesting:
                    if k in md:
                        v = md[k]
                        if isinstance(v, list):
                            print(f"    metadata.{k}: list len={len(v)}  preview={v[:8]}")
                        else:
                            print(f"    metadata.{k}: {v}")
            print(f"\n  full_text preview (first 800 chars):")
            for line in (r[5] or "").splitlines()[:20]:
                print(f"    {line[:120]}")

        print()
        print("=" * 78)
        print("7) Does ML have a queryable 'Keith made a trade' event view?")
        print("=" * 78)
        # The closest equivalent to a structured trade event log:
        #   - hedgeye_portfolio_actions (parser-extracted from commentary)
        #   - hedgeye_portfolio_solutions diffs across snapshots
        # Check whether either is currently queryable as a trade-event stream.
        cur.execute("""
            SELECT EXISTS(SELECT 1 FROM information_schema.tables
                          WHERE table_schema='public'
                            AND table_name='hedgeye_portfolio_actions')
        """)
        hpa_exists = cur.fetchone()[0]
        print(f"  hedgeye_portfolio_actions table exists: {hpa_exists}")
        if hpa_exists:
            cur.execute("""
                SELECT COUNT(DISTINCT action_date)
                  FROM hedgeye_portfolio_actions
                 WHERE action_date >= CURRENT_DATE - interval '90 days'
            """)
            d = cur.fetchone()[0]
            print(f"  distinct action_dates with parsed actions (last 90d): {d}")

        # Check coverage gap: distinct PS snapshot dates that have a same-day
        # parsed action vs ones that don't (i.e. the parser missed the
        # commentary block).
        cur.execute("""
            WITH ps_days AS (
              SELECT DISTINCT snapshot_date::date AS d
                FROM hedgeye_portfolio_solutions
               WHERE snapshot_date >= CURRENT_DATE - interval '30 days'
            ),
            action_days AS (
              SELECT DISTINCT action_date::date AS d
                FROM hedgeye_portfolio_actions
               WHERE action_date >= CURRENT_DATE - interval '30 days'
            )
            SELECT (SELECT COUNT(*) FROM ps_days) AS ps_pub_days,
                   (SELECT COUNT(*) FROM action_days) AS action_days,
                   (SELECT COUNT(*) FROM ps_days WHERE d NOT IN (SELECT d FROM action_days)) AS no_action_days
        """)
        ps_d, act_d, gap = cur.fetchone()
        print(f"\n  Last 30d coverage:")
        print(f"    PS publications:               {ps_d}")
        print(f"    Days with parsed actions:      {act_d}")
        print(f"    PS days with NO parsed action: {gap}")
