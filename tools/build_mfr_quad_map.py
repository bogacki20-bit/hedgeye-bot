"""Build config/mfr_quad_map.yaml — the bot's per-Quad, per-ticker classification.

Sources:
  1. config/hedgeye_doctrine.yaml::quad_universe  → seed=doctrine (high confidence)
  2. price_monitor.HEDGEYE_TO_YFINANCE keys       → broad ticker universe coverage
  3. (optional) Railway DB UNION of hedgeye_*     → operational universe
  4. Sector/instrument heuristics                 → seed=heuristic
  5. Anything left → all-neutral with inferred=pending

Output shape:
    tickers:
      SPY:
        quad_1: long
        quad_2: long
        quad_3: short
        quad_4: short
        sector: Equity-Broad
      ...
    meta:
      SPY:        { source: seed=doctrine }
      AAPL:       { source: seed=heuristic, sector_inferred: Equity-Tech }
      OBSCURE:    { source: inferred=pending }

Idempotent — reads any existing yaml and only overwrites entries with a strictly
better source (doctrine > heuristic > pending). Operator-curated overrides stay.

CLI:
    python -m tools.build_mfr_quad_map                 # default (no DB read)
    python -m tools.build_mfr_quad_map --include-db    # union with Railway DB universe
    python -m tools.build_mfr_quad_map --infer-with-claude   # run Claude pass on pending tickers
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_DOCTRINE_PATH = _REPO / "config" / "hedgeye_doctrine.yaml"
_MAP_PATH      = _REPO / "config" / "mfr_quad_map.yaml"

# Source-confidence ordering (high → low). Anything strictly higher overrides.
_SOURCE_ORDER = {
    "operator":         100,
    "seed=doctrine":     80,
    "inferred=claude":   50,
    "seed=heuristic":    30,
    "inferred=pending":  10,
}

QUAD_KEYS = ("quad_1", "quad_2", "quad_3", "quad_4")
SIDES     = ("long", "short", "neutral")


# ─────────────────────────── Heuristic sector classification ────────────────

# Hand-curated sector buckets keyed to the four-Quad rubric.
# Q1 (Goldilocks: growth↑ inf↓): long growth/tech/discretionary, short defensives
# Q2 (Reflation:  growth↑ inf↑): long cyclicals/energy/banks, short rates-sensitive
# Q3 (Stagflation: growth↓ inf↑): long gold/energy/staples/REITs, short growth/banks
# Q4 (Deflation:  growth↓ inf↓): long bonds/staples/utilities, short cyclicals/banks
SECTOR_QUAD_RULES = {
    # sector key                       Q1      Q2       Q3       Q4
    "Equity-Broad":                   ("long", "long",  "short", "short"),
    "Equity-SmallCap":                ("long", "long",  "short", "short"),
    "Equity-Tech":                    ("long", "long",  "short", "short"),
    "Equity-Growth":                  ("long", "long",  "short", "short"),
    "Equity-Discretionary":           ("long", "long",  "short", "short"),
    "Equity-Comm":                    ("long", "neutral","short","short"),
    "Equity-Industrials":             ("long", "long",  "short", "short"),
    "Equity-Materials":               ("neutral","long","neutral","short"),
    "Equity-Financials":              ("long", "long",  "short", "short"),
    "Equity-Energy":                  ("neutral","long","long",  "short"),
    "Equity-REITs":                   ("neutral","neutral","long","neutral"),
    "Equity-Utilities":               ("short","short", "long",  "long"),
    "Equity-Staples":                 ("short","short", "long",  "long"),
    "Equity-Healthcare":              ("neutral","neutral","long","long"),
    "Equity-HighBeta":                ("long", "long",  "short", "short"),
    "Equity-LowBeta":                 ("short","short", "long",  "long"),
    "Equity-Momentum":                ("long", "long",  "neutral","short"),
    "Equity-Quality":                 ("long", "neutral","long", "long"),
    "Equity-Dividend":                ("neutral","neutral","neutral","long"),
    "Equity-Value":                   ("neutral","long", "neutral","long"),
    "FixedIncome-LongDuration":       ("short","short", "neutral","long"),
    "FixedIncome-TreasuryBelly":      ("short","short", "neutral","long"),
    "FixedIncome-TIPS":               ("neutral","neutral","long","neutral"),
    "FixedIncome-Munis":              ("neutral","neutral","long","long"),
    "FixedIncome-HYCredit":           ("long", "long",  "short", "short"),
    "FixedIncome-IGCredit":           ("neutral","neutral","neutral","long"),
    "FixedIncome-Convertibles":       ("long", "long",  "short", "short"),
    "FixedIncome-BDCs":               ("long", "long",  "short", "short"),
    "FixedIncome-EMDebt":             ("long", "neutral","short","short"),
    "FixedIncome-LeveragedLoans":     ("long", "long",  "short", "short"),
    "FixedIncome-Preferreds":         ("neutral","long","short","neutral"),
    "Commodity-Oil":                  ("neutral","long","long",  "short"),
    "Commodity-NatGas":               ("neutral","long","long",  "short"),
    "Commodity-Gold":                 ("neutral","long","long",  "long"),
    "Commodity-Silver":               ("neutral","long","long",  "neutral"),
    "Commodity-Copper":               ("long", "long",  "neutral","short"),
    "Commodity-BroadDBC":             ("long", "long",  "long",  "short"),
    "FX-USD":                         ("short","short", "long",  "long"),
    "FX-EUR":                         ("long", "long",  "short", "short"),
    "FX-GBP":                         ("long", "long",  "short", "short"),
    "FX-JPY":                         ("short","short", "long",  "long"),
    "FX-CAD":                         ("long", "long",  "neutral","short"),
    "FX-AUD":                         ("long", "long",  "neutral","short"),
    "FX-CHF":                         ("short","short", "long",  "long"),
    "FX-MXN":                         ("long", "long",  "short", "short"),
    "FX-CNY":                         ("neutral","neutral","neutral","neutral"),
    "Crypto":                         ("long", "long",  "long",  "short"),
    "Volatility":                     ("short","short", "long",  "long"),
}

# ─────────────────────────── Heuristic ticker → sector map ────────────────

# Reasoned best-guess sector tag per ticker. Curated 2026-05-24.
# Tickers absent from this map get inferred=pending. Operator-curated yaml
# entries always win over heuristic.
TICKER_SECTOR = {
    # Broad equity
    "SPY": "Equity-Broad", "VOO": "Equity-Broad", "IVV": "Equity-Broad",
    "QQQ": "Equity-Tech", "QQQM": "Equity-Tech",
    "IWM": "Equity-SmallCap", "VXF": "Equity-SmallCap",
    "IWR": "Equity-SmallCap", "IWD": "Equity-Value", "IWF": "Equity-Growth",
    "RSP": "Equity-Broad", "OEF": "Equity-Broad",
    "DIA": "Equity-Broad",
    "SPHB": "Equity-HighBeta", "SPLV": "Equity-LowBeta",
    "MTUM": "Equity-Momentum", "QUAL": "Equity-Quality",
    "SDY": "Equity-Dividend", "VYM": "Equity-Dividend",

    # Sector ETFs
    "XLK": "Equity-Tech", "VGT": "Equity-Tech", "SMH": "Equity-Tech",
    "XLY": "Equity-Discretionary",
    "XLC": "Equity-Comm",
    "XLI": "Equity-Industrials", "ITA": "Equity-Industrials",
    "XLB": "Equity-Materials",
    "XLRE": "Equity-REITs", "ITB": "Equity-REITs",
    "XLU": "Equity-Utilities",
    "XLP": "Equity-Staples",
    "XLV": "Equity-Healthcare", "IAK": "Equity-Financials",
    "XLF": "Equity-Financials", "KRE": "Equity-Financials",
    "XLE": "Equity-Energy", "XOP": "Equity-Energy", "OIH": "Equity-Energy",
    "XTL": "Equity-Comm",
    "XBI": "Equity-Healthcare",
    "TAN": "Equity-Tech",   # solar leans tech-discretionary; rough
    "NLR": "Equity-Utilities",   # nuclear leans utility-grid
    "SLX": "Equity-Materials",   # steel
    "ROBO": "Equity-Tech", "QTUM": "Equity-Tech",
    "IBIT": "Crypto",
    "MSOS": "Equity-Discretionary",   # cannabis ~ disc/grow

    # Single stocks — tech mega-caps
    "AAPL": "Equity-Tech", "MSFT": "Equity-Tech", "GOOGL": "Equity-Tech",
    "GOOG": "Equity-Tech", "AMZN": "Equity-Discretionary",
    "META": "Equity-Comm", "NFLX": "Equity-Comm", "TSLA": "Equity-Discretionary",
    "NVDA": "Equity-Tech", "ORCL": "Equity-Tech",
    "AMD": "Equity-Tech", "AVGO": "Equity-Tech", "ADBE": "Equity-Tech",
    "CRM": "Equity-Tech", "INTC": "Equity-Tech",
    "NET": "Equity-Tech", "DDOG": "Equity-Tech", "CRWV": "Equity-Tech",
    "NBIS": "Equity-Tech", "TWLO": "Equity-Tech", "U": "Equity-Tech",
    "PINS": "Equity-Comm", "ROKU": "Equity-Comm", "TTWO": "Equity-Comm",
    "LYV": "Equity-Discretionary", "TKO": "Equity-Comm", "FWONK": "Equity-Comm",
    "COIN": "Crypto", "HOOD": "Equity-Financials",

    # Financials single names
    "JPM": "Equity-Financials", "BAC": "Equity-Financials",
    "GS": "Equity-Financials", "MS": "Equity-Financials",
    "C": "Equity-Financials", "WFC": "Equity-Financials",
    "ALLY": "Equity-Financials", "CFG": "Equity-Financials",
    "SYF": "Equity-Financials", "CPAY": "Equity-Financials",
    "CACC": "Equity-Financials",
    "V": "Equity-Financials", "MA": "Equity-Financials", "FI": "Equity-Financials",
    "HQY": "Equity-Healthcare",

    # Consumer / retail
    "ROST": "Equity-Discretionary", "BBWI": "Equity-Discretionary",
    "PVH": "Equity-Discretionary", "PRMB": "Equity-Staples",
    "TSN": "Equity-Staples", "MNST": "Equity-Staples",
    "BTI": "Equity-Staples", "PM": "Equity-Staples",
    "EAT": "Equity-Discretionary", "BJRI": "Equity-Discretionary",
    "BLMN": "Equity-Discretionary", "CAKE": "Equity-Discretionary",
    "DAR": "Equity-Staples", "QSR": "Equity-Discretionary",
    "SBUX": "Equity-Discretionary", "MGM": "Equity-Discretionary",
    "CAVA": "Equity-Discretionary", "FIVE": "Equity-Discretionary",
    "CASY": "Equity-Staples",
    "HLT": "Equity-Discretionary", "PEB": "Equity-REITs",
    "RHP": "Equity-REITs", "ABNB": "Equity-Discretionary",
    "VIK": "Equity-Discretionary",

    # Energy single names
    "XOM": "Equity-Energy", "CVX": "Equity-Energy",
    "MTDR": "Equity-Energy", "CHRD": "Equity-Energy",
    "PR": "Equity-Energy", "OXY": "Equity-Energy",
    "PBR": "Equity-Energy", "BNO": "Equity-Energy",

    # Industrials / materials single names
    "OC": "Equity-Industrials", "OSK": "Equity-Industrials",
    "PCAR": "Equity-Industrials", "TT": "Equity-Industrials",
    "EXP": "Equity-Materials", "VMC": "Equity-Materials",
    "DOW": "Equity-Materials", "APD": "Equity-Materials",
    "MP": "Equity-Materials", "LII": "Equity-Industrials",
    "CARR": "Equity-Industrials", "BA": "Equity-Industrials",
    "RTO": "Equity-Industrials",

    # Healthcare single names
    "LLY": "Equity-Healthcare", "NVO": "Equity-Healthcare",
    "ILMN": "Equity-Healthcare", "OZEM": "Equity-Healthcare",
    "LFMD": "Equity-Healthcare", "PRVA": "Equity-Healthcare",
    "RDNT": "Equity-Healthcare", "ACHC": "Equity-Healthcare",
    "ONTO": "Equity-Tech", "TXG": "Equity-Healthcare",
    "SITM": "Equity-Tech", "SXT": "Equity-Materials",

    # Crypto-adjacent
    "GBTC": "Crypto", "ETHE": "Crypto", "BITCOIN": "Crypto",
    "BTI": "Equity-Staples",

    # Real estate / cannabis / specialty
    "TCNNF": "Equity-Discretionary", "GTBIF": "Equity-Discretionary",
    "REAL": "Equity-Discretionary",
    "SPHR": "Equity-Comm",
    "ARCO": "Equity-Discretionary",
    "CBRS": "Equity-Financials",  # unclear; leave heuristic
    "AS": "Equity-Discretionary",
    "ITB": "Equity-Discretionary",

    # International equity ETFs
    "EWC": "Equity-Broad", "EWW": "Equity-Broad",
    "EPHE": "Equity-Broad", "INDA": "Equity-Broad",
    "IDX": "Equity-Broad", "NORW": "Equity-Broad",
    "TUR": "Equity-Broad",

    # Indices (info)
    "SPX": "Equity-Broad", "COMPQ": "Equity-Tech",
    "RUT": "Equity-SmallCap", "VIX": "Volatility",
    "DAX": "Equity-Broad", "NIKK": "Equity-Broad", "SSEC": "Equity-Broad",

    # Commodities — futures + ETFs
    "DBC": "Commodity-BroadDBC", "USO": "Commodity-Oil",
    "BNO": "Commodity-Oil", "UNG": "Commodity-NatGas",
    "GLD": "Commodity-Gold", "AAAU": "Commodity-Gold", "IAU": "Commodity-Gold",
    "SLV": "Commodity-Silver", "PALL": "Commodity-Silver",  # palladium tracks
    "CPER": "Commodity-Copper", "DBB": "Commodity-Copper",
    "SOYB": "Commodity-BroadDBC",
    "WTIC": "Commodity-Oil", "BRENT": "Commodity-Oil",
    "NATGAS": "Commodity-NatGas", "GOLD": "Commodity-Gold",
    "SILVER": "Commodity-Silver", "COPPER": "Commodity-Copper",
    "ALLW": "Commodity-BroadDBC",
    "COM": "Commodity-BroadDBC",

    # Fixed income
    "AGG": "FixedIncome-IGCredit", "BND": "FixedIncome-IGCredit",
    "TLT": "FixedIncome-LongDuration", "ZROZ": "FixedIncome-LongDuration",
    "IEF": "FixedIncome-TreasuryBelly", "IEI": "FixedIncome-TreasuryBelly",
    "TLH": "FixedIncome-TreasuryBelly",
    "SHY": "FixedIncome-TreasuryBelly", "STIP": "FixedIncome-TIPS",
    "TIP": "FixedIncome-TIPS", "LQD": "FixedIncome-IGCredit",
    "HYG": "FixedIncome-HYCredit", "JNK": "FixedIncome-HYCredit",
    "MBB": "FixedIncome-IGCredit", "MUB": "FixedIncome-Munis",
    "BAB": "FixedIncome-Munis", "BKLN": "FixedIncome-LeveragedLoans",
    "BIZD": "FixedIncome-BDCs", "CWB": "FixedIncome-Convertibles",
    "PFF": "FixedIncome-Preferreds",
    "EMB": "FixedIncome-EMDebt", "EMLC": "FixedIncome-EMDebt",
    "UST30Y": "FixedIncome-LongDuration",
    "UST10Y": "FixedIncome-TreasuryBelly",
    "UST2Y": "FixedIncome-TreasuryBelly",
    "FDRXX": "FixedIncome-TreasuryBelly",
    "BUXX": "FixedIncome-TreasuryBelly",
    "PFIX": "FixedIncome-LongDuration",  # rate-vol; inverted in Q2

    # FX (alerts go through equity wrappers + crypto)
    "UUP": "FX-USD", "USD": "FX-USD",
    "DXY": "FX-USD",
    "FXE": "FX-EUR", "EUR/USD": "FX-EUR",
    "FXB": "FX-GBP", "GBP/USD": "FX-GBP",
    "FXA": "FX-AUD",
    "FXY": "FX-JPY", "USD/YEN": "FX-JPY",
    "FXC": "FX-CAD", "CAD/USD": "FX-CAD",
    "FXF": "FX-CHF", "FXM": "FX-MXN",
    "CYB": "FX-CNY",

    # Volatility / hedges
    "EQRR": "Equity-Financials",   # rates-sensitive — guess
    "GII": "Equity-Industrials",
    "DRAM": "Equity-Tech",
    "TSSI": "Equity-Tech", "OAEM": "Equity-Tech",   # AI-themed guess
    "CLOX": "FixedIncome-LeveragedLoans",
    "CLOZ": "FixedIncome-LeveragedLoans",
    "TILL": "FixedIncome-TIPS",
    "CNXT": "Equity-Tech",
    "ATRO": "Equity-Industrials",
    "ATZAF": "Equity-Materials",
    "SLX": "Equity-Materials",
    "FYBR": "Equity-Comm",
    "ITT": "Equity-Industrials",
}


# ─────────────────────────── Universe assembly ─────────────────────────────

def _load_doctrine() -> dict:
    with open(_DOCTRINE_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _doctrine_quad_sides() -> dict[str, dict[str, str]]:
    """Walk quad_universe and produce {ticker: {quad_1:long, quad_2:short, ...}}
    using the doctrine's own long/short lists per Quad. Tickers not in a side
    for a Quad default to 'neutral' for that Quad."""
    d = _load_doctrine()
    qu = d.get("quad_universe") or {}
    out: dict[str, dict[str, str]] = {}
    for quad_label, block in qu.items():
        # quad_label = "Quad 1" → quad_1
        try:
            n = int(quad_label.split()[-1])
        except Exception:
            continue
        qk = f"quad_{n}"
        for side, side_block in (block or {}).items():
            if side not in ("longs", "shorts"):
                continue
            sign = "long" if side == "longs" else "short"
            for _asset_class, tickers in (side_block or {}).items():
                for t in tickers or []:
                    tu = str(t).upper()
                    out.setdefault(tu, {})
                    out[tu][qk] = sign
    # Fill in missing Quads as neutral so every doctrine ticker has all 4 sides.
    for tu, sides in out.items():
        for qk in QUAD_KEYS:
            sides.setdefault(qk, "neutral")
    return out


def _price_monitor_universe() -> list[str]:
    """Tickers in price_monitor.HEDGEYE_TO_YFINANCE — the legacy live universe."""
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE  # type: ignore
        return list(HEDGEYE_TO_YFINANCE.keys())
    except Exception:
        return []


def _watchlist_universe() -> list[str]:
    """Operator's MFR account watchlist via mfr_client.list_watchlist().

    Added 2026-05-24 to close the loop on the fan-out fix (commit b89be88):
    tickers the operator drops into his MFR account should auto-classify
    on the next quad-map rebuild instead of needing manual entry.

    Returns the sorted distinct ticker list. Empty list on API error /
    missing MFR_API_TOKEN.
    """
    try:
        import mfr_client
        return mfr_client.list_watchlist()
    except Exception as e:
        log.warning("MFR watchlist universe fetch failed (continuing without): %s", e)
        return []


def _db_universe() -> list[str]:
    """UNION of hedgeye_* product tables, recent window. Empty list on DB error."""
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return []
    try:
        import psycopg2
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ticker FROM hedgeye_risk_ranges        WHERE signal_date    >= CURRENT_DATE - INTERVAL '30 days'
                UNION SELECT DISTINCT ticker FROM hedgeye_signal_strength    WHERE snapshot_date  >= CURRENT_DATE - INTERVAL '30 days'
                UNION SELECT DISTINCT ticker FROM hedgeye_portfolio_solutions WHERE snapshot_date >= CURRENT_DATE - INTERVAL '30 days'
                UNION SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges     WHERE week_of        >= CURRENT_DATE - INTERVAL '60 days'
                UNION SELECT DISTINCT ticker FROM hedgeye_investing_ideas    WHERE snapshot_date  >= CURRENT_DATE - INTERVAL '90 days'
            """)
            return [r[0] for r in cur.fetchall() if r[0]]
    except Exception as e:
        log.warning("DB universe fetch failed (continuing without): %s", e)
        return []


