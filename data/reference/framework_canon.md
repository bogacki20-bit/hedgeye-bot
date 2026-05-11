# Framework Canon — the bot's operating manual

This file is the single authoritative reference the decision engine consults
on every call. It distills three layers — Hedgeye (regime), SpotGamma
(tactical levels), VolSignals / Natenberg (market-maker mechanics) — into
the rules the bot uses to translate context into a sized trade decision.

**Edits here propagate immediately** — `decision_engine.py` reads this file
into the prompt on every call. No code changes needed to update the bot's
reasoning.

---

## Layer 0 — Keith McCullough's trading doctrine (Hedgeye University)

These are the discipline rules drilled into every HU lesson — *how* to trade,
not what to trade. The lower layers tell you the trade; this layer tells you
how to express it. Citations are from the HU course (Chapters 2-4) — every
rule below traces back to a specific Keith quote in
`data/snapshots/hedgeye/2026-05-09/university/framework_quotes_compiled.md`.

1. **At the top of the range you sell. At the bottom of the range you buy.
   You're fading the market.** (Ch2 L6 — "It's simple.")
   - The Risk Range is a *fade* tool, not a momentum tool.
   - High conviction = framework-aligned BUY near the bottom edge.
   - Low conviction = framework-aligned BUY near the top edge (basically don't).

2. **The Signal front-runs the Quad.** (Ch3 L1 — OODA Loop lesson)
   - Risk Range Signal (price + volume + volatility) is fast-twitch.
   - Quad regime is slow-twitch — it's the cap, not the trigger.
   - Tactical positioning responds to Signal first; the Quad sets the bias.

3. **Buy and sell incrementally — never all at once.** (Ch3 L4 — "Strategy & Tactics")
   - Pre-set your minimum and maximum size before entering.
   - Build positions in increments. A single BUY signal should NEVER take you
     from 0 → full position.
   - Same rule for trimming: incremental, not all-out.
   - This is the foundation of the 100 bps starter / 50 bps adds / $1K per-fill
     ceiling sizing framework.

4. **Risk Range Signal = price + volume + volatility (three factors).** (Ch2 L5)
   - Price alone is the weakest of the three.
   - Volume confirms; volatility regime-shifts.
   - Currently the bot only ingests price levels — volume + volatility
     remain enrichment work (note in punch-list).

5. **VIX buckets gate aggression** (Ch3 L5 — "Fear & Greed: The Buckets of Volatility")
   - VIX 10–19 ("Investable") — full long-bias sizing.
   - VIX 20–30 ("Chop") — trader mode; smaller sizes; fade the edges aggressively.
   - VIX > 30 ("F#$k") — defensive only; trim toward cash; only highest-conviction names.

6. **Realized vs. implied vol is the fear/greed gauge.** (Ch4 L3)
   - IV premium (IV > RV) = rising fear, options bidding up = often a setup
     for framework-aligned longs to add.
   - IV discount (IV < RV) = complacency = caution on longs near range top.
   - The SpotGamma context block already exposes IV/RV — use it as a vote.

7. **The Setup of the Decade: when Wall Street consensus disagrees with the Quads.**
   (Ch4 L2)
   - When consensus crowds one direction and Quad direction says the opposite,
     that's the highest-conviction setup.
   - This is future enrichment work; flag if/when consensus data lands.

8. **Embrace uncertainty; right two-thirds of the time is "really good."** (Ch3 L1)
   - The bot should not aim for 100% accuracy. It should aim for systematic
     application of the framework, and trust the math of incremental sizing
     to dampen the misses.

9. **The Macro notebook is the moat.** (Ch2 L7)
   - Every alert, decision, and outcome captured = the framework sharpens
     over time. The `trade_recommendations` + `alerts_fired` + `user_actions`
     + `outcome_followups` tables ARE that notebook.

10. **OODA Loop is the decision framework.** (Ch3 L1)
    - Observe (gather context) → Orient (synthesize through canon) → Decide
      (this engine's output) → Act (Telegram alert + user reply).
    - Every scan is one OODA cycle.

---

## Layer 1 — Hedgeye (regime / macro / sector)

The Hedgeye GIP (Growth-Inflation-Policy) model is the **regime anchor**.
Every trade must be classifiable as framework-aligned, framework-against,
or neutral relative to the current Quad.

The full Quad → sector mapping and VIX-bucket rules are encoded in the
`decision_engine.py` system prompt. This canon does not duplicate them.
Key principle: **Quad-alignment is the first gate**. A framework-against
trade must clear a much higher bar (multi-source flow confirmation) before
sizing above Monitor.

### Hedgeye range hierarchy (master → secondary → MFR-as-tertiary)

The bot has THREE possible range sources. Use them in strict priority order
— never override a higher-priority source with a lower one:

1. **Hedgeye Risk Range** (daily RTA, table `hedgeye_risk_ranges`, fields
   `buy_trade` / `sell_trade` / `trend`) — **PRIMARY**. Keith's daily
   signal output. If present, this IS the range.
2. **Hedgeye ETF Pro Range** (Monday email, weekly cadence, 18 tickers,
   table `hedgeye_etf_pro_ranges`, fields `range_low` / `range_high`) —
   **SECONDARY**. Use when the daily Risk Range is missing for an ETF Pro
   ticker. Treat as fresh while `week_of >= today - 7 days`.
3. **MFR fractal range** (table `mfr_snapshots`, fields `range_low` /
   `range_high`) — **TERTIARY**. MFR has ranges but they are weaker than
   Hedgeye's. Only use MFR's range numbers when both Hedgeye sources are
   missing. MFR's other outputs (Hurst, trend_signal) remain a confirmation
   vote independent of which range source is active.

**INVIOLABLE conviction cap when Hedgeye range is missing**: if neither
Risk Range nor ETF Pro Range is available for a ticker, the bot **must not**
size above **Adding (50 bps)** regardless of how strong every other layer
aligns, regardless of which HU doctrine could be cited. Hedgeye is the
master signal source; trading without it is by definition off-process.
The model must downshift any "Best Idea / 100 bps" intent to "Adding / 50 bps"
when this condition is met — no override, no exception, no "framework alignment
is so strong it justifies" carve-outs. This rule exists precisely because
the carve-outs are tempting.

### Risk Range zones (applies to any of the three sources above)

- Below low boundary — opportunity for framework-aligned longs; caution for
  framework-aligned shorts (you're already short into support).
- Bottom third — scale-in zone for longs; entry for shorts is premature here.
- Middle third — no-trade zone; let the tape develop.
- Top third — trim zone for longs; entry zone for shorts.
- Above high boundary — trim aggressively or short.

**Conviction translation** (Hedgeye → bot):
- "Best Idea" → 100 bps starter (or 100 bps add if already in)
- "Adding" → 50 bps add
- "Reducing" → trim 50% of current position
- "Remove" → close entire position
- Anything without an explicit conviction → Monitor

---

## Layer 2 — SpotGamma (tactical levels)

SpotGamma data describes **where dealer hedging will react**. It does not
generate the trade — Hedgeye does. SpotGamma tells you **at what level**
the bot should act.

### The six core signals
1. **Market Maker (MM)** — the dealer hedging your options. Their hedging
   activity moves the underlying.
2. **Delta** — the share count MM needs to hedge an option *now*.
3. **Gamma** — how that hedge requirement changes as price/time move.
4. **Call Wall** — strongest resistance strike. Holds intraday in ~83%
   of S&P sessions.
5. **Put Wall** — strongest support strike. Holds intraday in ~89% of
   S&P sessions.
6. **Vol Trigger** — regime separator. Above it = positive-gamma regime
   (MM dampens moves, lower realized vol). Below it = negative-gamma
   regime (MM amplifies moves, higher realized vol, ~40% higher daily
   realized vol on average).

### Decision rules — SpotGamma layer
- **Call Wall held + framework-aligned short** → strong shorting signal at the wall.
- **Put Wall held + framework-aligned long** → strong scale-in signal at the wall.
- **Below Vol Trigger** → expect amplified moves. Cut starter size in half
  (50 bps not 100 bps) unless framework alignment is extreme.
- **Above Vol Trigger** → MM is dampening. Normal sizing.
- **Hedge Wall** — usually far-OTM. Treat as a tail-risk marker, not
  actionable level.
- **Negative net gamma regime** + framework-aligned trade → size **lower**
  (vol amplification cuts both ways); wait for clearer level.

### SpotGamma's 4-step daily workflow (encoded for use by the scanner)
1. **Composite view** — confirm options are driving the name (red/green
   shading intensity = options influence).
2. **Put/Call impact chart** — flat curve zones = low expected vol; steep
   zones = high.
3. **10-day key-level history** — Call Wall / Put Wall / Hedge Wall trend:
   trending **up** = constructive bullish; **down** = constructive bearish.
4. **SG Levels + live price** — measure distance to nearest key level.
   Closer = imminent reaction; farther = room to run.

### Compass (cross-sectional positioning)
Compass plots names on two axes — bullish/bearish positioning vs.
expensive/cheap options. Scanner mode (Slice 0d) should use Compass-style
filters: find names that are **bullish-positioned + cheap options** (long
candidates) or **bearish-positioned + expensive options** (short candidates).

---

## Layer 3 — VolSignals / Natenberg (market-maker mechanics)

This layer explains **why** SpotGamma levels matter. The decision engine
needs to know that:

- **Positive gamma exposure for dealers** → they sell rallies and buy dips
  to stay hedged → realized vol gets dampened → mean-reverting tape.
- **Negative gamma exposure for dealers** → they buy rallies and sell dips
  to stay hedged → realized vol gets amplified → trending tape.
- **Charm** — delta decays toward 0 (for OTM) or +/-1 (ITM) as expiry
  approaches. Friday afternoon charm flows pin SPX to high-OI strikes.
- **Vanna** — delta sensitivity to implied vol. Falling IV → calls lose
  delta, puts gain delta → dealer rebalancing creates lift in equities
  when vol crushes.
- **Pin risk** — at expiry, high-OI strikes act as magnets if price is
  within ~0.5% by 3 PM ET.

### Practical bot rules
- Friday after 12 PM ET + spot within 0.5% of high-OI strike → expect pin.
  Do not chase moves; either trade the pin or stand aside.
- IV crush event (post-Fed, post-CPI) + framework-aligned long → expect
  vanna lift the next session; favorable add window.
- 0DTE positioning matters in S&P names but not in single stocks. SPX, SPY,
  QQQ decisions must check 0DTE GEX in addition to all-expirations GEX.

---

## Decision synthesis — the master rule

The 5-step algorithm in `decision_engine.py` is the canonical decision
procedure. Restated for clarity:

1. **Framework alignment** (Quad gate) — if framework-against, default to Monitor.
2. **Risk Range zone** — must be in a zone that justifies the action.
3. **SpotGamma corroboration** — wall/trigger position must support, or
   at minimum not contradict, the Hedgeye thesis.
4. **MFR corroboration** — Hurst trend + range position add a third
   independent vote.
5. **Synthesis** —
   - 4/4 agree → **Best Idea, 100 bps**
   - 3/4 agree → **Adding, 50 bps**
   - 2/4 or sharp disagreement → **Monitor**
   - Risk Range missing → **Monitor** (Hedgeye is foundational)
   - Account value $0 or unknown → **Monitor**

**Sizing ceiling**: any single fill is capped at $1,000 regardless of bps math.
Build positions in legs.

**Negative-gamma override**: when below Vol Trigger, halve the starter
size (50 bps not 100 bps) unless 4/4 agreement is unusually strong.

---

## What this canon is NOT

- Not a replacement for the dynamic context blocks (Hedgeye Risk Range,
  SpotGamma latest, MFR Hurst, Yahoo price, corpus FTS snippets). Those
  carry the *current state*. The canon carries the *rules for interpreting*
  that state.
- Not a duplicate of the Quad → sector table in the system prompt. That
  table is authoritative there; this canon points to it.
- Not static. Add new rules here as they're learned (from Hedgeye U,
  SpotGamma updates, VolSignals research, Portfolio Solutions patterns).

---

## Change log

- 2026-05-10 — Initial canon. Three-layer architecture (Hedgeye / SpotGamma /
  VolSignals-Natenberg) + decision synthesis rule. Built from 107 SpotGamma
  course lessons + 6 manually-authored signal definitions + VolSignals
  transcripts already in corpus.
