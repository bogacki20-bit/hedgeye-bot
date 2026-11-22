# Rule card (Kris; rewritten 11/14/2026, plus audit round 3 rulings the same evening; SpotGamma rules 11/15; VXN scope 11/16; VIX 30+ ruling 11/20; short press, options note, stop candidate and 2-day range rule 11/21)

This is the source of truth for every desk call. It is built on Keith's *Master The Market* (ch. 3–4), his Real-Time Alerts, and Kris's rulings.
"Laggard / rotate out" or a macro view never overrides it.
Previous version: `archive/RULE_CARD_before_2026-11-14.md`.
Tools: `tools/desk_rot.py`, `tools/desk_shorts.py`, `tools/desk_sizing.py`, `tools/range_structure.py`, `tools/level_lines.py`.

**The core idea (Kris 11/14):** recycle capital fast, like Keith's Real-Time Alerts. He books some on a long the next green day (COST +1.2%, LMT +1.4%). He covers some on a short 1–2 days after entry, at a 1–6% gain. He cuts shorts that go against him (ARES, FSK, CHTR) before any Trend break. We never ride a long to the top of its range or a short to the bottom.

## Lines
- **TRADE = Vola2vola Trade line. TREND = the yellow MFR cloud: bottom edge for longs, top edge for shorts.**
  - Keith's published number wins: Alpha Code, then the monthly Trend & Tail email, then ETF Pro / Risk Range.
  - **His numbers are good for one week (Kris 11/14).** A Keith Trade or Trend level more than 7 days old no longer wins; the cloud (Trend) and Vola (Trade) take precedence until he publishes a newer one.
  - The 34-day EMA is the Trade fallback when a name has no Vola bar from the last 5 days.
  - The 89-day EMA is the Trend fallback only when a name has no cloud.
- **Breaks are called at the 3:45 PM check** if price is still through the line. Exception (audit 11/14): a cloud (Trend) break that is 1%+ through after 2:00 PM can be acted on before 3:45.
- **2% buffer** on Vola Trade or an EMA only: call a break only when price is about 2% through. **No buffer on the cloud** (Kris 11/14): a 3:45 close through its edge counts.
- **A range low or high is never a Trade or Trend line.** Ranges are context and rp only.
- **rp** = (price − range low) / (range high − range low).
  - **Which range (Kris 11/21):** a name that gets a new range from Hedgeye every day uses that one first: the Risk Range, and the Crypto Quant tracker for crypto names (MARA, COIN, MSTR, IBIT, ETHA). A name that gets one once a week on Monday (Investing Ideas, ETF Pro) uses it Monday and Tuesday; **from Wednesday rp uses MFR's range**, then TradingView's. A weekly range that a dashboard re-shows each day is still the Monday range. Every rp shows its source and date.
- **Range structure** = today's range vs 5 sessions ago: HH/HL, LH, etc.
- **Proxies:** an ETF can use a close proxy's levels (SCHA uses IWM). **Read price and every line on the proxy alone; never mix the two** (Kris 11/14).
- **Formations:**
  - Bullish = price > Trade > Trend > Tail: buy the dips.
  - Bearish = the mirror: short strength.
  - The stated Trend signal outranks one rally. `py -m tools.formations`.

## Position size (% of that account)
| Asset class | MIN | MAX |
|---|---|---|
| Commodities & crypto (BNO, HGER, BWET, silver, platinum, BTC) | 1% | 4% |
| Gold ETFs: bullish on Trend (above the cloud) | 3% | 12% |
| Gold ETFs: below the cloud | 1% | 4% |
| Equity ETFs (sector, country, index) | 2% | 6% |
| Fixed income ETFs (TLT, CLOX, ILS) | 3% | 10% |
| Currency ETFs (UUP, FXY) | 3% | 12% |
| Single-stock longs | 1% | 3% |
| Shorts, incl. 1x inverse ETFs held long | 0.5% | 2% |
- **Gold sizes like a currency (Keith, Q&As 2/24, 3/31, 6/30, 8/25; Kris 11/14):** 3%–12% while it's bullish on Trend. Below its cloud it drops to the commodity band, and it goes back to 3% when it reclaims the cloud. Silver and platinum always stay commodities.
- **The cap is on size, not on the number of positions (Kris 11/14).** No limit on how many names. **Size ceiling:** a position that grows past its class MAX gets trimmed back to MAX on the next green day, whatever the rp.
- Cash parking (SPAXX, FDRXX, BUXX) is not sized.
- **Everything is incremental: longs and shorts both (Kris 11/14; Keith says it repeatedly).** Never open at full size. **A new position opens AT the class MIN in one buy** (audit 11/14), then builds over several days.
  - Steps are 50–150 bp: bigger near the range low, smaller near the top.
  - Never add to a position that's going against you. Keith's "2-dot" add into a loser is his tactic for coaching his way out of a losing position (getting it off the screen), not a build; his doubled trades win only 19–31%. We don't copy it: our stops handle losers.
