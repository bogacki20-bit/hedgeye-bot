# `reports/` — generated weekly + period summaries

I write these when you ask. Folder per ISO week (`YYYY-Www`, e.g. `2026-W19`).

## Suggested files per week

**`weekly_summary.md`** — what happened, what was traded, what worked, what
didn't, what to watch next week. Compiled from that week's `snapshots/` and
`decisions/` folders.

**`alignment_patterns.md`** — the cross-vendor (Hedgeye vs SpotGamma) pattern
register for the week. When did Keith's calls agree with SpotGamma's flow read?
When did they diverge? What happened in each case?

## When to ask me to write one

Weekend morning is the natural rhythm. "Pull together this week's report" —
I read across the week's snapshots and decisions, write the two files,
attach to a Dispatch message so you can read on your phone.

## Note for after monitoring ships

Once `alerts_log` + `actions_log` + `outcomes_log` are live in Postgres, the
weekly report will pull from those tables in addition to the file snapshots.
The structured tables give clean trade-level outcomes; the snapshots give
narrative context. Reports synthesize both.
