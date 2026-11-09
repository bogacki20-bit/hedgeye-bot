# Keith's Process Playbook

> **PRIVATE.** This is distilled from Hedgeye subscription content (The Macro Show). Keep it local. Do not publish, paste, or upload it.

**What this is:** the risk-manager agent (OpenClaw) runs on these rules, and Kris uses them as a reference. Each rule comes from something Keith McCullough said on The Macro Show between 2026-08-04 and 2026-10-19 (46 shows, auto-transcribed). Each rule cites 1–3 dates plus a short quote. Transcription errors happen ("debt" for "dip", "Cosby" for KOSPI), so treat every quoted number as approximate. Where Keith's rule changed over time, the entry says so.

**Machine-readable twin:** `docs/playbook/rules.yaml`.

**Agent's stance:** recommend only. The agent reports what the rules say about Kris's book, and Kris makes every trade decision.

---

## 1. Durations: TRADE, TREND, TAIL

| Duration | Horizon | How Keith uses it | Source |
|---|---|---|---|
| **TRADE** (immediate-term) | ≤ 3 weeks | First stop level. A break sends the position to its minimum. Keith calls trade momentum the machine's most powerful force. | 08-10 "trades are three weeks or less, trends are three months or more"; 08-11 "that's the most powerful force inside of the machine" |
| **TREND** (intermediate) | ≥ 3 months | Hard stop. A break means you're out. It also defines bullish, bearish, or neutral. | 08-10; 09-14 "the three month trending return we define the word trend" |
| **TAIL** (long-term) | ≤ 3 years | Context only, rarely acted on. | 08-10 "tails are three years or less"; 09-22 "I don't trade that duration much" |

- **Alpha code** = "TRADE / TREND" written as one number. For example, BTC 8277 means 82k trade and 77k trend (10-19). Trade comes first, then trend (08-11).
- **Whichever line breaks first matters more:** "The one that happens first" (08-25).
- **Price speeds up through the trade line:** "it goes up faster when it goes through the trade line" (10-15; also 08-25, 09-02).
- **Why 3 weeks:** most hedge-fund risk platforms model factors on a 1-month window, so a ≤3-week signal front-runs them (10-19). See §7.
- **Use the short term to make intermediate-term decisions,** not to day-trade (08-25).

---

## 2. Signal states → actions

The **"line"** is the TRADE or TREND level. **rp** is position in the daily Risk Range: 0 = low end, 1 = top end.

