# MASTER THE MARKET — Quad definition extract (reconciliation reference)

Provided by the operator 2026-09-07 from the trading-desk project doc
`claude/MASTER_THE_MARKET_McCullough_2025.md` (McCullough eBook, 1/29/2025),
Part 2 (GIP construction) only. Static reference for the quad_monthly
cross-check — never a live signal.

## 2.1 Construction (Ch. 2, p.25-26)
Growth = real GDP YoY; Inflation = headline CPI YoY; both on a
year-over-year rate-of-change basis; accelerating/slowing = this quarter's
YoY vs the prior quarter's YoY (second derivative).

- QUAD 1 — Growth accelerating, Inflation slowing
- QUAD 2 — Growth accelerating, Inflation accelerating
- QUAD 3 — Growth slowing, Inflation accelerating
- QUAD 4 — Growth slowing, Inflation slowing

## Reconciliation notes
- load_quad.py implements sign(D YoY GDPC1) x sign(D YoY CPIAUCSL) q/q —
  matches. CPI method used by the bot: **YoY of the quarterly average
  index** (not average of monthly YoY prints); both computed in the
  reconciliation table, quads compared.
- Hedgeye's PUBLISHED Quad is a nowcast/forecast; disagreement with the
  realized series is expected and is a finding, not a bug.
- Desk anchors: 2Q26 stated Quad 4 (June 3Q26 themes deck); 3Q26 stated
  hybrid Quad 1 / Quad 4 (July Monthly Monitor).

## 2.2 Quad playbook (doctrine table for the eventual quad-compliant join)
- Q1 best: Tech/Disc/Materials/Industrials · worst: Utilities/REITs/Staples/Financials
- Q2 best: Tech/Disc/Industrials/Materials · worst: Telecom/Utilities/REITs/Staples
- Q3 best: Utilities/Tech/Energy/Industrials · worst: Financials/REITs/Materials/Telecom
- Q4 best: Staples/Utilities/REITs/Health Care · worst: Energy/Tech/Industrials/Financials
- Asset classes: Q1 equities/credit/commodities/FX best, FI/USD worst;
  Q2 commodities/equities/credit/FX best, FI/USD worst; Q3 gold/commodities
  best, credit worst; Q4 FI/gold/USD best, commodities/equities/credit/FX worst.
