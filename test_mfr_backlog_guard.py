"""test_mfr_backlog_guard.py — the 2026-07-20 '456 bogus backlog' fix.

Run:  python3 test_mfr_backlog_guard.py

History: MFR BACKLOG emitted 456 names; 439 were already activated. Cause —
mfr_client.list_watchlist() returned [] (auth / timeout / changed response shape)
and the backlog, which is a SUBTRACTION, subtracted nothing. A guard was written up
that day and never built. These tests cover it, plus the provenance grouping that
makes an unfamiliar ticker name its own source feed.

Pure logic only: no DB, no network, no MFR calls.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.enrollment import (COLLAPSE_FLOOR, SRC_TAG, TAG_LEGEND,     # noqa: E402
                              TG_CHUNK, WatchlistUnavailable, _send_chunked,
                              group_by_origin, origin_summary,
                              provenance_lines, tagged_list, watchlist_verdict)


# ───────────────────────── the guard ────────────────────────────────────────

def test_empty_watchlist_is_always_refused():
    """The exact 7/20 failure: 0 names back means API failure, not empty account."""
    ok, why = watchlist_verdict(0, 560)
    assert ok is False
    assert "0 names" in why and "2026-07-20" in why, why
    # ...even with no history to compare against
    ok, why = watchlist_verdict(0, 0)
    assert ok is False, "empty must be refused even on a first-ever run"


def test_collapse_is_refused_gradual_decline_is_not():
    assert watchlist_verdict(560, 560)[0] is True
    assert watchlist_verdict(520, 560)[0] is True      # normal churn
    assert watchlist_verdict(340, 560)[0] is True      # 61% — just above the floor
    ok, why = watchlist_verdict(300, 560)              # 54% — collapse
    assert ok is False and "collapsed to 300" in why, why
    assert watchlist_verdict(1, 560)[0] is False


def test_floor_boundary_is_exact():
    last = 100
    assert watchlist_verdict(int(last * COLLAPSE_FLOOR), last)[0] is True
    assert watchlist_verdict(int(last * COLLAPSE_FLOOR) - 1, last)[0] is False


def test_first_ever_read_with_no_history_is_allowed():
    """No last-good count yet — any non-empty read has to be trusted."""
    assert watchlist_verdict(3, 0)[0] is True
    assert watchlist_verdict(560, 0)[0] is True


def test_growth_is_never_a_refusal():
    assert watchlist_verdict(900, 560)[0] is True


def test_refusal_reason_is_actionable():
    """A refusal the operator can't act on is just a different kind of silence."""
    _, why = watchlist_verdict(0, 560)
    for token in ("auth", "timeout", "shape"):
        assert token in why.lower(), f"reason should name {token}: {why}"


# ───────────────────────── provenance grouping ──────────────────────────────

PER_SOURCE = {
    "posmon": ["AAPL", "JBS", "FCFS", "ZG", "NVDA"],
    "book":   ["AAPL", "BUXX"],
    "keiths": ["FCFS", "ZG"],
    "ideas":  ["NVDA"],
}


def test_group_by_origin_names_every_feed_combination():
    g = group_by_origin(["AAPL", "JBS", "FCFS", "BUXX", "NVDA"], PER_SOURCE)
    assert g["book+posmon"] == ["AAPL"]
    assert g["posmon"] == ["JBS"]
    assert g["keiths+posmon"] == ["FCFS"]
    assert g["book"] == ["BUXX"]
    assert g["ideas+posmon"] == ["NVDA"]


def test_group_by_origin_flags_orphans():
    g = group_by_origin(["MYSTERY"], PER_SOURCE)
    assert g == {"<none>": ["MYSTERY"]}
    lines = provenance_lines(["MYSTERY"], PER_SOURCE)
    assert any("should be impossible" in ln for ln in lines), lines


def test_group_by_origin_handles_empty_inputs():
    assert group_by_origin([], PER_SOURCE) == {}
    assert group_by_origin(["X"], {}) == {"<none>": ["X"]}
    assert group_by_origin(["X"], None) == {"<none>": ["X"]}


