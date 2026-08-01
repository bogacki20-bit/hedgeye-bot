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

from tools.enrollment import (COLLAPSE_FLOOR, TG_CHUNK,               # noqa: E402
                              WatchlistUnavailable, _send_chunked,
                              group_by_origin, origin_summary,
                              provenance_lines, watchlist_verdict)


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
    assert "paste the full list above" in body
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


def test_junk_needs_all_three_negatives():
    c = _classify()
    v = c(["BEEN"], has_range=set(), quoted=set(), held=set())
    assert v["BEEN"].startswith("JUNK")
    # any single piece of evidence rescues it
    assert not c(["BEEN"], {"BEEN"}, set(), set())["BEEN"].startswith("JUNK")
    assert not c(["BEEN"], set(), {"BEEN"}, set())["BEEN"].startswith("JUNK")
    assert not c(["BEEN"], set(), set(), {"BEEN"})["BEEN"].startswith("JUNK")


def test_real_tickers_that_look_like_words_survive():
    """HAS is Hasbro, JUST is the Goldman Sachs JUST US Large Cap ETF. Both are
    in corpus_rag._TICKER_STOPWORDS, so reusing that list to clean the universe
    would delete two real Hedgeye names. Evidence must override spelling."""
    c = _classify()
    v = c(["HAS", "JUST"], has_range=set(), quoted={"HAS", "JUST"}, held=set())
    assert not v["HAS"].startswith("JUNK"), v["HAS"]
    assert not v["JUST"].startswith("JUNK"), v["JUST"]


def test_no_wordlist_is_used_to_decide_membership():
    src = _src("_junk_sweep.py")
    body = src.split("def classify")[1].split("def main")[0]
    for banned in ("STOPWORD", "_TICKER_STOPWORDS", "WORDS ="):
        assert banned not in body, f"classify() must not consult {banned}"


def test_the_2026_08_01_backlog_splits_correctly():
    """The real 20-name backlog: 4 junk, HAS/JUST real, BBRE/CERY held."""
    c = _classify()
    cands = ["BEEN", "FROM", "LIST", "SIGNAL", "HAS", "JUST", "MEME", "PTF",
             "RTX", "BBRE", "CERY"]
    v = c(cands, has_range={"RTX", "MEME"},
          quoted={"HAS", "JUST", "MEME", "PTF", "RTX", "BBRE", "CERY"},
          held={"BBRE", "CERY"})
    junk = sorted(t for t, x in v.items() if x.startswith("JUNK"))
    assert junk == ["BEEN", "FROM", "LIST", "SIGNAL"], junk


def test_range_without_quote_is_flagged_but_not_deleted():
    c = _classify()
    v = c(["BITCOIN"], has_range={"BITCOIN"}, quoted=set(), held=set())
    assert "not junk" not in v["BITCOIN"]
    assert not v["BITCOIN"].startswith("JUNK")
    assert "symbol" in v["BITCOIN"] or "mapping" in v["BITCOIN"], v["BITCOIN"]


def test_sweep_refuses_to_judge_without_a_price_feed():
    """No quotes means no evidence — every verdict would be a guess."""
    src = _src("_junk_sweep.py")
    assert "cannot judge" in src or "would be a guess" in src
    assert "return 2" in src.split("price feed unavailable")[1][:200]


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