- One ticker, one account. Splits that already exist (TLT, BNDD 11/12) are managed by the same lines; flag a split only on a NEW buy.

## Longs: when to buy
- **Best:** bullish Trade and Trend, higher highs and higher lows in the range, rp ≤ 0.35. Use a full step.
- **Weaker:** bullish but lower highs with higher lows. Use a smaller step.
- Buy after a 3+ day selloff on falling volume, on a RED day. Never chase a green day.
- **Falling volume = selling exhaustion (Kris 11/14):** volume shrinking day over day across the down days, today compared at the same clock time. The 20-day average does NOT matter.
- Distance past Trade is never a reason to pass; low in the range is the buy (11/5).
- **Never buy the top of the range (Kris 11/14):** no buys or adds at rp 0.85+ or above the range top.
- Trade breaking → add less. Trade broken with a Trend break implied → don't buy.
- No new position within 2 days of earnings.
- **Never buy something purely for its dividend (Kris 11/14).** A dividend or yield is not a risk-management strategy. Every buy needs the signals above, and dividend capture is never a reason to enter or hold. **Only exception: BUXX** (cash parking; buy the ex-dividend dip).

## Longs: recycle on green (Kris 11/14)
- On any green day when price is **0.5% or more above what you paid, OR high in its range (rp 0.65+)**, sell **25 bp of that account**. One trim per name per day. (Audit 11/14: Keith trims by range position, and a third of his winning trims are under +1%.)
- With a **lower high in the range**, sell **50 bp** per green day instead.
- **Top of the range = trim hard (Kris 11/14, round 3):** at **rp 0.85+, or above the range top**, sell **100 bp** per green day.
- Never trim below the class MIN on a recycle. At MIN, the lines decide.
- Buy it back in steps on red days low in the range (buy rules above).

## Longs: exits
1. **−5% from what you paid → close it,** at any size (Kris 11/14; replaces the old "−6% at MAX" stop).
2. Below TRADE → cut to MIN and stop buying, with or without a lower high (Kris 11/14).
   - **A new position still at its opening MIN that breaks Trade → close it** ("the newest position is the riskiest"; Keith, Q&As 1/27–4/28; Kris 11/14).
   - **Adds start again only when** price is low in the range, the range low is still above Trend, and the range top is back above Trade (Keith 3/31, 5/26, 8/25).
3. Below TREND → out.
   - **Exception (Kris 11/14):** when Keith says he is still holding the name through his own break (TLT at minimum), hold at MIN while he holds. The −5% stop still applies.
4. **Above the top of the range = book gains, period (Kris 11/14, round 3; replaces the breakout hold-and-add):** 100 bp per green day down to MIN, and no adds. Adds come back only after the range resets higher and price is low in the new range (Keith 2/24, 3/31).

## Shorts: instrument
- **Use a 1x inverse ETF wherever one exists (Kris 11/14).** It gives control over how much to buy back and sell, with fractional sizes. Examples: METD (META), NVDD (NVDA), ORCS (ORCL), PSQ (QQQ), SH (SPY), RWM (IWM).
- Inverse ETFs can sit in any account, so the IRAs can carry short exposure this way.
- **Second choice: short the 2x long single-stock ETF (Kris 11/14),** for example NVDL for NVDA. It is cheaper per share, so whole-share covers come in smaller steps, and the daily-reset decay works for the short.
  - Individual account only.
  - **Size by exposure:** a 2x ETF counts double toward the 0.5–2% band, so $100 short of a 2x ETF = 1% of exposure on a $20k account.
  - Check Fidelity's borrow first. If it's hard to borrow or carries a fee, skip it and go to the next choice.