def test_provenance_lines_biggest_group_first():
    lines = provenance_lines(["AAPL", "JBS", "FCFS", "BUXX", "NVDA"], PER_SOURCE)
    counts = [int(ln.split("] ")[1].split(":")[0]) for ln in lines
              if ln.startswith("• [")]
    assert counts == sorted(counts, reverse=True), counts


def test_provenance_lines_truncate_loudly_never_silently():
    many = [f"T{i:03d}" for i in range(150)]
    lines = provenance_lines(many, {"posmon": many}, cap_per_group=60)
    assert len(lines) == 1
    assert "[posmon] 150:" in lines[0]
    assert "+90 more" in lines[0], lines[0]
    assert lines[0].count(" T") == 60


def test_provenance_line_carries_the_full_count_not_just_shown():
    lines = provenance_lines([f"T{i}" for i in range(5)],
                             {"book": [f"T{i}" for i in range(5)]},
                             cap_per_group=2)
    assert "[book] 5:" in lines[0] and "+3 more" in lines[0], lines[0]


def test_a_name_in_one_feed_reads_as_that_feed_alone():
    """The whole point: 'where did FCFS come from' has a printed answer."""
    lines = provenance_lines(["FCFS"], PER_SOURCE)
    assert lines == ["• [keiths+posmon] 1: FCFS"], lines


# ───────────────────────── wiring is real, not just defined ─────────────────

def _src(rel):
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), rel),
                encoding="utf-8").read()


def test_both_compilers_go_through_the_guard():
    src = _src("tools/enrollment.py")
    assert "active = active_watchlist(force=force)" in src
    assert src.count("active_watchlist(force=force)") >= 2, \
        "compile_to_add AND compile_backlog must both be guarded"
    assert "_mfr_active()\n" in src  # raw helper still exists for the guard itself


def test_command_surfaces_handle_the_refusal():
    src = _src("tools/enrollment.py")
    assert src.count("except WatchlistUnavailable") >= 3, \
        "handle_backlog_command, run_weekly_backlog and run_nightly must all catch it"
    assert "MFR BACKLOG FORCE" in src, "operator needs an override"


def test_weekly_sweep_does_not_stamp_the_week_when_blocked():
    """If it stamped the week on a refusal, the sweep would be skipped until the
    next ISO week and the gap would go unnoticed."""
    src = _src("tools/enrollment.py")
    blocked = src.split("except WatchlistUnavailable as e:")[2]
    head = blocked.split("return f\"blocked")[0]
    assert "BACKLOG_WEEK_KEY" not in head, \
        "the refusal path must not mark the week as swept"


def test_served_crosscheck_catches_a_collapse_with_no_stored_history():
    """The 2026-07-30 reading, and the case a stored baseline CANNOT catch: first
    run after a deploy, high_water=0, watchlist returns a partial 88 while MFR is
    serving ranges for 560. Backlog printed 472 'un-enrolled' names while the dark
    footer said only 2 names lacked a range row — both cannot be true."""
    ok, why = watchlist_verdict(88, 0, served=560)
    assert ok is False, "a partial read must be caught without any history"
    assert "cannot serve a range" in why and "88" in why and "560" in why, why
    # a healthy read against the same evidence passes
    assert watchlist_verdict(560, 0, served=560)[0] is True
    assert watchlist_verdict(540, 0, served=560)[0] is True


def test_served_crosscheck_is_optional_and_fails_open():
    """mfr_snapshots unavailable -> served=0 -> the check simply doesn't fire."""
    assert watchlist_verdict(88, 0, served=0)[0] is True
    assert watchlist_verdict(0, 0, served=0)[0] is False   # empty still refused


def test_served_never_blocks_a_legitimately_larger_watchlist():
    """Activated-but-not-yet-fanned-out names mean count > served routinely."""
    assert watchlist_verdict(600, 560, served=540)[0] is True


