# `data/` — the corpus

Everything we capture, write, decide, and learn during the build phase lives here.
Once monitoring goes live (May 12 target), the bot's `alerts_log`, `actions_log`,
and `outcomes_log` Postgres tables become the canonical structured layer; this
folder remains the human-and-agent-readable layer for pattern review.

Both layers stay populated forever. Postgres is the engine room (queryable);
this folder is the library (readable, searchable, git-versioned).

## Layout

```
data/
├── README.md                                 (this file)
│
├── snapshots/                                Daily market data captures
│   ├── spotgamma/
│   │   └── YYYY-MM-DD/
│   │       ├── market_overview.md
│   │       └── <TICKER>.md
│   └── hedgeye/
│       └── YYYY-MM-DD/
│           ├── risk_range.md
│           ├── portfolio_solutions.md
│           ├── etf_pro.md                   (Mondays — weekly)
│           ├── early_look.md
│           └── rta_log.md
│
├── decisions/                                Trades + observations + the WHY
│   └── YYYY-MM-DD/
│       ├── morning_brief.md                 (what the data said pre-market)
│       ├── trades.md                        (what got executed in Fidelity)
│       └── observations.md                  (notes, learnings, things to revisit)
│
├── reference/                                Static reference material
│   ├── trading_framework.md                 (Quad, Risk Range thirds, sector caps)
│   ├── watchlist.md                         (active tickers under monitoring)
│   └── (Daily Market Analysis project files when copied in)
│
└── reports/                                  Generated period summaries
    └── YYYY-Www/                             (ISO week, e.g. 2026-W19)
        ├── weekly_summary.md
        └── alignment_patterns.md
```

## How each piece is used

**`snapshots/`** — what each vendor said on each day. Captured manually during
walkthroughs (like tonight's OIH/HYG/market_overview), automated by the
SpotGamma + Hedgeye scrapers when they ship post-May 12. Read these for "what
was the picture on date X" questions.

**`decisions/`** — Kristian's view of the day. Pre-market brief (what the
alignment looked like), trades executed (what was clicked in Fidelity Mobile),
observations (anything notable). One folder per day. Three files per folder.
Brief — short paragraphs, not essays. The point is to capture what was THOUGHT,
not just what was DONE — so future weeks can compare reasoning to outcomes.

**`reference/`** — frozen documents that don't change daily. Trading framework,
watchlist, anything from the Daily Market Analysis project Kristian maintains in
Claude.ai that's worth pulling local. Read for context when reasoning, not for
daily updates.

**`reports/`** — Kristian asks me on a weekend to compile the week. I read
across `snapshots/` and `decisions/` for that week and write a `weekly_summary.md`
covering what alignment patterns appeared, what trades were made, what worked
and didn't, what to watch into the next week. `alignment_patterns.md` is the
specific cross-vendor (Hedgeye vs SpotGamma) pattern register — when did the
two frameworks agree, when did they diverge, and what happened.

## Phone access pattern

During workdays, query the corpus through Dispatch — "what did we say about
HYG yesterday?" — and I'll pull the relevant snapshot and decision files and
answer. The corpus is for reading by me on your behalf, not for direct mobile
browsing. (Though you CAN browse via github.com/bogacki20-bit/hedgeye-bot/tree
/master/data on your phone if you want.)

## When the bot's ML logging goes live

After May 12 monitoring ships, three new Postgres tables (`alerts_log`,
`actions_log`, `outcomes_log`) capture the same kind of data in structured form.
This `data/` folder stays — Postgres is structured-and-queryable, this folder
is readable-and-searchable. Both are useful; they're not redundant.
