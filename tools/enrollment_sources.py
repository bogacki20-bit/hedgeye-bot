"""Registry of MFR-enrollable sources for the nightly to-add batch (tools/enrollment).

Adding a future source = ONE line here (or a small adapter implementing
names_added_on(day) -> set[str] if its schema doesn't fit TableSource). The nightly
job never changes.
"""
from tools.enrollment import TableSource, BookSource

# Tickers to EXCLUDE from the enrollment backlog + DARK footer — can't/shouldn't be
# activated in MFR. Curated 2026-07-05 from the 61-name backlog triage.
KNOWN_UNCOVERABLE = {
    "RPBPF",                        # original seed (OTC, un-activatable)
    # Foreign local listings — not tradeable from the US
    "005930.KS", "1913.HK", "2331.HK", "2408.TW", "2513.HK", "285A.T", "3998.HK",
    "ABX.TO", "ADS.DE", "ATD.TO", "ATZ.TO", "CFR.SW", "DTG.DE", "EL.PA", "GRGD.TO",
    "HAYPP.ST", "JD.L", "LISN.SW", "LTMC.MI", "PUM.DE", "RI.PA", "RMS.PA", "RPI.L",
    "VOLV-B.ST",
    "EXPN",                         # Experian London line (US ADR EXPGY = future alias work)
    # US but untradeable / defunct
    "NKLA",                         # Nikola — Chapter 11, delisted
    "FDRXX",                        # Fidelity Govt Cash Reserves — money-market fund, no range
    "MICC", "OFRM", "YSWY",         # unresolved / stale-or-unrecognized tickers
    # Verified NOT in MFR's ticker universe — live enrollment session
    # 2026-07-12 (Claude-in-Chrome, operator-supervised): searched by
    # symbol AND company name, no US listing found.
    "ATUS",                         # Altice USA — absent from MFR search
    "FI",                           # Fiserv — only foreign listings (FISV.XMEX etc.)
    "FYBR",                         # Frontier Comms — delisted (Verizon acquisition)
    "GDNSF",                        # OTC — zero search results
    "PANDY",                        # Pandora ADR (OTC) — zero search results
    "ZI",                           # ZoomInfo -> GTM rename; neither in MFR
    "SETH",                         # ProShares UltraShort Ether — crypto ETFs absent
                                    #   from MFR equities; ETHUSD covers the trend
                                    #   via the wrapper link
    # ── Scraped words, not selections (2026-08-01) ──────────────────────────
    # parser_signal_strength.py:62 is TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
    # run over email text with no filter, so prose and headers become tickers.
    # These reached the live backlog on 2026-08-01 and would have been pasted
    # into MFR -> Activate Assets by an operator following the instruction at the
    # bottom of the message.
    #
    # THIS IS A BAND-AID. The cure is scoping that regex to the Add/Remove
    # section. Until then each new email can mint fresh ones.
    "BEEN", "FROM", "LIST", "SIGNAL",   # not tickers at all — safe to block forever
    # HAS and JUST were here 2026-08-01 to 08-02 and have been REMOVED.
    #
    # Real securities: Hasbro (NASDAQ) and the Goldman Sachs JUST U.S. Large Cap
    # Equity ETF (NYSE Arca). They entered the universe only because
    # parser_research_common.side_blocks scraped the words "has" and "just" out
    # of prose in ONE Keith's email on 7/29 16:13. That parser is bounded now
    # (a4fbd0c), the rows were deleted from hedgeye_keiths_signals and
    # hedgeye_ticker_inventory, and hedgeye_ticker_history keeps the audit trail.
    #
    # With the source fixed, the only way either returns is Hedgeye actually
    # publishing them — and blocking them here would silently suppress exactly
    # that. Blocking a real ticker forever, to defend against a bug that no
    # longer exists, is the same failure as the stopword list this whole thread
    # was about. Do not re-add. If they reappear, that is signal.
}

# Crypto names PARKED for the BTC Quant source — NOT uncoverable, just routed elsewhere.
# btcquant is now live on the existing hedgeye_crypto_quant feed (source_registry).
# Data-backed routing (2026-07-05, from 184 rows of trend sentiment):
#   PROMOTED (now live under btcquant, removed here): BTCUSD, ETHUSD, SOLUSD, XRPUSD,
#     AVAXUSD — all 5 majors carry trend designations (the parser splits slash-lists
#     like "COIN/SOL/AVAX/XRP=NEUTRAL", so ETH/SOL/AVAX are covered).
#   STILL PARKED: RUNEUSD, TRXUSD — never designated by the product; no live source yet.
# No name is promoted without a signal backing it.
# NB (2026-07-26): btcquant is TREND-ONLY — hedgeye_crypto_quant carries no range levels,
# so a btcquant promotion never supplied a range. MFR ranges these under CRYPTO.*, and
# XRPUSD/AVAXUSD/RUNEUSD/TRXUSD are now force-fanned-out (mfr_client._CRYPTO_FORCE_FANOUT)
# so ranges flow from MFR while trend continues to come from btcquant.
PARKED_FOR_SOURCE = {
    "RUNEUSD", "TRXUSD",
    # 2026-08-02: the five promoted majors are PERMANENT backlog residents and
    # always will be. The posmon seed and btcquant carry them as BTCUSD/ETHUSD/
    # SOLUSD/XRPUSD/AVAXUSD, while MFR activates and serves them under the bare
    # symbol (BTC, ETH, SOL, XRP, AVAX) plus the CRYPTO.* forms — so the *USD
    # name can never appear in list_watchlist() and the diff can never clear it.
    # Not uncoverable, not missing: a NAMING mismatch. Ranges already arrive via
    # mfr_client._CRYPTO_FORCE_FANOUT and the alias table in fetch_raw.
    # They showed up in the 8/1 backlog flagged 'persisted >=3 wks' for exactly
    # this reason — the weeks-seen counter was measuring an unfixable diff.
    # Proper fix is a wrapper/alias mapping on the ENROLLMENT side; parking them
    # stops the nag until that exists.
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "AVAXUSD",
}

REGISTRY = [
    # Signal Strength — live today (ss_roster_history exists).
    TableSource("signal_strength", "ss_roster_history",
                date_col="added_on", where="removed_on IS NULL"),

    # Fidelity book holdings — so names I actually own feed the enrollment backlog
    # even when Hedgeye never tagged them (sector ETFs, etc.). Cash excluded.
    BookSource(),

    # Future sources — uncomment + point at each roster table once it exists.
    # No change to tools/enrollment is needed when you add one.
    # TableSource("retail_go",     "hedgeye_retail_roster",  where="removed_on IS NULL"),
    # TableSource("financials_go", "hedgeye_fin_roster",     where="removed_on IS NULL"),
    # TableSource("etf_pro_plus",  "hedgeye_etfpro_roster",  where="removed_on IS NULL"),
]