def test_high_water_baseline_cannot_walk_itself_down():
    """The ratchet bug: 560→340 passes, then 340→205 passes, then 205→123 —
    three degraded reads and a 130-name watchlist is 'believable'. With a
    high-water baseline every step is measured against 560 and the second
    degraded read is refused."""
    src = _src("tools/enrollment.py")
    assert "if n > high_water:" in src, \
        "baseline must only rise on its own — an auto-ratchet walks down"
    # the sequence, evaluated against a NON-moving baseline
    assert watchlist_verdict(340, 560)[0] is True     # first partial: allowed
    assert watchlist_verdict(205, 560)[0] is False    # would have passed if rebased
    assert watchlist_verdict(123, 560)[0] is False


def test_force_rebases_so_a_real_shrink_does_not_wedge_the_jobs():
    """run_nightly/run_weekly take no force arg. If FORCE didn't reset the
    baseline, pruning the watchlist would block them permanently."""
    src = _src("tools/enrollment.py")
    forced = src.split("log.warning(\"watchlist guard BYPASSED")[1].split("return active")[0]
    assert "_set_state(WATCHLIST_GOOD_KEY" in forced, \
        "force must rebase the high-water mark"
    assert "if n:" in forced, "an EMPTY read must never become the baseline"


def test_refusal_notify_is_throttled_and_shared():
    src = _src("tools/enrollment.py")
    assert "BLOCKED_NOTIFY_KEY" in src
    assert src.count("_notify_blocked(") >= 3, \
        "nightly AND weekly must both notify through the shared throttle"
    stamp = src.split("def _notify_blocked")[1]
    assert stamp.index("send_telegram") < stamp.index("_set_state(BLOCKED_NOTIFY_KEY"), \
        "stamp the throttle AFTER the send, or a failed alert mutes the day"


# ───────────────────────── the paste list stays complete ────────────────────

def test_default_reply_carries_every_name_not_a_capped_view():
    """THE regression this format nearly shipped: grouped output capped at 60
    per group dropped ~40% of a 456-name backlog while still saying 'paste into
    MFR'. A half-enrolled account that reads as finished."""
    src = _src("tools/enrollment.py")
    body = src.split("def handle_backlog_command")[1]
    assert '" ".join(to_add)' in body, \
        "the default reply must emit the COMPLETE to_add list"
    assert "paste the untagged line" in body
    # the lossy view must not claim to be pasteable
    why = body.split("if why:")[1].split("return")[0]
    assert "provenance_lines" in why and "paste from plain MFR BACKLOG" in why


def test_origin_summary_is_counts_only_so_it_cannot_crowd_the_paste_list():
    many = [f"T{i:03d}" for i in range(400)]
    lines = origin_summary(many, {"posmon": many})
    assert len(lines) == 1
    assert "posmon=400" in lines[0]
    assert "T000" not in lines[0], "summary must not carry ticker names"
    assert len(lines[0]) < 200, f"summary should stay tiny, got {len(lines[0])}"


def test_origin_summary_folds_the_long_tail():
    per = {f"f{i}": [f"T{i}"] for i in range(12)}
    lines = origin_summary([f"T{i}" for i in range(12)], per, top=6)
    assert "smaller combos=6" in lines[0], lines[0]


def test_realistic_456_backlog_message_fits_after_chunking():
    """The 7/20 shape: 456 backlog, posmon dominating."""
    posmon = [f"P{i:03d}" for i in range(433)]
    book = [f"B{i:02d}" for i in range(23)]
    to_add = sorted(posmon + book)
    head = "x" * 120
    body = "\n".join([head] + origin_summary(to_add, {"posmon": posmon, "book": book})
                     + [" ".join(to_add)])
    sent = []
    _fake_send(sent, lambda: _send_chunked("MFR backlog", body))
    assert sent, "nothing sent"
    assert all(len(m) <= TG_CHUNK for _, m in sent), \
        [len(m) for _, m in sent]
    # every ticker survives the split
    joined = " ".join(m for _, m in sent)
    for t in to_add:
        assert t in joined, f"{t} lost in chunking"


