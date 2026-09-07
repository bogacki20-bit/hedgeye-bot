# Phase B(c) — commodity-dip vs vol regimes (2026-09-07)

Commodity bars (USO/GLD/AAAU) with targets: 6049, 2018-03-01 .. 2026-08-07. Own-vol joins: OVX for USO, GVZ for GLD/AAAU. Bands are stated in-sample terciles. Baseline = commodity setup_any in the SAME cell. Rules study only — the ranker stays retired.
Coverage: vix 100% · ts 100% · own_vol 100% of bars. TS distribution: contango<0.9 56% · 0.9-1.0 36% · backwardation>1 8%

### commodity lrr x VIX band
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
<20                759  52.7%  +0.82%   3784  58.0%  [ -8.5, -2.0] **
20-30              231  71.0%  +4.15%   1809  57.0%  [ +8.7,+19.2] **
>30                 50  72.0%  +2.92%    456  61.0%  [ -0.1,+21.9] (n<100)

### commodity lrr x VIX/VIX3M term structure
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
<0.9 contango      587  53.2%  +1.04%   3366  56.4%  [ -6.9, +0.4]
0.9-1.0            405  63.2%  +2.36%   2198  60.0%  [ -1.1, +7.4]
>1.0 backwd         48  66.7%  +3.25%    485  58.4%  [ -3.3,+19.8] (n<100)

### commodity lrr x OWN vol index band (OVX/GVZ)
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
high               212  60.8%  +2.59%   2186  54.8%  [ +0.4,+11.8] **
low                387  57.1%  +1.26%   1666  58.8%  [ -6.2, +3.0]
mid                440  56.6%  +1.55%   2185  60.5%  [ -8.1, +0.3]

### commodity dip x VIX band
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
<20                265  56.2%  +1.36%   3784  58.0%  [ -6.9, +3.4]
20-30               79  77.2%  +6.01%   1809  57.0%  [+12.1,+28.2] ** (n<100)
>30                 18  72.2%  +2.72%    456  61.0%    thin   (n<100)

### commodity dip x VIX/VIX3M term structure
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
<0.9 contango      204  58.3%  +2.14%   3366  56.4%  [ -4.0, +7.7]
0.9-1.0            140  65.7%  +3.10%   2198  60.0%  [ -1.1,+12.4]
>1.0 backwd         18  66.7%  +0.72%    485  58.4%    thin   (n<100)

### commodity dip x OWN vol index band (OVX/GVZ)
band                 n    hit   ret20   anyN anyHit  hit-diff 90% CI
high                76  67.1%  +4.03%   2186  54.8%  [ +3.2,+21.3] ** (n<100)
low                136  56.6%  +1.50%   1666  58.8%  [ -9.5, +5.0]
mid                150  63.3%  +2.49%   2185  60.5%  [ -4.1, +9.6]

### the cross: dip x own-vol MID, split by term structure
  own-mid & <0.9 contango: n=93 hit=54.8% ret=+1.79% (any 59.2%) CI [-13.2,+4.4]
  own-mid & 0.9-1.0: n=53 hit=77.4% ret=+4.00% (any 62.0%) CI [+5.0,+25.0]
  own-mid & >1.0 backwd: n=4 hit=75.0% ret=-1.44% (any 70.0%) CI thin

### multiple-testing guard
Cells tested: 18; expected chance positives at 90% ~0.9. Observed 4 positive / 1 negative CIs excluding zero. n<100 cells flagged.