"""Fixture tests for doc_ingest pure logic (no DB, no network) —
classification, date parsing, text extraction, reply formatting.
    python test_doc_ingest.py
"""
from datetime import date

from tools.doc_ingest import (classify_upload, parse_note_date,
                              extract_text, summary_reply)


# ── classification (filename wins, then content head) ──

def test_classify_from_filename():
    assert classify_upload("founders_note_am_2026-07-11.pdf", "") == "founders_note_am"
    assert classify_upload("SpotGamma Founders Note PM.pdf", "") == "founders_note_pm"
    assert classify_upload("flow_patrol_jul11.pdf", "") == "flow_patrol"
    assert classify_upload("EquityHub_export.csv", "") == "equity_hub"
    # SpotGamma's actual export naming (live 7/11 upload)
    assert classify_upload("SPX_data-table_2026-07-11.csv", "") == "equity_hub"
    assert classify_upload("Tier One Alpha 7-11.pdf", "") == "tier1alpha"
    assert classify_upload("tier_1_alpha.pdf", "") == "tier1alpha"


def test_classify_from_content_when_filename_generic():
    assert classify_upload("report.pdf", "SpotGamma Founder's Note — AM edition") == "founders_note_am"
    assert classify_upload("doc(3).pdf", "Welcome to Flow Patrol for July 11") == "flow_patrol"
    assert classify_upload("x.pdf", "TIER ONE ALPHA daily: 1M vs 3M vol") == "tier1alpha"


def test_classify_unknown_is_other_never_dropped():
    assert classify_upload("random.pdf", "some unrelated text") == "other"
    assert classify_upload(None, None) == "other"


def test_classify_plain_founders_note_without_ampm():
    assert classify_upload("founders_note.pdf", "") == "founders_note"


# ── note date ──

def test_date_from_filename_formats():
    assert parse_note_date("founders_note_2026-07-11.pdf", "") == date(2026, 7, 11)
    assert parse_note_date("flow patrol 7/11/2026.pdf", "") == date(2026, 7, 11)
    assert parse_note_date("x_2026_07_11.pdf", "") == date(2026, 7, 11)


def test_date_from_content_month_name():
    assert parse_note_date("x.pdf", "Founder's Note\nJuly 11, 2026\n...") == date(2026, 7, 11)


def test_date_day_month_abbrev_flow_patrol_format():
    # '10 Jul 2026' — the live Flow Patrol miss (7/12)
    assert parse_note_date(None, "Extreme Positioning 10 Jul 2026 FlowPatrol") == date(2026, 7, 10)
    assert parse_note_date(None, "as of 5 September 2026 update") == date(2026, 9, 5)


def test_date_missing_is_none_never_guessed():
    assert parse_note_date("report.pdf", "no dates here") is None
    assert parse_note_date("bad_2026-13-45.pdf", "") is None   # invalid -> None


# ── extraction ──

def test_extract_textlike_formats():
    t, note = extract_text("note.md", "# Founder's Note\nvol is low".encode())
    assert "vol is low" in t and note is None
    t, note = extract_text("data.csv", b"ticker,price\nSPY,620")
    assert "SPY,620" in t and note is None


def test_extract_unknown_binary_is_loud_not_silent():
    t, note = extract_text("pic.jpg", b"\xff\xd8\xff\xe0" + bytes(range(200)))
    assert t == "" and "unextractable" in note


# ── reply ──

def test_summary_reply_undated_is_loud():
    r = summary_reply("flow_patrol", None, 1234, "fp.pdf")
    assert "UNDATED ⚠" in r and "flow_patrol" in r


def test_summary_reply_other_flags_unclassified():
    r = summary_reply("other", date(2026, 7, 11), 10, "x.pdf",
                      preview="hello")
    assert "⚠unclassified" in r and "head: hello" in r


# ── paste-capture buffer (bot_state faked in-memory; store faked) ──

def _fake_state():
    import tools.doc_ingest as di
    state = {}
    di._bs_get = lambda k: state.get(k)
    def _set(k, v):
        state[k] = v
    di._bs_set = _set
    stored = []
    di.store_upload = lambda fn, kind, nd, text, meta=None: (
        stored.append((fn, kind, nd, text, meta)) or 77)
    return di, state, stored


def test_buffer_start_append_end_stitches_one_row():
    di, _s, stored = _fake_state()
    r = di.handle_doc_buffer("DOC START equity hub")
    assert "buffering" in r and "(equity hub)" in r
    assert di.handle_doc_buffer("row1,SPY,620") == ""     # silent claim
    assert di.handle_doc_buffer("row2,QQQ,560") == ""
    r = di.handle_doc_buffer("DOC END")
    assert "kind=equity_hub" in r and "2 chunks stitched" in r
    assert len(stored) == 1
    _fn, kind, _nd, text, meta = stored[0]
    assert kind == "equity_hub" and "row1,SPY,620" in text
    assert meta["chunks"] == 2


def test_buffer_claims_command_lookalikes():
    di, _s, _st = _fake_state()
    di.handle_doc_buffer("DOC START")
    # a pasted line that LOOKS like a command must be swallowed, not executed
    assert di.handle_doc_buffer("TARGET FXH 4.0") == ""
    assert di.handle_doc_buffer("REPORT") == ""
    r = di.handle_doc_buffer("DOC CANCEL")
    assert "discarded (2 chunks)" in r


def test_buffer_inactive_falls_through():
    di, _s, _st = _fake_state()
    assert di.handle_doc_buffer("SCREEN energy longs") is None
    assert di.handle_doc_buffer("DOC END") is None        # nothing buffering...
    # ^ no active buffer: END has no buffer -> falls through? spec: buf None
    #   and up == 'DOC END' -> returns None (not a DOC session) — correct.


def test_buffer_quiet_ack_every_10():
    di, _s, _st = _fake_state()
    di.handle_doc_buffer("DOC START")
    for i in range(1, 10):
        assert di.handle_doc_buffer(f"line {i}") == ""
    assert di.handle_doc_buffer("line 10") == "📋 10 chunks…"


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