def test_chunker_never_splits_a_ticker():
    names = [f"TICK{i:04d}" for i in range(900)]
    sent = []
    _fake_send(sent, lambda: _send_chunked("t", " ".join(names)))
    joined = " ".join(m for _, m in sent)
    for n in names:
        assert n in joined, n
    assert len(sent) > 1, "this should have needed multiple parts"


def test_chunker_reports_failure_when_any_part_fails():
    sent = []
    ok = _fake_send(sent, lambda: _send_chunked("t", "a\nb"), result=False)
    assert ok is False, "a failed part must not report success"


def _fake_send(sink, fn, result=True):
    """Run fn with notifier.send_telegram swapped for a recorder."""
    import notifier
    real = notifier.send_telegram

    def _rec(title, message, priority=1):
        sink.append((title, message))
        return result
    notifier.send_telegram = _rec
    try:
        return fn()
    finally:
        notifier.send_telegram = real


def test_weekly_marks_the_week_done_only_after_a_successful_send():
    src = _src("tools/enrollment.py")
    weekly = src.split("def run_weekly_backlog")[1]
    assert "if not ok:" in weekly
    assert weekly.index("_send_chunked") < weekly.index("_set_state(BACKLOG_WEEK_KEY, wk)\n    return f\"sent"), \
        "the week must be stamped after the send, not before"


def test_command_parsing_is_forgiving():
    """'/mfrbacklog force' used to fall through the dispatch chain and echo."""
    src = _src("tools/enrollment.py")
    body = src.split("def handle_backlog_command")[1]
    assert '" ".join(text.strip().upper().split())' in body, "normalise whitespace"
    assert '.replace("/MFRBACKLOG", "MFR BACKLOG")' in body
    assert 'rstrip(".!?")' in body


def test_dark_footer_is_bounded():
    src = _src("tools/enrollment.py")
    footer = src.split("def dark_footer")[1]
    assert "_cap(" in footer, "footer rides every MFR command — it must be capped"


def test_list_call_gets_its_own_timeout():
    """2026-08-01 measurement: the LIST response is 3.95 MB / 622 assets, vs a few
    KB per ticker. Sharing MFR_TIMEOUT=20s is what made it fail intermittently,
    and an empty read reads as an empty account."""
    import mfr_client
    assert mfr_client.MFR_LIST_TIMEOUT >= 60, mfr_client.MFR_LIST_TIMEOUT
    assert mfr_client.MFR_LIST_TIMEOUT > mfr_client.MFR_TIMEOUT
    src = _src("mfr_client.py")
    assert "_http_get_json(url, timeout=MFR_LIST_TIMEOUT)" in src


def test_empty_watchlist_reports_zero_coverage_before_bailing():
    """The early return skipped _report_fanout_completeness, so a failed fan-out
    left the previous 1.000 in bot_state and the health metric read green."""
    src = _src("mfr_client.py")
    body = src.split("def refresh_watchlist")[1].split("fanout = sorted")[0]
    assert "_report_fanout_completeness(0, 0, [])" in body
    assert body.index("_report_fanout_completeness") < body.index("return {")


def test_watchlist_reads_are_recorded():
    src = _src("mfr_client.py")
    assert src.count("_record_watchlist_read(") >= 5, \
        "every exit path of list_watchlist must leave a trace"
    assert "ZERO parseable tickers" in src, \
        "a 200-with-rows-but-no-tickers shape change must be called out"



# ───────────────────── junk sweep: evidence, not a word list ────────────────

def _classify():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "js", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_junk_sweep.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.classify


def _E(strong=None, weak=None, ocr=None):
    return {"strong": strong or [], "ocr": ocr or [], "weak": weak or []}


