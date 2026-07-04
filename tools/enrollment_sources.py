"""Registry of MFR-enrollable sources for the nightly to-add batch (tools/enrollment).

Adding a future source = ONE line here (or a small adapter implementing
names_added_on(day) -> set[str] if its schema doesn't fit TableSource). The nightly
job never changes.
"""
from tools.enrollment import TableSource, BookSource

# Tickers MFR cannot activate (OTC/foreign it rejects) — excluded so they don't recur
# in the nightly to-add list. Seed: RPBPF (confirmed un-activatable). Add more as found.
KNOWN_UNCOVERABLE = {"RPBPF"}

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