| State | Longs | Shorts | Source |
|---|---|---|---|
| **Bullish TRADE + TREND** | Buy the dip in increments. The best setup is bullish/bullish at the low end of the range. Sell some on green near the top end. | Don't short. Cover any existing short. | 10-16 "If it holds bullish trade and trend, I buy the damn [dip]"; 09-24 "bullish trade, bullish trend, and at the low end of the range"; 08-25 "Sell some winners when they're overbought at the top end" |
| **Bearish TRADE, bullish TREND** | Hold at the **minimum** and don't add. If it bounces but stays below trade, take it to the minimum. If it gets back above trade, keep it. | Don't short a bullish trend. | 10-19 "we keep the position at the minimum"; 10-16 (QQQ: hold, don't add) |
| **Breaks TRADE** (from bullish) | Three steps: (1) stop buying, (2) start selling, (3) reduce to the minimum. It's a "yellow light". | Step 2 allows new shorts, but only where the trend is also bearish. | 09-02 "First thing when the trade breaks is you stop buying stocks"; 08-12 "You sell it to your minimum"; 10-15 "it's a yellow light, stop buying it" |
| **Breaks TREND** | Stop out the same day. Neither the quad nor a narrative overrides it. If the position is at a loss, harvest the tax loss. | Becomes a short candidate once trade is also bearish. Short the bounces, and don't go big on day one. | 10-15 "If it breaks trend, it's a stop"; 10-15 "I don't care if it says quad too"; 09-22 "I'm stopped out of the position"; 10-16 "If Bitcoin breaks trend, it will be sold" |
| **Bearish TRADE + TREND** | Don't own it. Dips here aren't buying opportunities. | Short all bounces. Add near the top of the range or on green days. | 10-15 "you short all bounces"; 10-16 INTC/AMAT "you should not own those"; 10-19 GOOGL/NVDA "those aren't dips" |
| **Positive TRADE, bearish TREND** | Wait and watch. Don't buy. | Don't press new shorts. Don't short something while it's going up. | 10-19 "positive trade, but bearish trend, it's a wait and watch"; 10-16 "I don't want to short things that are going up" |
| **Neutral TREND** (higher low plus a bullish trade breakout) | Don't treat neutral as the go zone. Prefer the bullish alternative. | No action. | 08-28 "Higher low and bullish trade break up"; 09-08 "why would you buy the neutral when you can buy the bullish one?" |

**First day of a reversal.** Don't go big on the day a signal flips. Wait a day and "let the fire breathe." Keith calls this experience, not a backtested rule.
- 10-16 "the first day on a reversal is generally not the day"

**One bad day is not a broken signal.** Re-check the lines before acting on a P&L hit.
- 10-16 "One day clearly does not a trade make, never mind a trend make"

**Covering shorts:**
- Cover when the signal flips to bullish TRADE/TREND (10-16: he covered financials and utilities).
- Cover to the minimum at the low end of the range or when the name signals oversold. Reload on green (10-02 "Reduce them to their minimum position size and get ready to reload on green"; 09-17).
- Bond shorts are the mirror image: cover TLT/LQD to the minimum when **yields** reach the top of their range (08-11, 08-18 "You cover the shorts to their minimum"; 09-08).

**How tops form.** Watch for a lower high in the Risk Range, then a TRADE break, then a TREND break, in that order. Start trimming at the lower high.
- 10-02 "a lower high and immediate term trade breakdown than a trend breakdown in that order"
- 10-02 "reduce all my energy positions down to their minimums"
- 10-01: when a name is bullish TRADE/TREND but its range is making lower highs, "just be less aggressive when you buy it"
- 09-08: a lower high coming off the top end of the range is a reason to book gains.

**Scenario-plan every position** from its trade line, trend line, and range ends.
- 08-25 "You should think of every single position you have that way."

---

## 3. Position sizing

| Item | Rule | Source / notes |
|---|---|---|
| **Commodity minimum / maximum** | **1% / 4%**. Below 1%, the position is out. | 09-10 "minimum position size of a commodity investment? So 1%" and max "4%"; 10-15 "Four was the max. One is the min. And then it's out." |
| **Crypto** | Sized in the commodity bucket at 1% / 4%. Keith calls BTC "a high beta commodity". | 10-15 (BTC "my minimum for commodity? 1%"); 09-24 (1.5% to 4% "my max position"); 10-16 |
| **Equity minimum** | **2%** for an equity ETF. | 10-19 "a 2% position, which is my minimum position in an equity"; 08-13 (Colombia "minimum position is two") |
| **Equity maximum** | **Not stated as a number.** Keith says it's the investor's call: "A 3% position in Amazon? Five, 6%? That's on you." He mentions 2–4% for a country equity allocation. | 08-18 "If you don't know what your position limits are…"; 08-25 (New Zealand, "two to 4%") |
| **Gold (exception, rule changed)** | In August Keith treated gold as a core holding: 3% minimum, maximum "could be 12". He later went **below** his own minimum twice (1.5% on 09-24) and nearly exited. Use the 1%/4% commodity band unless Kris decides otherwise. | 08-13 "its max could be 12"; 08-10 "well below any minimum I've ever had"; 09-22 "violated my minimum… for the second time in my career" |
| **Add in increments** | Add 50–100 bp at a time, never 300. Add more the closer price is to the low end of the range. | 08-18 "You buy 50, 100 basis points at a time"; 10-19 (PFIX lesson); 09-24; 09-22 (oil scaled in toward trend support) |
| **Smallest move** | 25–50 bp. Never 10 bp. | 08-13 "I don't transact in 10 basis points" |
| **Trim at the top of the range** | Take winners to the minimum near the top of the range, then rebuild on dips toward the low end. | 10-19 (PFIX); 09-24 (BTC to the minimum "when it got towards the top end"); 08-25 |
| **Number of positions** | Long-only account: at most 30 positions. In Quad 4 he cut to about 20. | 08-11 "max of 30 positions"; 09-15 "30 longs, which is my max, down to 20" |
| **Signal strength → size** | "Long two ways" (two ETFs on one theme) means a very strong signal. | 08-11, 09-15 "When I go two ways, that means it's a very strong signal" |
| **Currencies** | FX positions need to be larger to matter. | 08-18 "If it's foreign currency, you gotta be bigger in it" |
| **Never buy the bench** | In small-cap and energy names, buy only the top names (Signal Strength plus research). Don't buy the analyst's bench. | 10-19 "I would not buy the bench"; "Stop buying the stocks that you or the analyst likes" |
| **Anxiety is a sizing problem** | A 3–6% correction in one position is normal. If you can't tolerate that, set lower minimums. | 10-19 "you should even have lower minimums in terms of position sizing than me"; 10-01 "It's probably a problem with your position sizing" |
| **Execution timing** | Transact into the close. Never trade pre-market. | 08-12 "into the close, I transact"; 09-15 "I never trade in the pre-market, ever" |

---

## 4. Volatility regimes

**Three buckets: investable, chop, and the "F bucket".**
- 08-17 "the investible bucket, the chop bucket, or in the F bucket?"

| Index | Investable | Chop | F bucket | Source |
|---|---|---|---|---|
| **VIX** (S&P) | < 19 | ≥ 19 | **No level stated** | 08-06 "where the investible bucket ends and the chop bucket begins"; 08-25 |
| **RVX** (Russell) | < 21 (beta-adjusted) | ≥ 21 | n/a | 08-12, 08-25 "below 21 for the Russell" |
| **NASVOL / VXN** (Nasdaq) | < ~22. The line moves: 21.56 on 09-02, ~21.76 on 09-04. | ≥ ~22 | Keith expects it if the QQQ trend breaks with NASVOL at 27 | 08-27 "Anything below 22 puts it into the investable bucket"; 10-19 |
| **MOVE** (bond vol) | Read against its own TREND line (73–75 in Aug, 93 on 10-15) | Keith says bonds are in their own "chop-bucket equivalent" (10-16) | n/a | 08-14, 09-08, 10-15 |

**How a bucket changes behavior:**
- **Investable:** buy dips in bullish signals. A tight, low VIX range is "a buyer of dips type market". 08-21 "buy dips in the investable bucket until it's not the investable bucket"; 08-26; 09-30 (VIX 14–17: be a buyer).
- **Chop:** don't buy the index that's in chop when something else is still investable. 08-25 "why would you buy something in the chop bucket". Example: he stayed out of QQQ and IWM while NASVOL/RVX were in chop, but bought S&P-linked names (08-25, 09-15).
- **Bucket change → the Risk Range changes:** "into the chop bucket, that's going to definitely change the risk range" (10-16).
- **Staying in chop → ranges don't shift.** Price whips between the top and bottom of the range, so trade the range edges: "a lot of people get whipped around in the risk range" (10-16).
- **F bucket = stop-loss territory.** "in that scenario, I will not stay long Qs" (10-19).
- **VIX above its own TREND line** (~16.4–16.6 in Sept): "the probability is rising that you're going to be in the chop bucket" (09-16, 09-18).
- **VIX range drives the S&P range:** the top of the VIX range sets the low end of the SPX range (08-18). VIX back below 19 and then below its ~17.5 trade line → S&P all-time highs become possible (10-19).
- **Short-dated calendar vol at 8–10 is complacent.** A legitimate S&P buying opportunity needs it "into the 12 to 14 range to get a legit buying opportunity" (08-18; 10-15, 10-19).
- **1-month vol crossing above 3-month vol** triggers selling by vol-control funds. Be careful (09-24, 09-29).
- **Oil moves bond vol:** "the oil moves move the bond market volatility" (08-12). A bullish MOVE trend is bearish for bonds. Keith expresses it long PFIX (09-22, 10-19).

**Not stated:** Keith never gives a % sizing change per bucket. Buckets change *what* you buy and *whether you buy dips*, not a sizing formula.

---

## 5. Quads

**What each quad favors and punishes** (US monthly/quarterly growth × inflation model):

| Quad | Growth / inflation | Favors | Punishes | Source |
|---|---|---|---|---|
| **1** | Growth up, inflation down | Small-cap value, low-quality / high-short-interest names, high-yield credit | n/a | 08-25 "you buy small cap value, low, crappy stocks, high short interest stocks, crappy credit"; 08-11 |
| **2** | Growth up, inflation up | Tech/growth ("grip it and rip it"), high beta, USD, Bitcoin, energy; **bond yields rise most** | Bonds, utilities, gold, financials when the curve flattens | 10-01 "We're in grip it and rip it zone for tech in quad two"; 09-29 (yields up most in 2); 09-22 (utilities "body bagged"); 09-02 (gold dislikes 2) |
| **3** | Growth down, inflation up (stagflation) | Energy, tech, healthcare, REITs (per the backtest); gold | Small caps | 08-04 "the back test says that you should buy energy stocks"; 08-04 "Quad 3 doesn't like small caps"; 09-01 (gold works in Quad 3) |
| **4** | Growth down, inflation down | Cash, shorts, USD; gold coming out of Quad 2 | High beta momentum, high short interest, retail baskets, credit (HY spreads widen), tech (falls with falling yields); the curve compresses or inverts | 08-04 "High short interest stocks get absolutely smoked"; 08-11; 08-17 "quad four, stock market crash dollar up"; 08-18 "In quad 4, you've raised cash, reduced risk"; 10-15; 10-19 |

**Signal beats quad, every time.** A quad-favored asset with a bad signal is a no.
- 08-11 "the quad said you could get long China, but the signal said get out"
- 09-15 "the Turkey looks good, but the signal looks bad, so don't buy it"
- 09-22 "If it doesn't have signal, we don't put it on." He sold small caps going into Quad 1 because "signal says get out" (09-02).
- The reverse also holds: he won't buy a market with four straight Quad 4s, and won't short it unless the signal says to (08-31).

**Counter-trend week vs. quad change:**
- A one-week Quad-4-looking move inside Quad 2 is a counter-trend move, not a quad change. 10-19 "that's called a Quad 4 counter-trend move, okay? The trend remains Quad 2."
- The test is inflation direction. Inflation rising means Quad 2 or 3. Inflation falling means Quad 4 or 1 (10-19 "This is Quad Four or Quad One because inflation's going down").
- One intraday Quad-4-style curve flattening is not a reason to position for Quad 4 (10-15 "I don't want people to position for quad four today").
- Positioning shifts ahead of the official quad change ("net shifting", 08-04). Read the quad *sequence*, not one month (08-04).
- Backtests of "typical" quad behavior are useless without a signal (08-04; 09-22 "X percentage of the time is useless, if you don't have a signal").

**Current outlook (snapshot from the 10-15, 10-16 and 10-19 shows; it will go stale):**
- US monthly quad is **Quad 2** ("Quad Two or Three because inflation's going up", 10-19). The October CPI nowcast is rising again.
- "the best of quad two is over, which isn't that far away… Like it's like December" (10-16).
- **First monthly Quad 4 = January 2027**, with a Quad 4 quarter in Q1 2027 (10-15, 10-19). The 09-29 quad count was 2, 2, 1, 2, 2, then Quad 4. Keith expects a 2027 growth and profit slowdown and an AI-bubble unwind.
- The world and Europe are in **Quad 3** (09-22, 10-01), so he shorts Europe and runs a long US-growth book.
- Book stance on 10-19: long USD, Brent, nat gas, PFIX, healthcare, software, MSFT/AAPL/META, BTC; QQQ and XLK at the 2% minimum. Short TLT, HY, the Russell, industrials, Europe, India.

---

## 6. Credit and macro warning lights

| Light | Rule | Source |
|---|---|---|
| **HY OAS 300 bp** | This is credit's breakout line, the equivalent of VIX 19. Above it, the cycle may be turning: be short HY and levered credit, and don't own credit-dependent names. It was 324 on 10-15 and 331 on 10-19 ("worst week"). | 09-29 "anytime high yield spreads go over 300 basis points"; 10-15 "for high yield spreads is 300 basis points over treasuries"; 10-19 |
| Credit as a priority | Keith watches HY spreads on his main screen alongside S&P futures. | 10-15 "we watch credit spreads like Hox" |
| **Gamma (Tier1 Alpha flip line)** | Above the line, gamma is positive: dealers sell strength and buy weakness, so fade moves, and selling on green is easier. Below it, gamma is negative: moves accelerate both ways. Positive gamma is **not** a bullish signal. Use gamma for execution, not for direction. | 08-28 "dealers are going to be selling strength and buying weakness"; 09-04 "do not confuse the words positive gamma with bullish market"; 10-19 "the rallies can happen as quickly as the declines"; 09-25; 09-24 "as an execution point" |
| T1A is not Keith's signal | It complements Keith's process but doesn't replace it. Never act on T1A alone. | 09-08 "The tier one alpha data is not my signal." |
| **2s10s curve** | A steepening curve with yields up means reflation: long a steepener (IVOL). A flattening or "pancaked" curve with the Fed hiking means don't buy financials. A curve that compresses or inverts is a Quad 4 warning, as in the 2021→2022 analog. | 08-11, 08-18 (steepener); 09-22 "Do not buy the financials on that"; 08-11 "yield curve, again, compresses, in some cases could invert" |
| **2-year yield** | Keith uses it as his "Fed front runner". | 08-25, 10-16 |
| **USD** | The #1 factor exposure in macro. Dollar up means gold down (about a 92% inverse correlation on a trending basis) and EM down. Dollar down helps the S&P and EM. In Quad 4 the dollar rises. | 10-01 "the dollar is the number one factor exposure in all of macro"; 09-02 "dollar up, gold down on a trending… basis"; 08-05, 08-17 |
| **BTC–USD correlation (changed)** | In August Keith said BTC was inversely correlated to the dollar. By late September the 15–30-day correlation had turned **positive**, and he went long both. Check the current correlation; don't assume the sign. | 08-11 (inverse); 09-24, 10-01, 10-19 (positive) |

---

## 7. The 1w / 1m / 3m momentum table

Build a row per ticker with 1-week, 1-month, and 3-month returns, and read it left to right the way the machine does.
- 10-19: "you can build these tables on your own"

**The 1-month column is the one that matters most.** Hedge-fund factor risk systems use a 1-month window, and Keith's ≤3-week TRADE front-runs it.
- 10-19 "There is no more important column to the machine than this."

| Pattern | Action | Source |
|---|---|---|
| 1m positive | Don't be short it. | 10-19 "it's positive price momentum, I'm probably not gonna be short that thing" |
| 1m flips negative | Treat it as very negative. | 10-19 "When that flips negative, it's very negative." |
| Positive trade, bearish trend | Wait and watch. | 10-19 |
| Positive trend that breaks trade | Reduce to the minimum. | 10-19 "If it's a positive trend that breaks trade, it gets reduced to my minimum" |
| All three columns negative (e.g. Russell −3.1 / −4.3 / −8.5) | A "certified disaster". Short it or avoid it. | 10-19 |
| New 1-month and 3-month highs | Higher highs, which keep the momentum going. | 09-08 |

---

## 8. How the products relate

| Product | What it is | Trust it for | Source |
|---|---|---|---|
| **Risk Range (daily sheet / dashboard)** | Trade/trend status (shown as color), plus a low/high range per ticker | Execution: buy near the low end, sell or cover near the top end. **Color changes** (trend flips) and the leaderboard are the daily must-reads. The range is "not a price target. That's a fractal target." | 10-15; 08-18; 09-25 "trading around your positions, using the risk range" |
| **Signal Strength (stocks)** | Daily long and short lists ("quantimental": signal plus analyst) | Single-stock selection. Additions and removals at the margin are early warnings. RPK said this and Keith agreed (10-15). The long/short **count ratio** is itself a signal: 46/80 on 09-15, 3:1 shorts on 10-19. | 10-15 "the signal strength list is the master chief list"; 08-07 "when I add things to signal strength, you should add them" |
| **ETF Pro** | Keith's long/short macro ETF book | Macro ETF positions, including the short side (about 20 macro shorts on 10-19) | 08-07, 09-15, 10-19 |
| **Real-Time Alerts** | Keith's in-game trades | Direction and timing. **Not a portfolio.** The long-vs-short count is a proxy for his net exposure. A short added in RTA usually means the name is near the top of its range. | 08-12 "Real time alerts is not a portfolio. It's a coaching tool."; 08-21; 10-19 |
| **Portfolio Solutions** | Keith's actual positions and sizes, published daily (he also trades into the close) | Sizing and minimum/maximum examples | 08-12, 08-13, 10-16 |
| **Tier1 Alpha** | Gamma, vol-control flows, short-dated vol | Execution context only | 09-08 |
| **Position Monitor** | n/a | **Not mentioned in any of the 46 shows.** No Keith rule exists for it. | Gap |

**If products conflict:** the signal (trade/trend line) wins, the Risk Range sets the execution price, and product summaries never override it.
- 09-08 "don't confuse the summary of one product with the summary of my signal"

---

## 9. Behavioral rules

| Rule | Source |
|---|---|
| **No emotion.** Decide from numbers, rates of change, signals, and quads. | 10-16 "There's no emotion to the decision." |
| **Numbers first, narrative never first.** | 10-15 "I never start with any fundamental rationalization anymore"; 09-08 "I don't really care about the narratives. I care about the numbers." |
| **Correction ≠ drawdown ≠ crash.** A correction is 3–5% (Nasdaq −3.8%). A drawdown is double digits (Russell −11%). A crash is the KOSPI at −39%. | 10-15 "do not confuse a correction with a drawdown or a crash"; 10-19 "a three to five percent correction is what the definition of a correction" |
| **Little losses are fine. Drawdowns and crashes break compounding.** | 09-02 "Little losses do not subtract from compounding. Drawdowns and crashes do." |
| **Rule #1: don't lose money.** Stop out losers, add to winners on sale, sell winners at the top of the range. | 08-25 "Stop yourself out of losers, that would be rule number one" |
| **Harvest losses on trend breaks.** Sell, take the tax loss, and redeploy to higher-probability positions. | 09-21 "bearish trade and trend on our signal, just sell it. Take the tax loss."; 10-15; 10-01 |
| **Don't add to a broken trade to "average down".** | 09-02 "the idea isn't to lever up the trade" |
| **Don't chase.** Buy on red, sell on green. | 08-25 (BTC FOMO); 09-22 "Please don't chase Bitcoin"; 09-25 |
| **Tickers aren't relationships.** | 08-25 "They're just tickers. They're not personal relationships." |
| **Index weights are the bubble.** Don't hug the benchmark. | 09-15 "The index weights are the bubble"; 10-15 |
| **Rotate, don't buy-and-hold.** | 09-02 "You don't sit on it, rotate" |
| **Direction over probability. Timing over the idea.** | 08-04 "much more important to get the direction right"; 09-08 "It's about how good your timing is" |
| **Separate P&L from life.** | 10-16 "I had a great day in life. I had a bad day in the portfolio." |

---

## 10. Questions to ask every morning (agent checklist)

Run these in order and report facts only.

1. **Trend breaks:** did any held name close below its TREND line? → Flag it as a **STOP**, plus a tax-harvest note if it's at a loss. (§2)
2. **Trade breaks:** did any held name close below its TRADE line while the trend held? → Flag **reduce to minimum / stop buying**. (§2)
3. **State changes:** list every color flip on the Risk Range overnight, for held names and watchlist names. Is today day one of a reversal? → If so, no big adds. (§2, §8)
4. **Sizing vs. bands:** is any position below its minimum (commodity/crypto 1%, equity 2%) or above its maximum (commodity/crypto 4%)? Is any add larger than 100 bp? (§3)
5. **Range position:** which bullish/bullish names sit at rp ≤ 0.2 (add candidates)? Which sit at rp ≥ 0.8 (trim candidates)? Which ranges made a lower high? (§2)
6. **Shorts:** are any bearish/bearish names bouncing toward the top of the range (short-the-bounce)? Did any short flip to bullish trade/trend, or reach the low end or oversold (cover to minimum)? Does any short have a positive 1-month return? (§2, §7)
7. **Vol buckets:** VIX vs 19 and vs its own trend line. RVX vs 21. NASVOL vs ~22. MOVE vs its trend line. Did any bucket change? (§4)
8. **Credit:** HY OAS vs 300 bp, and the change over 1 day and 1 month. (§6)
9. **Gamma:** SPX vs the T1A flip line, and short-dated vol vs 12–14. Is 1-month vol above 3-month vol? (§4, §6)
10. **Macro lights:** USD trade/trend, the 2-year and 10-year vs their lines, the 2s10s direction, and the BTC–USD correlation sign. (§6)
11. **Quad:** current monthly quad, and whether this week is counter-trend or a real change (check the inflation direction). Days until the January Quad 4. (§5)
12. **Lists:** for each holding, is it on the SS long list, the SS short list, or neither (bench)? What was added or removed overnight? What is the long/short count? (§8)
13. **Momentum table:** 1w/1m/3m for every holding. Flag any 1-month sign flip. (§7)
14. **Behavior check:** is any proposed action driven by a narrative, a P&L reaction, or chasing? If so, drop it. (§9)

---

## Gaps and contradictions in the source

- **Gold sizing:** in August Keith had a 3% minimum and a maximum of up to 12% (08-13). In September and October the commodity band is 1%/4% (09-10, 10-15), and he broke his own gold minimum twice (08-10, 09-22).
- **Small-cap/Nasdaq chop line:** 21 (08-07), then 22 (08-27), then a "dynamic" 21.56–21.76 (09-02, 09-04).
- **No F-bucket threshold** is given for the VIX, and no equity maximum % is stated.
- **QQQ "at minimum":** 3% on 10-16 ("a little bit above my minimum") vs 2% on 10-19.
- **Position Monitor** is never discussed.
- **TAIL** is defined but has no rules attached.
- **The BTC–USD correlation sign flipped** during the period.
- The "bleeding edge" line about Signal Strength additions was **RPK's**, with Keith agreeing. Earlier notes attribute it to Keith.
