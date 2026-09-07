# Hedgeye mail-archive parse — 2a/2b/2c coverage (2026-09-07)

Roadmap §2 step 2. Source: 19,055-message IMAP manifest (read-only,
`railway run`), 6,814 parse-product bodies archived to
`data/hedgeye_mail/` (gitignored). Full parse: **0 errors**.

## 2b — hedgeye_risk_ranges backfill

- 1,074 Risk Range mails → 28,548 parsed rows; **22,859 new rows
  backfilled** (live-feed rows preserved via ON CONFLICT DO NOTHING;
  provenance in `source_uid`).
- Table now: **28,073 rows · 81 instruments · 825 signal days ·
  2023-04-24 → 2026-09-04**. ~33–36 instruments per day, stable across
  years (2023 avg 33.4 → 2026 avg 35.6).

### Daily core (>90% of signal days) — 29 instruments

| tier | instruments |
|---|---|
| 100% | AMZN, GOOGL, META, MSFT, NFLX, NVDA, RUT, SPX, TSLA, VIX (825d) |
| ≥98% | AAPL, HYG, CAD/USD, COMPQ, COPPER, DAX, EUR/USD, GBP/USD, NATGAS, NIKK, SSEC, USD, USD/YEN, WTIC, BRENT, GOLD |
| ≥90% | XLK (dropped 2026-08-11), SILVER, BITCOIN (from 2023-08) |

### Rotating bench (partial coverage)

LQD 78% (from 2024-02) · BSE 63% (ended 2025-12) · XLU 47% (ended
2026-07-30) · XLE 36% · UST30Y 36% · ORCL 27% (from 2025-09) · XLI 23% ·
XLF 20% · PINK 19% · XLV 17% · IAK 17% · SPMO 14% · UST2Y/UST10Y 14% ·
GDX 12% · URA 10% · XLRE 10% · XOP 9% · rest <9% (episodic sector/single
-name rotations; SNOW appeared 2026-09-04).

**Known wart:** 2026-02-13→02-26 the email format briefly switched symbol
style (EURUSD/GCUSD/CLUSD/GDAXI/N225/… ≈ 9 days) — duplicates of the
slash-style names. Normalize aliases before T2 if that window matters.

## 2a — stated Quad + inflation nowcast

- `hedgeye_quad_stated`: 2,898 rows (6,735 raw mentions, frequency kept
  in `n_hits`, snippet stored per row for audit). Monthly-scope prose:
  2022-03 → 2026-09 (dense from 2023-04). Quarterly GIP/path statements:
  135 rows, 2023-05 → 2026-08 (mostly ETF Pro/Re-Rank + Themes/MidQ).
- `inflation_nowcast`: 90 base-case headline CPI observations,
  2024-09 → 2026-08 (weekly-updated monthly nowcast; upside/downside
  scenarios live in image tables, not email text).

## 2c — quad_nowcast_daily + nowcast_vs_realized

- `quad_nowcast_daily`: 1,126 trading days with a monthly dial, 840 with
  a quarterly dial (dial moves only on statements about the
  current month/quarter; per-note majority weighted by `n_hits`).
- `nowcast_vs_realized` (migration 096): stated = latest in-period note
  (per-note mention-majority), realized = FRED `quad_monthly`.

| period | stated | last note | realized | match |
|---|---|---|---|---|
| 2Q23 | 4 | 2023-05-30 | 1 | ✗ |
| 3Q23 | 1 | 2023-09-07 | 1 | ✓ |
| 4Q23 | 3 | 2023-11-15 | 1 | ✗ |
| 1Q24 | 4 | 2024-02-14 | 3 | ✗ |
| 2Q24 | 3 | 2024-05-16 | 1 | ✗ |
| 3Q24 | 3 | 2024-09-04 | 4 | ✗ |
| 4Q24 | 2 | 2024-12-03 | 3 | ✗ |
| 1Q25 | 4 | 2025-03-30 | 3 | ✗ |
| 2Q25 | 4 | 2025-06-09 | 1 | ✗ |
| 3Q25 | 3 | 2025-06-13 | 2 | ✗ |
| 4Q25 | 2 | 2025-10-29 | 4 | ✗ |
| 1Q26 | 1 | 2026-02-11 | 1 | ✓ |
| 2Q26 | 3 | 2026-06-10 | 3 | ✓* |
| 4Q26 | 3 | 2026-08-10 | — | pending |

Quarterly hit rate vs our FRED-realized rule: **3/13**. Monthly scope:
7/41. (Caveat: "stated" is parsed from email prose, and our realized
rule ≠ Hedgeye's model — this measures their email-visible call against
our FRED definition, nothing more.)

**\*2Q26 anchor case, honest note:** the email text's last in-quarter
*quarterly-path* statement (ETF Pro, 6/10) read 2Q26 = Quad 3. The June
**Quad-4** call the desk remembers shows up in the *monthly* scope: June
2026 dial = Quad 4 (6/30 Macro Show, 11 mentions vs 4 for Quad 1) vs
realized Quad 3 → the miss is captured there. The Quad-4 2Q26E table was
deck-image content, not email text.

## Gate

Stopped before T1/T2 per operator instruction — T2 universe selection
waits on this instrument list.
