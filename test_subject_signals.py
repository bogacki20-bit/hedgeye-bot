"""Tests for parser_subject_signals — the case gate (2026-09-08 MOMO
artifact: 'Momo (-2.9%)' in a MOMO Tracker subject is the momentum
basket, but it was stored as ticker MOMO — Hello Group's real symbol —
and reached the MFR enrollment backlog)."""
import parser_momo
from parser_subject_signals import extract_signals


def toks(subject, normalize=None, stop=None):
    return {s["token"] for s in extract_signals(
        subject,
        normalize=parser_momo._NORMALIZE if normalize is None else normalize,
        stop=parser_momo._STOP if stop is None else stop)}


def test_momo_basket_word_rejected():
    got = toks("MOMO Tracker | Momo (-2.9%), TSLA (+4%, BULLISH)")
    assert "MOMO" not in got
    assert "TSLA" in got


def test_mixed_case_prose_rejected_generally():
    got = toks("MOMO Tracker | Chips (-1.2%), NVDA (+2%)")
    assert "CHIPS" not in got
    assert "NVDA" in got


def test_mag7_pseudo_token_still_passes():
    got = toks("MOMO Tracker | Mag7 (+0.5%), MSFT/ORCL=BULLISH")
    assert "MAG7" in got and "MSFT" in got and "ORCL" in got


def test_trend_phrase_mixed_case_rejected():
    got = toks("MOMO Tracker | Momo To Bullish Trend, ORCL To Bullish Trend")
    assert "MOMO" not in got
    assert "ORCL" in got


def test_uppercase_momo_blocked_by_momo_stoplist():
    # belt behind the case gate: even an uppercase MOMO in this product's
    # subject is the basket, never Hello Group
    got = toks("MOMO Tracker | MOMO (-1.0%), AAPL (+1%)")
    assert "MOMO" not in got
    assert "AAPL" in got


def test_crypto_btc_glyph_still_passes():
    got = toks("CRYPTO QUANT | ₿TC (-0.9%), ETH/SOL=BEARISH",
               normalize={"TC": "BTC"}, stop=set())
    assert "BTC" in got and "ETH" in got and "SOL" in got
