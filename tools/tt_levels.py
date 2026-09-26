"""TRADE / TREND tag for every screen (Kris 9/26: "trade and trend in all the cards").

Values come from v_screener.tv_trade / tv_trend (migration 117), i.e. the
TradingView blend: TREND = MFR Trend daily band, TRADE = Vola2vola daily line,
read overnight (tools/tv_levels.py). One formatter so every card prints it the
same way:

    TRD 63.21 TND 58.59        bullish (TND = band floor, TRD = upper Vola line)
    TRD 61.67 TND 60.20·mir    bearish — mirrored levels, not yet validated
"""
from __future__ import annotations


def _p(v) -> str:
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:.1f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def tt_tag(trade, trend, basis: str | None = None) -> str:
    """' TRD x TND y' (leading space) or '' when neither level exists."""
    if trade is None and trend is None:
        return ""
    s = f" TRD {_p(trade) if trade is not None else '—'} TND {_p(trend) if trend is not None else '—'}"
    return s + ("·mir" if basis == "bear-mirror" else "")


def tt_map(tickers) -> dict:
    """{ticker: (trade, trend, basis)} from v_screener. Never raises."""
    tickers = [t for t in {t for t in (tickers or []) if t}]
    if not tickers:
        return {}
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, tv_trade, tv_trend, tv_basis FROM v_screener "
                        "WHERE ticker = ANY(%s)", (tickers,))
            return {t: (tr, tn, b) for t, tr, tn, b in cur.fetchall()}
    except Exception:
        return {}


def tt_for(ticker: str, cache: dict | None = None) -> str:
    """Tag for one ticker, using a tt_map() cache when the caller has one."""
    row = (cache if cache is not None else tt_map([ticker])).get(ticker)
    return tt_tag(*row) if row else ""
