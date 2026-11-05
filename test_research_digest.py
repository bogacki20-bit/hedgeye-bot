"""research_digest: what counts as research, and what a digest must carry."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from research_digest import SCHEMA, is_reissue, is_research, product_of, same_report, validate  # noqa: E402

CALL = {"kind": "ticker", "name": "omf", "direction": "short", "basis": "inferred", "thesis": "x"}


def test_research_is_kept():
    for subject in ("October 2026 Senior Loan Officer Survey | Demand Weakens, Standards Hold",
                    "ISM | That Which Cannot Continue",
                    "HOUSING: Weekly Housing Market Tracker",
                    "The Big Theme | AI Bubble",
                    "Credit Card Asset Quality Update",
                    "EARLY LOOK: The #Quad4 Express"):
        assert is_research(subject), subject


def test_logistics_and_rosters_are_skipped():
    for subject in ("INVITE | TGT | Over Loved",
                    "HAPPENING NOW | MID-TERM",
                    "RISK  RANGE  SIGNALS: NOVEMBER 2, 2026",
                    "**Real-Time Alert: Cover Signal (risk mgt first): FSK -KM",
                    "Signal Strength Shorts: 70 Stocks (7 added, 4 removed)",
                    ""):
        assert not is_research(subject), subject


def test_same_title_is_a_reissue_only_on_the_same_day():
    import datetime as dt
    d = dt.date(2026, 11, 3)
    seen = {("10 etf go-anywhere portfolio", d)}
    assert is_reissue("10 etf go-anywhere portfolio", d, seen)
    assert not is_reissue("10 etf go-anywhere portfolio", d + dt.timedelta(days=1), seen)
    assert not is_reissue("l1 watch | weekly update", d, seen)


def test_product_uses_known_name_then_the_subject_head():
    assert product_of("October 2026 Senior Loan Officer Survey | x") == "senior_loan_officer"
    assert product_of("ISM | That Which Cannot Continue") == "ism"


def test_a_reissue_is_the_same_report():
    assert same_report("(*Chartbook Link Fixed*) Survey |\r\n Demand Weakens") == same_report("Survey | Demand\r\n Weakens")
    assert same_report("ISM | A") != same_report("ISM | B")


def test_tickers_are_uppercased():
    assert validate({"summary": "s", "calls": [dict(CALL)]})["calls"][0]["name"] == "OMF"


def test_misleading_digests_are_refused():
    for bad in ({"summary": " ", "calls": []},
                {"summary": "s", "calls": [{**CALL, "basis": "guess"}]},
                {"summary": "s", "calls": [{**CALL, "direction": "buy"}]},
                {"summary": "s", "calls": [{**CALL, "thesis": ""}]},
                {"summary": "s", "calls": [dict(CALL), dict(CALL)]}):
        try:
            validate(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_schema_requires_the_basis_of_every_call():
    assert "basis" in SCHEMA["properties"]["calls"]["items"]["required"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
