"""Diagnose the BITCOIN bearish-vs-BUY contradiction.

Read-only — does not modify any data. Pulls every corpus_documents row
mentioning BTC/BITCOIN in the last 14 days plus every typed-table row
across all Hedgeye products, then summarizes by source/date/side.
"""
import db_pg

LOOKBACK_DAYS = 14

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        print("=" * 78)
        print("1) corpus_documents — BITCOIN/BTC mentions (last %d days)" % LOOKBACK_DAYS)
        print("=" * 78)
        cur.execute("""
            SELECT source, source_date, title,
                   LEFT(COALESCE(full_text,''), 220) AS snippet,
                   metadata
              FROM corpus_documents
             WHERE (
                   full_text ILIKE '%bitcoin%'
                OR full_text ILIKE '%BTC%'
                OR metadata::text ILIKE '%BITCOIN%'
                OR metadata::text ILIKE '%BTC%'
             )
               AND source_date >= CURRENT_DATE - interval '14 days'
             ORDER BY source_date DESC, source
        """)
        rows = cur.fetchall()
        if not rows:
            print("  (no rows)")
        print(f"  total rows: {len(rows)}")
        for src, dt, title, snippet, md in rows:
            print()
            print(f"  source={src}  date={dt}")
            if title:
                print(f"    title:    {title[:120]}")
            if md and isinstance(md, dict):
                tickers = md.get("tickers")
                side    = md.get("side") or md.get("position") or md.get("bias")
                sentiment = md.get("sentiment")
                if tickers and ("BITCOIN" in tickers or "BTC" in tickers):
                    print(f"    tickers:  BTC/BITCOIN explicitly tagged")
                if side:      print(f"    side:     {side}")
                if sentiment: print(f"    sentiment: {sentiment}")
            # Highlight bearish keywords in the snippet
            low = (snippet or "").lower()
            bear_kw = [k for k in ("bearish","short","trim","exit","sell",
                                    "fade","top of range","breakdown","capitulate",
                                    "lower","down")
                       if k in low]
            bull_kw = [k for k in ("bullish","long","buy","add","scale-in",
                                    "bottom of range","breakout","higher","continuation")
                       if k in low]
            print(f"    bear-kw:  {bear_kw}")
            print(f"    bull-kw:  {bull_kw}")
            print(f"    snippet:  {(snippet or '').replace(chr(10),' ')[:220]}")

        print()
        print("=" * 78)
        print("2) Typed product tables — BITCOIN/BTC presence")
        print("=" * 78)
        for table, date_col in (
            ("hedgeye_etf_pro_ranges",      "week_of"),
            ("hedgeye_signal_strength",     "snapshot_date"),
            ("hedgeye_portfolio_solutions", "snapshot_date"),
            ("hedgeye_investing_ideas",     "snapshot_date"),
            ("hedgeye_risk_ranges",         "signal_date"),
        ):
            for tk in ("BITCOIN", "BTC"):
                try:
                    if table == "hedgeye_etf_pro_ranges":
                        cur.execute(
                            f"SELECT ticker, {date_col}, description, range_low, range_high "
                            f"FROM {table} WHERE ticker=%s "
                            f"ORDER BY {date_col} DESC LIMIT 5",
                            (tk,))
                    elif table == "hedgeye_risk_ranges":
                        cur.execute(
                            f"SELECT ticker, {date_col}, trend, buy_trade, sell_trade "
                            f"FROM {table} WHERE ticker=%s "
                            f"ORDER BY {date_col} DESC LIMIT 5",
                            (tk,))
                    else:
                        cur.execute(
                            f"SELECT ticker, {date_col} "
                            f"FROM {table} WHERE ticker=%s "
                            f"ORDER BY {date_col} DESC LIMIT 5",
                            (tk,))
                    rows = cur.fetchall()
                    if rows:
                        print(f"\n  {table} [{tk}]:")
                        for r in rows:
                            print(f"    {r}")
                except Exception as e:
                    pass  # Table missing on this env

        print()
        print("=" * 78)
        print("3) mfr_snapshots BITCOIN — most recent")
        print("=" * 78)
        cur.execute("""
            SELECT snapshot_date, price, range_low, range_high,
                   trend_signal, momentum_signal, hurst, daily_pct_change
              FROM mfr_snapshots
             WHERE ticker='BITCOIN'
             ORDER BY snapshot_date DESC, fetched_at DESC
             LIMIT 3
        """)
        for r in cur.fetchall():
            print(f"  {r}")

        print()
        print("=" * 78)
        print("4) bot side-tagging — what bucket(s) does BITCOIN land in?")
        print("=" * 78)
        from tools import active_slice as al
        al.invalidate_cache()
        bd = al.source_breakdown()
        for k, lst in bd.items():
            if "BITCOIN" in lst:
                print(f"  bucket={k}   contributes_to_polling={k in al._POLLING_INCLUDED_KEYS}")
        print(f"  source_flags_for(BITCOIN) = {al.source_flags_for('BITCOIN')}")