def test_hedgeye_data_decides_membership_not_spelling():
    c = _classify()
    ev = {"RTX": _E(strong=["risk_range"]), "BEEN": _E(weak=["sigstr"])}
    v = c(["RTX", "BEEN"], ev, quoted={"RTX"}, held=set())
    assert v["RTX"][0] == "COVERED"
    assert v["BEEN"][0] == "JUNK"


def test_real_tickers_that_look_like_words_are_surfaced_not_deleted():
    """HAS is Hasbro, JUST is the Goldman Sachs JUST US Large Cap ETF. Both sit
    in corpus_rag._TICKER_STOPWORDS, so a stopword clean-up deletes two real
    names. With no Hedgeye data they are TOKEN-ONLY — flagged for the operator,
    never auto-condemned."""
    c = _classify()
    ev = {"HAS": _E(weak=["sigstr"]), "JUST": _E(weak=["sigstr"])}
    v = c(["HAS", "JUST"], ev, quoted={"HAS", "JUST"}, held=set())
    for t in ("HAS", "JUST"):
        assert v[t][0] == "TOKEN-ONLY", v[t]
        assert v[t][0] != "JUNK"


def test_weak_membership_alone_never_proves_coverage():
    """hedgeye_signal_strength carries ticker + flags and no values — it is the
    output of the unfiltered regex, so it cannot vouch for a name."""
    c = _classify()
    v = c(["X"], {"X": _E(weak=["sigstr", "ss_roster"])}, quoted=set(), held=set())
    assert v["X"][0] == "JUNK"


def test_held_names_survive_with_no_coverage_at_all():
    c = _classify()
    v = c(["BBRE"], {"BBRE": _E()}, quoted={"BBRE"}, held={"BBRE"})
    assert v["BBRE"][0] == "HELD"


def test_any_single_strong_source_is_enough():
    c = _classify()
    for src in ("risk_range", "etf_pro", "keiths", "posmon_seed", "posmon_live",
                "ideas", "portsol"):
        v = c(["Z"], {"Z": _E(strong=[src])}, quoted=set(), held=set())
        assert v["Z"][0] == "COVERED", (src, v["Z"])


def test_no_wordlist_is_used_to_decide_membership():
    src = _src("_junk_sweep.py")
    body = src.split("def classify")[1].split("if __name__")[0]
    for banned in ("STOPWORD", "_TICKER_STOPWORDS", "WORDS ="):
        assert banned not in body, f"classify() must not consult {banned}"


def test_the_2026_08_01_backlog_splits_correctly():
    c = _classify()
    ev = {t: _E(weak=["sigstr"]) for t in ("BEEN", "FROM", "LIST", "SIGNAL",
                                           "HAS", "JUST")}
    ev["RTX"] = _E(strong=["risk_range"])
    ev["BBRE"] = _E()
    v = c(list(ev), ev, quoted={"HAS", "JUST", "RTX", "BBRE"}, held={"BBRE"})
    assert sorted(t for t, (x, _) in v.items() if x == "JUNK") == \
        ["BEEN", "FROM", "LIST", "SIGNAL"]
    assert sorted(t for t, (x, _) in v.items() if x == "TOKEN-ONLY") == \
        ["HAS", "JUST"]


def test_sweep_refuses_to_judge_without_a_price_feed():
    """No quotes means no evidence — every verdict would be a guess."""
    src = _src("_junk_sweep.py")
    assert "cannot judge" in src or "would be a guess" in src
    assert "return 2" in src.split("price feed unavailable")[1][:200]



def test_pm_bucket_alone_does_not_prove_coverage():
    """Position Monitor buckets come from OCR of a PDF. pm_parse's own comment
    records that sector headers (GLL, RETAIL, ENERGY) match its ticker pattern,
    and posmon is ~435 of the universe — so an unconditional bucket would
    launder every OCR artifact into COVERED."""
    c = _classify()
    v = c(["ZZTOP"], {"ZZTOP": _E(ocr=["posmon_seed"])}, quoted=set(), held=set())
    assert v["ZZTOP"][0] == "PM-ARTIFACT", v["ZZTOP"]