- **Last choice: a direct short** of the stock, in the Individual account only.
- **Whatever the instrument, every line, rp and bounce count is read on the underlying stock** (META for METD; Kris 11/14). Only the size and the trims are done in the ETF.
- **Whole shares on direct shorts:** round each cover down to whole shares, but always cover at least 1 share. A 1-share short is covered in full at its first trigger.

## Shorts: entry
- Bearish trend, below Trade, rp ≥ 0.65, shorted on a GREEN day. Lower highs in the range are best.
- Day 3+ of the bounce (consecutive up closes). Day 1–2 names go on the stalk list.
- **Falling volume = buying exhaustion (Kris 11/13):** volume shrinking day over day across the bounce's up days, today compared at the same clock time. The 20-day average does NOT matter. Heavy-volume bounces: stand aside.
- Incremental like everything else: open at the 0.5% MIN, never at full size.
- **Build in steps up to the 2% MAX (Kris 11/14; replaces "one add max").** Add only on a new day-3+ bounce at the top of the range, never into a short that's going against you.
- No new short within 2 days of earnings.
- **No new short when the name's 1-month return is positive** (Keith, audit 11/14). The stalk list shows 1-week, 1-month and 3-month returns.
- **No new short within 2% under the Trend cloud top:** a name just under Trend is a warning, not a short.

## Shorts: press when the stars align (Kris 11/21)
The one case where a short is added on weakness, not on a bounce. All four must be true at the same time:
1. The name is Bearish Trend on Hedgeye, below Trade, and already a short in the book (never a new short).
2. SPX is below SpotGamma's Volatility Trigger (negative gamma).
3. SpotGamma HIRO for the S&P is breaking down and the TRACE Stability Gauge is low. The SpotGamma watcher chat reads both every 10 minutes (Alpha is live from 11/21). **The press cannot fire until Kris sets the HIRO and Stability numbers,** after the first live week.
4. The name is above rp 0.15. At rp ≤ 0.15 the bottom-of-range cover wins, as always.
- The press is **one extra add of one normal step**, once per name per break, and never past the 2% MAX. The negative-gamma half-size rule does not shrink it: negative gamma is the setup.
- The press piece comes off first: cover it at the range low (rp ≤ 0.15) or when HIRO goes flat or turns up, whichever comes first, on any day colour. (SpotGamma's own guide: take profit when the HIRO line flatlines or reverses.)
- The 4%-against stop and the Trade and Trend stops apply to the whole position, as now.
- No press within 2 days of earnings.
- Log every press, and every time the four lined up but no press was taken, in the conviction log, so the rule gets its own hit rate.

## Shorts: recycle on red (Kris 11/14, mirror of longs)
- On any red day when the short is **0.5% or more in profit, OR low in its range (rp 0.35 or less)**, cover **25 bp of that account**. With an inverse ETF, sell 25 bp of the ETF.
- **Profit covers stop at the 0.5% MIN (audit 11/14):** keep a MIN short while the trend stays bearish, and re-add on the next day-3+ bounce. Only the stops (4% against, above Trade, above Trend) take a short below MIN.
- **Bottom of the range = book the gains (Keith 2/24; Kris 11/10 and 11/14):** on a red day at **rp ≤ 0.15, or below the range bottom**, cover **to the 0.5% MIN in one move**.
- **Never short the bottom of the range:** no new shorts or adds at rp ≤ 0.15.
- Re-short on the next day-3+ bounce at the top of the range.

## Shorts: exits
1. **4% against you (price 4% above your short entry) → cover half,** even below Trade (Kris 11/14).
2. Above TRADE → cover half.
3. Above TREND → cover all.
4. 1-share short → cover it all at the first trigger.

## Market picture (Kris, 11/14/2026): read the whole market before any single name
The picture changes two things: **which side gets added first** and **the step size**. It never changes the single-name rules above or the recycle trims and covers.
- **Risk-off:** shorts and Quad 4 longs first; dip-buys in high beta wait. **Risk-on:** dip-buys first; new shorts only on the cleanest setups. **Mixed:** no side priority.
- **Gamma sets the step size (Kris 11/15, SpotGamma scorecard).** SPX's next-day move averaged 1.11% below SpotGamma's Volatility Trigger vs 0.61% above it, and 1.28% vs 0.39% between the most negative and most positive fifths of net gamma (2018–2026; the same on NDX, QQQ, RUT and IWM). Gamma predicted the size of moves, never the direction.
  - **Negative gamma = SPX below SpotGamma's Volatility Trigger** (the session's levels, `py -m tools.sg_pages --gamma`), or Tier 1 Alpha's gamma setup negative. New positions and adds, long and short, use **half-size steps, whatever the reading**. Recycle trims and covers, and every stop, stay as they are.
  - **Positive gamma = SPX above the Volatility Trigger:** normal steps; Tier 1 Alpha's RANGE setup applies (buy support, trim resistance).
  - **Half doesn't stack:** half-size from vol (VIX 20–29), gamma or the throttle is still one half.
  - Line 2 of every check-in shows "Gamma: neg/pos (SPX x vs VT y)".