def _heuristic_classify(ticker: str) -> Optional[tuple[dict[str, str], str]]:
    """Return ({quad_1: long/short/neutral, ...}, sector) for `ticker`,
    or None if no heuristic applies. Sector comes from TICKER_SECTOR; the
    quad sides come from SECTOR_QUAD_RULES."""
    sec = TICKER_SECTOR.get(ticker.upper())
    if not sec:
        return None
    rule = SECTOR_QUAD_RULES.get(sec)
    if not rule:
        return None
    return {qk: side for qk, side in zip(QUAD_KEYS, rule)}, sec


# ─────────────────────────── Merge with existing yaml ──────────────────────

def _load_existing() -> dict:
    if not _MAP_PATH.exists():
        return {"tickers": {}, "meta": {}}
    try:
        with open(_MAP_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as e:
        log.warning("existing %s unparseable, starting fresh: %s", _MAP_PATH.name, e)
        return {"tickers": {}, "meta": {}}
    data.setdefault("tickers", {})
    data.setdefault("meta", {})
    return data


def _source_for(meta_entry: dict) -> str:
    return (meta_entry or {}).get("source") or "inferred=pending"


def _better(new_src: str, existing_src: str) -> bool:
    return _SOURCE_ORDER.get(new_src, 0) > _SOURCE_ORDER.get(existing_src, 0)


# ─────────────────────────── Build ──────────────────────────────────────────

def build(include_db: bool = False, infer_with_claude: bool = False,
          include_watchlist: bool = False) -> dict:
    existing = _load_existing()
    pre_tickers = set((existing.get("tickers") or {}).keys())
    tickers_out: dict = dict(existing.get("tickers") or {})
    meta_out:    dict = dict(existing.get("meta")    or {})

    # 1. Doctrine seed (high confidence — long/short lists are Hedgeye's own).
    doc_sides = _doctrine_quad_sides()
    for tu, sides in doc_sides.items():
        existing_src = _source_for(meta_out.get(tu))
        if existing_src == "operator":
            continue
        if _better("seed=doctrine", existing_src):
            entry = {**sides}
            # Try to attach a sector tag from the heuristic map for context.
            sec = TICKER_SECTOR.get(tu)
            if sec:
                entry["sector"] = sec
            tickers_out[tu] = entry
            meta_out[tu] = {"source": "seed=doctrine"}

    # 2. Build the working universe: doctrine + price_monitor + heuristic
    #    map + (optional) DB recent UNION + (optional) MFR account watchlist.
    universe: set[str] = set(doc_sides.keys())
    universe.update(t.upper() for t in _price_monitor_universe())
    universe.update(TICKER_SECTOR.keys())
    if include_db:
        universe.update(_db_universe())
    if include_watchlist:
        universe.update(_watchlist_universe())

    # 3. Heuristic pass on anything still pending.
    pending: list[str] = []
    for tu in sorted(universe):
        existing_src = _source_for(meta_out.get(tu))
        if existing_src in ("operator", "seed=doctrine", "inferred=claude"):
            continue
        result = _heuristic_classify(tu)
        if result is None:
            if tu not in tickers_out:
                # Brand-new ticker with no heuristic — mark pending all-neutral.
                tickers_out[tu] = {qk: "neutral" for qk in QUAD_KEYS}
                meta_out[tu] = {"source": "inferred=pending"}
            pending.append(tu)
            continue
        sides, sec = result
        if _better("seed=heuristic", existing_src):
            tickers_out[tu] = {**sides, "sector": sec}
            meta_out[tu] = {"source": "seed=heuristic", "sector_inferred": sec}

    # 4. Optional Claude pass on still-pending tickers.
    if infer_with_claude and pending:
        try:
            updates = _infer_pending_with_claude(pending)
            for tu, payload in updates.items():
                sides, sec = payload
                tickers_out[tu] = {**sides, "sector": sec}
                meta_out[tu] = {"source": "inferred=claude", "sector_inferred": sec}
        except Exception as e:
            log.warning("Claude inference pass failed (keeping pending): %s", e)

    # Persist sorted for easy diffs.
    out = {
        "tickers": {k: tickers_out[k] for k in sorted(tickers_out)},
        "meta":    {k: meta_out[k]    for k in sorted(meta_out)},
    }
    _MAP_PATH.write_text(
        "# config/mfr_quad_map.yaml — per-Quad ticker classification.\n"
        "# Built by tools/build_mfr_quad_map.py. Operator-curated entries\n"
        "# (meta source=operator) win over rebuilds.\n"
        "#\n"
        "# Sides per Quad: long | short | neutral.\n"
        f"# Generated 2026-05-24. Tickers: {len(out['tickers'])}.\n\n"
        + yaml.safe_dump(out, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    post_tickers = set(out["tickers"].keys())
    added = sorted(post_tickers - pre_tickers)
    return {
        "total":     len(out["tickers"]),
        "doctrine":  sum(1 for v in meta_out.values() if v.get("source") == "seed=doctrine"),
        "heuristic": sum(1 for v in meta_out.values() if v.get("source") == "seed=heuristic"),
        "claude":    sum(1 for v in meta_out.values() if v.get("source") == "inferred=claude"),
        "pending":   sum(1 for v in meta_out.values() if v.get("source") == "inferred=pending"),
        "operator":  sum(1 for v in meta_out.values() if v.get("source") == "operator"),
        "added":     added,
    }


# ─────────────────────────── Claude inference pass ─────────────────────────

_CLAUDE_RUBRIC = """You are classifying financial tickers for a Hedgeye Quad
rotation model. Each Quad represents a growth/inflation regime:

  Quad 1 — Goldilocks       (growth ↑, inflation ↓): long growth/tech/discretionary,
                                                       short defensives/staples/utilities
  Quad 2 — Reflation         (growth ↑, inflation ↑): long cyclicals/energy/materials/banks,
                                                       short rates-sensitive/staples/utilities
  Quad 3 — Stagflation       (growth ↓, inflation ↑): long gold/energy/staples/REITs,
                                                       short growth/discretionary/banks
  Quad 4 — Deflation         (growth ↓, inflation ↓): long bonds/staples/utilities,
                                                       short cyclicals/energy/banks

For each ticker, output a JSON object:
  {"ticker": "<TICKER>",
   "sector": "<short tag, e.g. Equity-Tech / Equity-Energy / Commodity-Gold>",
   "quad_1": "<long|short|neutral>",
   "quad_2": "<long|short|neutral>",
   "quad_3": "<long|short|neutral>",
   "quad_4": "<long|short|neutral>"}

Rules:
  - Be CONSERVATIVE. Use "neutral" when unsure.
  - If you don't recognize the ticker, return neutral on all four Quads and
    set sector to "Unknown".
  - Do not invent sectors that aren't in the rubric's spirit.

Return a single JSON array of objects, one per ticker, in input order. No
commentary, no markdown fences.
"""


def _infer_pending_with_claude(tickers: list[str]) -> dict[str, tuple[dict, str]]:
    """Batched Claude call to classify the pending tickers. Returns a dict
    {ticker: (sides_dict, sector_tag)}. Errors return {}. Uses Haiku 4.5 —
    cheap, good enough for this label task.

    Batch size: 50 tickers per call. With <300 tickers total this caps at
    ~6 calls, well under the 30-minute budget.
    """
    import json
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set; skipping Claude inference pass.")
        return {}
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed; skipping Claude pass.")
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    out: dict[str, tuple[dict, str]] = {}
    BATCH = 50

    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        user_msg = (
            f"Classify these {len(batch)} tickers per the rubric:\n"
            + ", ".join(batch)
        )
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4000,
                system=_CLAUDE_RUBRIC,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = "".join(getattr(b, "text", "") for b in resp.content
                          if getattr(b, "type", None) == "text")
            # Extract first JSON array
            start = raw.find("[")
            end = raw.rfind("]")
            if start < 0 or end <= start:
                log.warning("Claude returned no JSON array for batch %d-%d",
                            i, i + len(batch))
                continue
            arr = json.loads(raw[start:end + 1])
            for item in arr:
                t = (item.get("ticker") or "").upper()
                if not t:
                    continue
                sides = {qk: (item.get(qk) if item.get(qk) in SIDES else "neutral")
                         for qk in QUAD_KEYS}
                sec = item.get("sector") or "Unknown"
                out[t] = (sides, sec)
        except Exception as e:
            log.warning("Claude batch %d-%d failed: %s", i, i + len(batch), e)
            continue
    return out


# ─────────────────────────── CLI ────────────────────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.build_mfr_quad_map")
    ap.add_argument("--include-db", action="store_true",
                    help="Also union the universe with the recent hedgeye_* "
                         "product tables from Railway Postgres "
                         "(requires DATABASE_PUBLIC_URL / DATABASE_URL).")
    ap.add_argument("--include-watchlist", action="store_true",
                    help="Also union the universe with the operator's MFR "
                         "account watchlist via mfr_client.list_watchlist() "
                         "(requires MFR_API_TOKEN). Picks up tickers the "
                         "operator added through the MFR UI.")
    ap.add_argument("--infer-with-claude", action="store_true",
                    help="Run a Haiku-4.5 batched classification pass over "
                         "tickers still tagged inferred=pending after the "
                         "deterministic seed + heuristic pass.")
    ap.add_argument("--merge", action="store_true",
                    help="No-op alias: merge semantics are the DEFAULT "
                         "behavior — every rebuild loads the existing yaml "
                         "and only overwrites entries with a strictly "
                         "higher-confidence source (operator > doctrine > "
                         "claude > heuristic > pending). Operator-curated "
                         "entries (meta source=operator) are always "
                         "preserved.")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    summary = build(include_db=a.include_db,
                    infer_with_claude=a.infer_with_claude,
                    include_watchlist=a.include_watchlist)
    added = summary.pop("added", [])
    print("mfr_quad_map.yaml built:")
    for k, v in summary.items():
        print(f"  {k:9} = {v}")
    if added:
        print(f"  new      = {len(added)} ticker(s): {', '.join(added)}")
    else:
        print("  new      = 0 (no new tickers vs prior yaml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
