"""Fixture tests for REPORT v4 pure logic (no DB) — flow-quality marks,
Δ-header, concentration clusters, candidates line. (Tranche v2 fill logic
lives in tools/position_targets — see test_position_targets.py.)
    python test_report_v4.py
"""
from tools.report import (flow_mark, delta_line, conc_clusters, conc_line,
                          candidates_line)


# ── P5 flow-quality ──

def test_flow_mark_agree_and_conflict():
    assert flow_mark(+0.38, "HH/HL") == "✓"      # rising rp, ascending range
    assert flow_mark(-0.20, "LH/LL") == "✓"      # falling rp, descending range
    assert flow_mark(+0.38, "LH/LL") == "✗"      # the XLB 7/11 fade case
    assert flow_mark(-0.10, "HH/HL") == "✗"


def test_flow_mark_no_verdict_cases():
    assert flow_mark(None, "HH/HL") == ""        # no Δ — no verdict
    assert flow_mark(0.30, None) == ""           # no structure
    assert flow_mark(0.30, "HH/LL") == ""        # widening: neither
    assert flow_mark(-0.30, "LH/HL") == ""       # compressing: neither
    assert flow_mark(0.0, "HH/HL") == ""         # flat Δ: no verdict


# ── P7 Δ-header ──

def test_delta_first_snapshot():
    assert "first snapshot" in delta_line(None, {"flags": []})


def test_delta_new_flags_and_drops_and_flow():
    prev = {"flags": ["TLT"], "sector_rp": {"XLV": 0.65, "XLK": 0.5},
            "ss_book_drops": []}
    cur = {"flags": ["TLT", "SHY", "XLU"], "sector_rp": {"XLV": 0.34, "XLK": 0.52},
           "ss_book_drops": ["NVO"]}
    d = delta_line(prev, cur)
    assert "2 new ⚠ (SHY XLU)" in d
    assert "1 SS drop affects book (NVO)" in d
    assert "XLV flow -0.31" in d


def test_delta_small_flow_moves_suppressed():
    prev = {"flags": [], "sector_rp": {"XLK": 0.50}, "ss_book_drops": []}
    cur = {"flags": [], "sector_rp": {"XLK": 0.55}, "ss_book_drops": []}
    assert delta_line(prev, cur) == "Δ since last: none"   # |Δ|<0.10 = noise


def test_delta_resolved_flags_are_not_new():
    prev = {"flags": ["TLT", "SHY"], "sector_rp": {}, "ss_book_drops": []}
    cur = {"flags": ["TLT"], "sector_rp": {}, "ss_book_drops": []}
    assert delta_line(prev, cur) == "Δ since last: none"


# ── P3 concentration ──

def test_conc_clusters_overlap_and_untagged():
    # 6-tuple: (gics_sector, rate_sensitive, duration_char, commodity_linked,
    #           exposure, inverse). 3-tuples still accepted (padded).
    pos = [("TLT", 10.0, ("Fixed Income", 1, "duration", None, None, 0)),
           ("SHY", 8.0, ("Fixed Income", 1, "duration", None, None, 0)),
           ("XLV", 6.0, ("Health Care", 0, None, None, None, 0)),
           ("USO", 5.0, (None, 0, None, 1, "commodity-proxy", 0)),   # energy
           ("UGA", 4.0, (None, 0, None, 1, "commodity-proxy", 0)),   # energy
           ("SH", 3.0, (None, 0, None, None, "broad-market", 1)),    # inverse
           ("IBIT", 2.0, ("Legacy Row", 0, None)),                   # 3-tuple
           ("NOGX", 1.0, (None, 0, None, None, None, 0)),  # row, no axis
           ("ZZZZ", 2.0, None)]                            # not in ticker_tags
    c = conc_clusters(pos)
    assert c["rate_sensitive"] == [2, 18.0]
    assert c["fixed_income"] == [2, 18.0]
    assert c["dur:duration"] == [2, 18.0]      # dur: prefix — 'duration' as a
    assert "duration" not in c                 # bare word would read as a side
    assert c["health_care"] == [1, 6.0]
    # commodity_linked AND commodity-proxy fold into ONE 'commodity' cluster;
    # a single position is not double-counted (USO+UGA = 2 pos, 9.0%w).
    assert c["commodity"] == [2, 9.0]
    assert c["broad-market"] == [1, 3.0]       # SH's underlying
    assert c["inverse"] == [1, 3.0]            # SH's geared flag
    assert c["legacy_row"] == [1, 2.0]         # 3-tuple padded, still clusters
    assert c["no-gics"] == [1, 1.0]            # has a row, no grouping axis
    assert c["no-tags"] == [1, 2.0]            # absent from ticker_tags
    assert "untagged" not in c                 # the lying label is gone


def test_conc_line_top3_and_top_cluster():
    line = conc_line({"rate_sensitive": [8, 22.4], "healthcare": [6, 17.8],
                      "commodity": [5, 12.0], "tech": [2, 3.0],
                      "no-gics": [1, 1.5], "no-tags": [1, 0.8]})
    assert line.startswith("CONC: rate_sensitive 8pos/22.4%w · "
                           "healthcare 6pos/17.8%w · commodity 5pos/12.0%w")
    assert "no-gics 1pos/1.5%w" in line
    assert "no-tags 1pos/0.8%w" in line
    assert "top_cluster: rate_sensitive" in line
    assert "tech" not in line                       # only top 3 + residuals


# ── P6 candidates ──

def test_candidates_format_rule_inline_no_advice():
    line = candidates_line([("FXH", 0.20, None), ("NVO", 0.21, 52.0)])
    assert line == ("CANDIDATES: FXH(0.20) NVO(0.21,held 52%fill)  "
                    "[rule: TREND=BULL + rp<0.35 + fill<80%]")
    assert "buy" not in line.lower()


def test_candidates_empty_and_cap():
    assert candidates_line([]).startswith("CANDIDATES: none [rule:")
    rows = [(f"T{i}", 0.10 + i / 100, None) for i in range(15)]
    line = candidates_line(rows, cap=12)
    assert "+3 more" in line


if __name__ == "__main__":
    import sys, inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"],
                                              inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