- **Net exposure:** no target yet. Show it next to the reading for two weeks (to ~11/28), then review.
- **Short profit covers are only taken on red days, never on bounces.** Only the stops cover into strength.

**Layer 1: volatility** ("rising" = above its own 5-day average)
- VIX: 19+ and rising = risk-off; under 19 and falling = risk-on.
- VXN rising faster than VIX = stress in tech/AI: press AI-sleeve shorts. RVX rising faster than VIX = stress in small caps: press small-cap shorts.
- MOVE: 110+ = risk-off; 90 or lower = risk-on.
- **Vol sets the size (Keith's buckets, audit 11/14):** 9–19 investable (normal steps); **20–29 chop: half-size steps on new longs; 30+: no new longs**, just cash and the stops.
  - **30+ blocks new longs only (Kris 11/20, VIX 38):** no long buys or adds. Longs already held are NOT sold for the vol bucket; their own lines, trims and stops still manage them.
  - **Shorts stay open at 30+** on the card's entry rules. **Inverse funds and hedge products can be bought at 30+** (PSQ, SH, RWM, METD, NVDD, ORCS; BTAL, TAIL, CAOS, UVXY): they are short or hedge exposure, not new longs. Their own lines and sizes still apply.
  - **Each market's own vol index (Keith, 3/6 lesson; Kris 11/14, tentative, recheck at the 11/28 review):** VXN for tech and AI names only (Kris 11/16: "tech only"; a Nasdaq listing alone doesn't count, so staples, healthcare and consumer names like COST, KDP, ISRG and SBUX go on VIX), RVX for small caps, VIX for everything else. Use the higher of the name's own index and VIX. At 30+ on its own index, that market gets no new longs (Kris 11/20: held longs are not sold for it; this replaces "long exposure is also sold, even at MIN").
- **Implied vs realized: are puts cheap or expensive? (Kris 11/14)** Use Keith's implied-vol premium table (tms_decks.ivol `ivol_prem` = implied vs realized, per ETF; SPY, QQQ, IWM first) with SpotGamma's IV/RV, skew rank and 25-delta risk reversal (rr25).
  - Premium 0% or below (implied at or under realized) = **protection is cheap; complacency** → risk-off lean: inverse-ETF protection is cheap, press shorts, trim longs into strength.
  - Premium +30% or more = **protection is expensive; fear is priced** → risk-on lean: cover shorts on red days, buy dips.
  - (Draft levels; review with the conviction log after two weeks.)
- **Who's buying: protection or calls? (Kris 11/14)** SpotGamma put vs call volume and OI, delta/gamma tilt, skew rank.
  - Heavy call buying with cheap puts = complacency → fade strength.
  - Heavy put buying with expensive puts = fear → buy weakness.