def test_pm_bucket_plus_corroboration_is_coverage():
    """The PM is a single-stock / ETF list — every real entry quotes."""
    c = _classify()
    ev = {"JBS": _E(ocr=["posmon_seed"])}
    assert c(["JBS"], ev, quoted={"JBS"}, held=set())["JBS"][0] == "COVERED"
    assert c(["JBS"], ev, quoted=set(), held={"JBS"})["JBS"][0] == "COVERED"


def test_pm_header_words_are_named_as_artifacts():
    c = _classify()
    for w in ("RETAIL", "ENERGY", "GLL", "LONGS", "BENCH"):
        v = c([w], {w: _E(ocr=["posmon_seed"])}, quoted=set(), held=set())
        assert v[w][0] == "PM-ARTIFACT", (w, v[w])
        assert "header word" in v[w][1], v[w]


def test_a_values_feed_still_outranks_everything():
    c = _classify()
    v = c(["AAPL"], {"AAPL": _E(strong=["risk_range"], ocr=["posmon_seed"])},
          quoted=set(), held=set())
    assert v["AAPL"][0] == "COVERED" and "risk_range" in v["AAPL"][1]



# ───────────────────── per-ticker source tags ───────────────────────────────

def test_every_ticker_carries_its_source():
    per = {"etfpro": ["CARZ"], "posmon": ["CARZ", "RTX"], "book": ["BBRE"],
           "riskrange": ["RTX"]}
    out = tagged_list(["BBRE", "CARZ", "RTX"], per)
    assert out == "BBRE(bk) CARZ(ep,pm) RTX(pm,rr)", out


def test_multi_source_tags_are_sorted_and_deduped():
    per = {"posmon": ["X"], "etfpro": ["X"], "book": ["X"]}
    assert tagged_list(["X"], per) == "X(bk,ep,pm)"


def test_unknown_feed_falls_back_to_its_own_name():
    """A newly registered source must never render as untagged."""
    assert tagged_list(["X"], {"brand_new": ["X"]}) == "X(brand_new)"


def test_orphan_is_marked_not_blank():
    assert tagged_list(["Z"], {}) == "Z(?)"
    assert tagged_list(["Z"], None) == "Z(?)"


def test_legend_documents_every_code():
    for code in SRC_TAG.values():
        assert f"{code}=" in TAG_LEGEND, code


def test_tags_never_contaminate_the_paste_line():
    """The tagged view is not pasteable — MFR would reject 'CARZ(ep,pm)'. The
    untagged list must still be emitted separately and stay clean."""
    src = _src("tools/enrollment.py")
    body = src.split("def handle_backlog_command")[1]
    assert 'lines.append(" ".join(to_add))' in body, "clean paste line missing"
    assert "paste this line (no tags)" in body
    assert "paste the untagged line" in body
    # and the tagged helper says so itself
    assert "NOT pasteable" in _src("tools/enrollment.py")



# ───────────────── symbol validation gate (2026-08-23 malformed-ticker fix) ─
# History: the backlog emitted garbage strings (MORRIS, WIDEST, MAG7, BUXXX,
# APPL) — upstream parser artifacts stored in roster tables and passed through
# verbatim. The gate: shape check, then a live-quote probe; dropped symbols are
# counted in the head line and listed, never printed in the paste line. The
# probe is injected here so these tests stay offline.

def _validate_with_probe(symbols, probe):
    from tools import enrollment as e
    orig = e._live_quote_probe
    e._live_quote_probe = probe
    try:
        return e.validate_backlog_symbols(symbols)
    finally:
        e._live_quote_probe = orig


def test_malformed_symbols_are_shape_dropped_before_any_quote_call():
    calls = []
    def probe(syms):
        calls.append(sorted(syms))
        return set(syms)
    r = _validate_with_probe(
        ["AAPL", "100.00", "2513", "USD/YEN", "-XLV260717C230", "MSFT"], probe)
    assert r["kept"] == ["AAPL", "MSFT"], r
    assert sorted(r["dropped_shape"]) == ["-XLV260717C230", "100.00", "2513",
                                          "USD/YEN"]
    assert calls == [["AAPL", "MSFT"]], "shape-dropped names must not be quoted"


