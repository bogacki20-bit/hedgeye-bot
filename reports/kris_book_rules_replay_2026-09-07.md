# T8 — rules-replay of Kris's 2026 long book (2026-09-07)

## Coverage: 23/314 names on real signals (HEDGEYE 15, MFR 8); 9% of episodes, 13% of episode dollars. Rest on PROXY (20d range / SMA50), labeled.

## R4 spike gate: dropped 948 adds, 129,771 USD (34% of all buy fills).

## Aggregates (long episodes only)
variant          P&L $  per $ invested
ACTUAL            +496            0.1%
RULES           +1,199            0.3%
HOLD-9/4        +5,403            1.3%
  HEDGEYE    n= 29  actual      -235  rules      -143  hold    +1,074
  MFR        n= 10  actual      +125  rules       +51  hold      +148
  PROXY      n=381  actual      +606  rules    +1,292  hold    +4,181

## Per-episode (top 30 by |actual-rules| gap)
sym    src      open          actual$   rules$    hold$     gap$ r4drops
ABXXF  PROXY    2026-05-15        608        5     -124     -603       0
USO    HEDGEYE  2026-02-12        476        0    1,038     -476      18
SBIT   PROXY    2026-06-11       -209      120     -981      329       6
OKTA   PROXY    2026-07-29        336       26      390     -310       8
NVDA   HEDGEYE  2026-04-30       -306      -28      438      278       2
AAPL   HEDGEYE  2026-01-05       -249       -4      135      245       2
UGA    PROXY    2026-02-17        278       54    1,114     -224      20
VSXY   PROXY    2026-01-02       -166       50      403      216       8
FXH    PROXY    2026-06-12        207       37      291     -170      16
TXG    PROXY    2026-07-16        160        0      221     -160       6
DRAM   PROXY    2026-05-18        440      291      212     -148       4
BUG    PROXY    2026-07-13        -97       51       63      148      12
CRWD   PROXY    2026-08-18         97      -46       86     -143       0
XOP    PROXY    2026-02-02        157       16      367     -141      10
TSLA   HEDGEYE  2026-01-02        141        4      -58     -137       0
UNH    PROXY    2026-04-24        170       35       72     -135       6
ARKG   PROXY    2026-07-10        -39       93      189      131       0
AMZN   HEDGEYE  2026-05-12       -125        4      -24      130       2
URA    PROXY    2026-02-02        -79       50     -175      129       0
BMNZ   PROXY    2026-02-06        102      -26     -872     -128       0
PANW   PROXY    2026-08-20        -48       65      -34      113       0
OIH    PROXY    2026-02-05        185       75      282     -110       9
BRKR   PROXY    2026-06-12         81      190       88      109       6
WFC    PROXY    2026-08-19         68      -37       84     -106       0
SN     PROXY    2026-06-25        105        0       97     -105       3
COM    PROXY    2026-03-17        -95        9      131      104      16
PSX    PROXY    2026-07-23        100        0      151     -100       4
XAR    PROXY    2026-06-02        -69       30     -143       99       0
UFO    PROXY    2026-05-29         13      -83     -385      -96       4
RDNT   PROXY    2026-07-09        -47       45      151       92       4

PNG: t8_rules_replay_2026-09-07.png. Final: ACTUAL +496 | RULES +1,199 | HOLD +5,403 | SPY +7,173 USD.

Caveats: longs only; sells at closes, no costs/slippage; R1 triggers on 0.9-crossings, R2a once per streak; starter = first buy leg's shares; AUM before 5/11 = the 5/11 snapshot (Jan-2 statement not in DB); PROXY names use 20d range / SMA50; dividends excluded.