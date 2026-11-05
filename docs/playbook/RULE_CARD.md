# Rule card (Kris, confirmed 11/1/2026)

Source of truth for every desk call. Built on Keith's *Master The Market* (ch. 3–4) plus Kris's rulings.
"Laggard / rotate out" never overrides it. Tools: `tools/desk_rot.py`, `tools/desk_shorts.py`, `tools/desk_sizing.py`, `tools/range_structure.py`.

## Lines
- **TRADE = Vola2vola Trade line (Kris, 11/5/2026). TREND = 89-day EMA of the daily close (Kris, 11/2/2026).** On every position, long or short. When Keith publishes a level, his number wins. The 34-day EMA is the Trade fallback when a name has no Vola bar from the last 5 days. The MFR Trend band is not an exit line.
- **A range low or high is never a Trade or Trend line (Kris, 11/4/2026).** Quote ranges as context only; a break needs his stated line, Alpha Code, Vola Trade, or the EMA.
- rp = (price − range low) / (range high − range low).
- **Range structure** = today's risk range vs 5 sessions ago: HH/HL (higher high, higher low), LH (lower high), etc.
- Breaks are called at the **3:45 PM check if still through the line** (closing prices matter).
- **Which level (Kris, 11/2):** Keith's published number first (Alpha Code, monthly Trend & Tail email), then his ranges (ETF Pro, Risk Range), then Vola Trade and the EMAs. An ETF can use a close proxy's levels (SCHA uses IWM). On Vola Trade or the EMA alone, call a break only when price is **about 2% through**: the buffer stays at 2% until Kris changes it.

## Position size: MIN / MAX (% of that account)
| Asset class | MIN | MAX |
|---|---|---|
| Commodities & crypto (BNO, HGER, BWET, gold, BTC) | 1% | 4% |
| Equity ETFs (sector, country, index) | 2% | 6% |
| Fixed income & currency ETFs (TLT, CLOX, ILS, UUP, FXY) | 3% | 10% |
| Single-stock longs | 1% | 3% |
| Shorts (incl. 1x inverse ETFs) | 0.5% | 2% |
- Cash parking (SPAXX, FDRXX, BUXX) is not sized.
- **Buy and sell in increments of 50–150 bp of the account**: bigger near the range low, smaller near the top.
- **Build positions incrementally over several days** (Kris never goes straight to MIN). A position under MIN that is still being built is normal: the desk shows the next tranche, not a violation.
- One ticker, one account: don't split a position across accounts (FXY lesson).
- "To minimum" means the class MIN above. A position already at MIN that breaks trade is held to trend.

## Longs — when to get out
1. **Lower high in the risk range → start reducing size** (step down 50 bp), before any line breaks.
2. Lower high **and** below TRADE → cut **to MIN**; no adds.
3. Below TREND → **out**.
4. **At MAX: −6% stop** from cost → out.
5. rp > 1 → trim.

## Longs — when to buy the dip
- **Best:** bullish TRADE and TREND, **higher highs and higher lows** in the range, near the range low (rp ≤ 0.35). Full increment.
- **Weaker:** bullish TRADE and TREND but **lower highs** with higher lows → smaller increment, bottom of the book.
- Plus: 3+ day selloff on falling volume (< 20d avg), buy on a RED day.
- Trade breaking → add less aggressively. Trade broken and trend break implied → **do not buy**.
- No new position within 2 days of earnings.

## Shorts (Individual only)
- Entry: bearish trend, 3+ day bounce on falling volume, rp ≥ 0.65, below trade, short on a GREEN day. Lower highs in the range = best.
- Above TRADE → cover half. Above TREND → cover all.
- Cover ⅓–½ at +3–5% or rp ≤ 0.25; another ⅓ at the range low; last ⅓ to the trade line.
- One add max. Size 0.5–2% of the account (Individual: ~$145–575).
- At MAX: −6% stop → cover.

## Signal Strength / Position Monitor
- **Removed from Signal Strength (same side) → out of the book**, long or short, and off the stalk list.
- **Exception: on the Position Monitor** (any bucket, same side) → keep, manage by the lines.

## Exceptions
- **FXY (yen)** — safe haven, still building to size. Not exited on a trend break.

## Regime
- Use Quad 2 strength to build a Quad 4 book: long staples, healthcare, utilities, TLT, UUP; short tech/semis, AI, industrials, small caps, credit, high beta.
- Countries: Quad 1/2 + bullish index = long candidate; Quad 3/4 + bearish = short candidate; signals win on conflict.
- IRAs short indexes only via 1x inverse ETFs (PSQ, SH, RWM).