def test_suffixed_and_futures_forms_pass_the_shape_gate():
    from tools.enrollment import _symbol_shape_ok
    for good in ("RPI.L", "005930.KS", "1913.HK", "ADS.DE", "VOLV-B.ST",
                 "ES_F", "FESB_F", "BRK-B"):
        assert _symbol_shape_ok(good), good
    for bad in ("2513", "100.00", "USD/YEN", "", "-XLV260717C230",
                "TOOLONGNAME.XX"):
        assert not _symbol_shape_ok(bad), bad


def test_unresolvable_symbols_are_dropped_and_listed():
    r = _validate_with_probe(["AAPL", "MORRIS", "WIDEST", "MSFT", "MAG7",
                              "GOOG", "AMZN", "NVDA", "META", "TSLA"],
                             lambda syms: set(syms) - {"MORRIS", "WIDEST",
                                                       "MAG7"})
    assert "MORRIS" not in r["kept"] and "MAG7" not in r["kept"]
    assert sorted(r["dropped_quote"]) == ["MAG7", "MORRIS", "WIDEST"]
    assert r["note"] == ""


def test_quote_gate_fails_open_when_the_feed_is_degraded():
    """Resolving under QUOTE_RATE_FLOOR means the FEED is broken, not the
    symbols — dropping half the backlog on a rate-limit would be the same
    confident lie the watchlist guard exists to prevent."""
    r = _validate_with_probe(["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"],
                             lambda syms: {"AAPL"})   # 20% < floor
    assert sorted(r["kept"]) == ["AAPL", "AMZN", "GOOG", "MSFT", "NVDA"]
    assert r["dropped_quote"] == []
    assert "FAILED OPEN" in r["note"]


def test_probe_exception_fails_open_with_a_note():
    def probe(syms):
        raise RuntimeError("rate limited")
    r = _validate_with_probe(["AAPL", "MSFT"], probe)
    assert r["kept"] == ["AAPL", "MSFT"]
    assert "rate limited" in r["note"]


def test_held_names_are_tagged_with_fill_in_the_tagged_view():
    out = tagged_list(["AAPL", "PANW"], {"keiths": {"AAPL", "PANW"}},
                      held_fills={"PANW": 53.4})
    assert "PANW(kt,held 53%fill)" in out, out
    assert "AAPL(kt)" in out
    # held with unknown fill still marked, never crashes
    out = tagged_list(["PANW"], {}, held_fills={"PANW": None})
    assert "PANW(?,held)" in out, out


def test_dropped_symbols_never_reach_the_paste_line():
    """compile_backlog paste line = kept only; the dropped list is a separate
    labeled line plus a count in the head line."""
    src = _src("tools/enrollment.py")
    assert "dropped {len(dropped)} " in src or "dropped {len(dropped)}" in src \
        or "dropped " in src.split("def handle_backlog_command")[1].split(
            "── paste this line")[0], "head line must carry the dropped count"
    body = src.split("def handle_backlog_command")[1]
    assert "_validation_lines" in body, \
        "backlog command must render the dropped/held evidence lines"
    wk = src.split("def run_weekly_backlog")[1].split("def handle_backlog")[0]
    assert "_validation_lines" in wk, "weekly sweep must render them too"


def test_compile_backlog_routes_through_the_validation_gate():
    src = _src("tools/enrollment.py")
    body = src.split("def compile_backlog")[1].split("def run_weekly_backlog")[0]
    assert "validate_backlog_symbols" in body
    assert "held_fill_map" in body
    nightly = src.split("def compile_to_add")[1].split("def run_nightly")[0]
    assert "validate_backlog_symbols" in nightly, \
        "the nightly to-add pastes into MFR too — same gate"


if __name__ == "__main__":
    import inspect
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
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
