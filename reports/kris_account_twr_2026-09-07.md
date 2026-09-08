# T9 — account TWR (Modified Dietz), 2026-09-07

Snapshots: 41, 2026-05-11 .. 2026-09-04, per-account, CASH INCLUDED (read from the raw CSVs — the DB parser drops cash rows, which is why a first pass looked catastrophic).

**The Individual account (X96383748) has NO computable TWR from these exports**: it runs a margin debit, and Fidelity position exports show assets only, never the loan. Deposits that pay down the debit look like vanished money (gross assets 28,063 -> 21,747 while 18,800 of EFT deposits landed). Its true equity return needs the Balances export or Fidelity's own Performance page. Reported below: the two margin-free, flow-free accounts, where Dietz is exact.

## May 11 -> Sep 4, chained (IRAs: no margin, no flows)
Rollover IRA   total  +0.13%  annualized   +0.42%  maxDD(snapshot)  -1.60%
Roth IRA       total  +1.29%  annualized   +4.12%  maxDD(snapshot)  -0.38%
SPY            total  +4.45%  annualized  +14.68%  maxDD(snapshot)  -3.38%
60/40          total  +1.70%  annualized   +5.44%  maxDD(snapshot)  -2.79%

## Individual account — raw series (NOT a return; margin debit invisible)
gross assets 28,063 -> 21,747 USD; EFT deposits in window: +18,800 USD. Change net of deposits: -25,116 USD — an UPPER BOUND on losses only if the margin debit was unchanged, which we cannot verify from these files.

PNG: t9_account_twr_2026-09-07.png

Caveats: window = snapshot coverage (2026-05-11 on; earlier book invisible); benchmarks on adjusted closes; IRA numbers are exact Dietz (no margin, no external flows); the whole-book and Individual numbers are NOT computable without margin balances — get them from Fidelity's Performance page or a Balances export.