**SPY range: trim high, reload low (Kris 11/14)** (read SPX on Keith's Risk Range every check-in; rp as on the card)
- **SPX rp 0.80 or higher (top of the range): trim longs into strength.** On green days trim 50 bp instead of 25, highest beta and highest correlation to SPY first (60-day beta). No new high-beta longs. Let net exposure step down. Shorts go on day-3+ setups.
- **SPX rp 0.20 or lower (bottom of the range): reload.** Buy back the trimmed longs in full steps on red days, lowest rp first. Cover shorts on red days.
- **The put wall is not a floor (Kris 11/15, SpotGamma scorecard).** SPX closed through its put wall within 5 sessions 37.6% of the time, vs 30.9% for an ordinary level the same distance away; every index broke its put wall more often than chance.
  - While SPX is in negative gamma and at or below SpotGamma's put wall, **the rp 0.20-or-lower reload waits**: reload index and high-beta longs only after SPX closes back above the put wall.
  - Red-day short covers and the bottom-of-range cover still apply.
- **Call wall = trim zone for index ETFs (Kris 11/15):** the call wall capped a little (SPX crossed it 5 points less often than chance). When SPX is within 1% under its call wall, index-ETF green-day trims use 50 bp (the top-of-range size) even below rp 0.80.
- **In between:** the normal rules.
- **SPX lower high in its range:** long adds use half steps until SPX makes a higher high (Keith, audit 11/14).
- Show the trim and reload prices on line 2 of every check-in. 11/13 example: range 7,320–7,687 → trim zone starts at 7,614, reload zone below 7,393; SPX 7,563 = rp 0.66.

**Layer 2: credit** (FRED official, plus the live estimate from `tools.market_tape`)
- HY OAS: 5-day widening of 15 bp+, or above Keith's 3.00% and widening = risk-off. 5-day tightening of 15 bp+ while below 3.00% = risk-on.
- IG / BBB OAS: 5-day widening of 4 bp+ = risk-off; tightening of 4 bp+ = risk-on.
- Live: HY estimate widening 5 bp+ today = risk-off.
- **Ripple flag:** CCC widening while HY tightens, or CCC in its top 10% of 2 years.
  - The credit-funded AI sleeve (ORCL, CRWV, NBIS, IREN, BX, APO, ARES, OWL, ARCC, FSK, SoftBank) goes to the front of the short list, still on the card's entry rules.
  - No new longs in high beta or credit-sensitive names.
  - It clears when CCC leaves its top 10% or stops widening for 5 days.
- **Rates caution (a caution, not a block):** 10Y up 25 bp+ in 5 days → label every buy in rate-sensitive names (homebuilders, REITs, utilities, long bonds, mortgage-sensitive financials) "RATES CAUTION". The card's signals still decide.

**Layer 3: Tier 1 Alpha flows** (recheck against T1A's how-to video once it's transcribed)
- **Gamma doesn't vote a direction:** negative gamma makes moves bigger whichever way the flows push.
- **Vol toggle** (SPX 1-month realized vol = blue vs 3-month = red, T1A p4; `tools.rv_cross`):
  - Blue above red = systematic funds selling ("look out") = risk-off.
  - Blue crossing under red = they re-buy ("ball under water pops") = risk-on.
  - Show the projected cross date.
- **Throttle:** negative = vol expanding (half-size steps); positive = vol compressing (range-trade the levels).
- **CTAs** (direction only): rising 3 reads = buyer (risk-on); falling 3 reads = seller (risk-off).
- **Backboard levels** (not a vote): the gamma flip, support/resistance strikes, and the PV band. **Plus SpotGamma's (Kris 11/15):** Volatility Trigger, zero gamma, call wall, put wall, Large Gamma 1–4 and Combo strikes (`py -m tools.sg_pages --gamma`).
- **Setups:**
  - SQUEEZE (negative gamma, flows buying): take red-day profit covers before the pop; no new index shorts until the pop stalls at resistance; on the pop only the stops cover.
  - AIR POCKET (negative gamma, flows selling): press shorts on bounces; dip-buys wait.
  - RANGE (positive gamma): buy support, trim resistance.
  - PIVOT (near the flip): wait.

**Layer 4: cross-asset and countries** (each read against its trend cloud; crude = WTIC/CL_F, NOT "CL", which is Colgate)
- Dollar above its cloud and rising = risk-off for EM, commodities and multinationals as a group. **It never overrides a name or country whose own lines are bullish:** a market rising against a strong dollar (Mexico, 11/14) shows relative strength and is a keeper.
- Bitcoin below its cloud = risk-off for high beta and crypto names.
- Copper falling faster than gold = growth slowing. Oil rising while copper falls = Quad 3 warning.
- Country breadth: under 40% of indexes above their cloud = risk-off; over 60% = risk-on.
- SPY and TLT falling together (30-day correlation positive) = TLT isn't a hedge right now.

**Inflation nowcast and the release calendar (Kris 11/14)** (read docs/playbook/MACRO_CALENDAR.md every check-in)
- Know the dates: CPI, PPI, PCE, jobs, GDP, retail sales (FRED's official calendar, loaded daily). Line 1 shows the next high-importance release and how many days away it is.
- Lean into the print with Hedgeye's inflation nowcast (direction plus numbers, as of its latest note) and the inflation axis of the quad forecast in QUAD_STATUS:
  - Nowcast says inflation is decelerating: lean long duration (TLT), short commodities on strength, and take the dollar side Hedgeye's quad call gives for that quad.
  - Nowcast says inflation is accelerating: apply the rates caution, lean commodities/energy on dips, and take the dollar side Hedgeye's quad call gives.
  - The dollar lean comes from what Keith says works in that quad, never a fixed rule. DXY's own Trade/Trend lines still decide each entry.
- When the print lands, compare it with the nowcast. A surprise against the nowcast is the first thing on the next check-in.

**Combining:** risk-off = 2+ layers vote risk-off and none vote risk-on; risk-on = the mirror; otherwise mixed. Flags always print on their own line: credit ripple, rates caution, squeeze / air pocket, Quad 3 warning.

**Keith's slant (Real-Time Alerts)** (flags, not votes; read his last 2 sessions)
- Covers outnumber short adds = HARVEST SHORTS: on our red days, cover 25 bp on every short that qualifies for a red-day cover, 50 bp in the theme he's covering (still not below the 0.5% MIN).
- Short adds outnumber covers = PRESS SHORTS: new shorts on card setups get priority.
- Long trims outnumber buys = HARVEST LONGS: trim 50 bp instead of 25 on green days.
- Long buys outnumber trims = BUY DIPS: dip-buys get priority.
- **He posts in two batches:** about 10–11 AM (mostly covers and trims) and about 3:10–3:55 PM (mostly short adds). The latest batch sets the flag.
- **His clock is context, not a rule (Kris 11/14):** he tends to cover in the morning on red mornings and add shorts late in the day. We can add at any time.
- A plain "sell" can be a short add or a long exit: check his earlier alerts on the name.

**Worked example, the week of 11/9/2026:**
- SPY bounced Mon–Thu on falling volume (50 → 41 → 39 → 37M).
- Thursday's high of 763.04 ≈ SPX 7,657, the gamma flip (the backboard). It failed there on the lightest volume, and Friday rolled over from the open.
- The lesson: add shorts into the bounce at the backboard while volume fades; take profit covers on the red day.

## Conviction score (Kris, 11/14/2026): rank every name; Kris chooses, no daily cap
Every action and stalk line carries a score out of 100. Lists sort highest first, and the line shows the breakdown, e.g. `A 86 (card 50 · HE 15 · picture 15 · setup 6)`.
- **Card checks (up to 50):** the share of the entry checks passed. Short: bearish trend, below Trade, rp 0.65+, day 3+ bounce, buying drying up, green day. Long: bullish trend, rp 0.35 or less, 3+ day selloff, selling drying up, red day. Only names with all checks passed (50) go on the action list; the rest are stalk.
- **Hedgeye backing (up to 20):** Signal Strength same side +10; ETF Pro same side +5; Keith alerted the same side in the last 10 days +5; Position Monitor same side +5 (capped at 20). A side conflict costs −10.
- **Market picture (up to 15):** the side matches the reading +15 (mixed +7, against 0). Ripple on and the name is in the credit-funded AI sleeve +5. Rates caution on a rate-sensitive buy −5.
- **Setup quality (up to 15):** lower highs (short) or higher highs and lows (long) +5; rp 0.85+ (short) or 0.20 or less (long) +5; volume shrinking 3 days straight +5.
- **Exits are scored too (Kris 11/15: "score everything"): trims, covers, stops and breaks.**
  - Card (50): an exit goes on the list only when its rule's trigger is met, so it always gets 50.
  - Hedgeye backing (up to 20), on the exit side: removed from Signal Strength or ETF Pro +10; Keith trimmed, covered or sold it in the last 10 days +5; research now against our side +5; Position Monitor removed or flipped +5. Hedgeye still adding on our side −10.
  - Market picture (up to 15): the exit fits the reading +15 (risk-off for long trims and stops, risk-on for short covers; mixed +7, against 0).
  - Setup (up to 15): rp 0.85+ for a long trim, or 0.15 or less for a short cover, +5; a lower high (long) or higher low (short) +5; volume rising against the position 3 days +5.
  - Stops and Trend breaks are taken whatever the score. The score ranks them and logs them; it never vetoes one.
- **Grades:** 80+ = A, 65–79 = B, 50–64 = C.
- **Scores aren't probabilities yet.** Log every scored action in `data/stalk/conviction_log.csv`: date, time, account, ticker, action, side, size, score, grade, the four parts, entry/trigger price, then the outcome (+3% reached, or stop hit, and days taken). After ~50 logged calls, each grade gets a real hit rate, and the weights get reviewed.
- **Desk picks vs my own trades (Kris 11/15).** Every row says who it came from and whether I took it:
  - source: desk (the desk called it) or kris (I traded it with no desk call; the desk logs it from my fills and scores it after the fact).
  - taken: yes / partial / no / pending, with the fill date, price and quantity from my Fidelity fills. A desk call not filled by the next day's close = no.
  - Outcomes are tracked for every row, taken or not, so the review can compare three things: all desk calls, the desk calls I took, and my own trades.

## Book, cash and clock (audit round 2, Kris 11/14/2026)
**Cash and book shape**
- **Cash is a position.** Proceeds from stops and trims sit in cash (SPAXX/BUXX) until a card buy qualifies. "Raise cash" is a valid action at any check-in.
- **Book shape:** total long stays bigger than total short, and the average long is bigger than the average short.
- **Concentration:** flag when more than half the book depends on one quad outcome. Single stocks and ETFs are both fine; Kris uses single stocks sometimes, not always.
- **No daily cap on new names, and no limit on the number of positions** (Kris's call), but flag when more than 3 A-grades show up on one side the same day.
- **Leverage:** no 3x products. Margin is fine; keep the Individual account's $5,000 margin buffer.
- **No options, in any account.** The options paper study (see Open) is on paper only and changes nothing here until Kris rules on its results.

**The clock**
- **No time-of-day limit on new positions (Kris 11/14, round 3):** longs and shorts can be added at any time. Kris tries not to trade the first 20 minutes, so the desk marks early actions "after 9:50". (Keith's own windows, 9:50–11:30 and 2:15–3:30, are context; may be revisited.)
- **Trim green-open overbought winners early in the session,** after the first 20 minutes.
- Breaks are still called at the 3:45 check (exits; see Lines).
- **Day-1 check:** every new position gets checked the next day for a trim or cover trigger (35–42% of Keith's first exits come day 1).
- **Losers take longer:** don't treat day 1–2 weakness as a stop; only the price stops cut early (Keith's losers exit at a median 4–5 days).

**Re-entries**
- **After a stop:** re-buy only after a higher low and a Trade/Trend breakout, at MIN, low in the range, never on the flip day. Wait 2+ weeks after a losing exit.
- **Trade reclaimed with a higher low:** rebuild in full steps.

## Signal Strength / Position Monitor
- **Removed from Signal Strength (same side) → out of the book**, long or short, and off the stalk list.
- **Exception: on the Position Monitor** (any bucket, same side) → keep and manage by the lines.
- **Conflict: Signal Strength outranks the Position Monitor (11/12).** Trade the Signal Strength side and note the PM conflict in one line.
- Stay inside Hedgeye inventory: Signal Strength, Position Monitor, ETF Pro, Risk Range, Alpha Code, and positions Keith states on The Call or the Macro Show. Anything else is tagged "off-list".

## BUXX: buy every month (Kris 11/14; replaces the FXY exception)
- BUXX is cash parking and the only name bought for its dividend.
- Buy it **every month**, in the Individual account. The program is **$5,000 every month** (Kris 11/14; $60K a year).
- The window is the **ex-dividend dip**. Ex-dates fall around the 27th–30th. The cheap half is rp < 0.5.
- The desk flags the window at 9:00 on the first day of it. Don't let a month pass without a BUXX buy.

## Regime: read live, never hard-coded (Kris 11/14)
- The regime is whatever Hedgeye says **now**. Never carry last month's quad in your head.
- At the start of every desk session:
  - Read `docs/playbook/QUAD_STATUS.md`, which the capture desk keeps current: the now-quads (US monthly, US quarterly, global) and the next-quad forecast. Cross-check `hedgeye_quad` if the file is more than a day old.
  - Read Keith's forward call: Macro Week Summary Notes quad probabilities, and what he states on The Call and the Macro Show.
  - Put the regime in one line at the top of every action list.
- Long and short themes come from Hedgeye's own playbook for the current quads (what Keith says works in that quad), not from a fixed list.
- **A quarterly or global quad change: say so first and re-rank the book and stalk lists. A monthly quad flip: one line and a tactical tilt only** (Keith: the quarterly quad is the climate, the monthly is the weather; audit 11/14). The capture desk stores every quad update.
- Countries: Quad 1/2 + bullish index = long candidate; Quad 3/4 + bearish = short candidate.
- **Signal vs quad conflict:** the signal is allowed, but at MIN size only and no adds until the quad agrees (audit 11/14).
- **During a stated quad transition,** the legs move together (e.g. into Quad 4: commodities short, bonds long, bond proxies long). A Trade break in one established leg = hold at MIN; don't exit one leg alone. (A brand-new leg still at its opening MIN that breaks Trade is closed, per Longs exit 2.)
- **Using the quad forecast (Keith: 2/27, 3/10, 7/13, 7/29; Kris 11/14):**
  - Build the coming quad's book **leg by leg, only as each name's own lines confirm.** A name that fits the quad but fights its own Trend is never bought (or shorted).
  - When the monthly and quarterly quads disagree, the signal decides.
  - **If the leading next-quad probability is under 40%, apply no forecast tilt; signals only.**
  - **3+ legs of one quad breaking Trade in the same week = the shift is in progress.** Start the legs that confirm at MIN without waiting for the model.
  - **When Keith says he's moving into Quad 4, cut high-beta longs first** (Kris: keep filling up on Quad 4 exposures; no count cut).
- The current regime and the plan built on it (for example, using Quad 2 strength to build the coming Quad 4 book) live in `PORTFOLIO_DIRECTION.md`, which the desk updates when the quads change.

## Open
- For the first monthly rule review (weekend of 11/28–29):
  - the own-vol-index rule (tentative; the 30+ part was ruled 11/20), and how hedge products (BTAL, TAIL, CAOS, UVXY) are sized;
  - whether to add time windows (Kris: not yet);
  - day-1 shorts at the SPX backboard;
  - the short press (11/21): set the HIRO and Stability Gauge numbers after the first live week of the SpotGamma watcher (starts 11/23), then check its hit rate.
  - a tighter long stop than −5% (Kris 11/21, candidate only): `py -m tools.execution_report --days 90` showed a typical winner never went more than about 1.5% against, a typical loser went to about −5%, and about two in three losers were up 1%+ first. On paper a −3% or −4% stop beat −5% over 90 days, but that test uses daily bars and is rough. Re-run it for the review, and also look at a stop moved to cost once a long is up 1%+.
- **Talked through 11/21, not ruled (SpotGamma Alpha is live; waiting on the first live week):** (1) use the live S&P read to time trims, covers and adds the card already calls for; (2) a separate index sleeve that trades the move itself (long beta up, inverse funds or UVXY down) with its own products, size, stop and time limit. Neither is a rule yet.
- **Options paper study (Kris 11/20 and 11/21): paper only.** For every A-grade call the desk logs what the option version cost at that moment, off live TradingView quotes, and compares the outcome with cash (`data/stalk/options_study.csv`; the desk prompt has the steps). SpotGamma (cheap or expensive options, the walls) is context for it. Review after 30 finished calls; until Kris rules, no options.
- Audit round 3 batches 2–3 (16 items, single-source or low confidence) are parked in `PROCESS_AUDIT_ROUND3.md`.
- **Mid-December SpotGamma keep/cancel review:** re-score the tape (15-minute reads from 11/16) and call-wall distance net of momentum (11/15: about zero once 1-month momentum is removed, so no single-name rule); check the three SpotGamma rules above against the conviction log. Evidence: `data/reviews/sg_scorecard_2026-11-16.md`, `PROPOSAL_SPOTGAMMA_CARD.md`.
