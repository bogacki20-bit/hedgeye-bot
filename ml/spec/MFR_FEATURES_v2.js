// ============================================================================
//  MFR feature scripts v2 — for the 8-year custom symbols (daily charts)
//  Paste each block as its own custom indicator. ≤6 request.history per script.
//  All symbols are finished, bounded values from hedgeye-bot; scripts only
//  fetch, land on the chart, forward-fill, and clamp where needed.
//  Shared helper block is repeated in each script (TrendSpider scripts are standalone).
// ============================================================================


// ------------------------------------------------------------ MFR_MACRO_RP --
// Range position of the macro block: SPY (beta), UUP, VIX, USO, AAAU + SPY LT range
describe_indicator('MFR_MACRO_RP', 'lower', { decimals: 3 });
const RES = 'D';
const isNum = (x) => typeof x === 'number' && isFinite(x);
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        return interpolate_sparse_series(land_points_onto_series(h.time, h.close, time), 'constant');
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + e); return series_of(NaN); }
}
const [SPY, UUP, VIX, USO, AAAU, SPYLT] = await Promise.all([
    feat('#MFR_SPY_RP'), feat('#MFR_UUP_RP'), feat('#MFR_VIX_RP'),
    feat('#MFR_USO_RP'), feat('#MFR_AAAU_RP'), feat('#MFR_SPY_LTRP'),
]);
paint(SPY,   { name: 'spy_rp',   color: '#4caf50' });
paint(UUP,   { name: 'uup_rp',   color: '#29b6f6' });
paint(VIX,   { name: 'vix_rp',   color: '#ef5350' });
paint(USO,   { name: 'uso_rp',   color: '#8d6e63' });
paint(AAAU,  { name: 'aaau_rp',  color: '#ffb300' });
paint(SPYLT, { name: 'spy_ltrp', color: '#ab47bc' });
// -------------------------------------------------------------------------


// --------------------------------------------------------- MFR_MACRO_TREND --
// Trend tag (+1/0/−1) of the macro block + SPY distance to bullish trend level
describe_indicator('MFR_MACRO_TREND', 'lower', { decimals: 3 });
const RES = 'D';
const isNum = (x) => typeof x === 'number' && isFinite(x);
const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        return interpolate_sparse_series(land_points_onto_series(h.time, h.close, time), 'constant');
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + e); return series_of(NaN); }
}
const [SPY, UUP, VIX, USO, AAAU, BD] = await Promise.all([
    feat('#MFR_SPY_TREND'), feat('#MFR_UUP_TREND'), feat('#MFR_VIX_TREND'),
    feat('#MFR_USO_TREND'), feat('#MFR_AAAU_TREND'), feat('#MFR_SPY_BULLDIST'),
]);
const bulldist = for_every(BD, (v) => (isNum(v) ? clamp(v, -0.3, 0.3) : NaN)); // outlier clamp (2020)
paint(SPY,      { name: 'spy_trend',    color: '#4caf50' });
paint(UUP,      { name: 'uup_trend',    color: '#29b6f6' });
paint(VIX,      { name: 'vix_trend',    color: '#ef5350' });
paint(USO,      { name: 'uso_trend',    color: '#8d6e63' });
paint(AAAU,     { name: 'aaau_trend',   color: '#ffb300' });
paint(bulldist, { name: 'spy_bulldist', color: '#ab47bc' });
// -------------------------------------------------------------------------


// --------------------------------------------------------------- MFR_CORR --
// Hedgeye "Key $USD Correlations": UUP vs SPY / USO / AAAU at 30d and 90d
describe_indicator('MFR_CORR', 'lower', { decimals: 3 });
const RES = 'D';
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        return interpolate_sparse_series(land_points_onto_series(h.time, h.close, time), 'constant');
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + e); return series_of(NaN); }
}
const [S30, S90, O30, O90, G30, G90] = await Promise.all([
    feat('#CORR_UUP_SPY30'),  feat('#CORR_UUP_SPY90'),
    feat('#CORR_UUP_USO30'),  feat('#CORR_UUP_USO90'),
    feat('#CORR_UUP_AAAU30'), feat('#CORR_UUP_AAAU90'),
]);
paint(S30, { name: 'usd_spy30',  color: '#4caf50' });
paint(S90, { name: 'usd_spy90',  color: '#81c784' });
paint(O30, { name: 'usd_uso30',  color: '#8d6e63' });
paint(O90, { name: 'usd_uso90',  color: '#bcaaa4' });
paint(G30, { name: 'usd_aaau30', color: '#ffb300' });
paint(G90, { name: 'usd_aaau90', color: '#ffe082' });
// -------------------------------------------------------------------------


// ----------------------------------------------------------- MFR_SPY_HIST --
// SPY-specific 8-year features: shadow Hurst, SPY↔UUP 60d corr, LT range positions
describe_indicator('MFR_SPY_HIST', 'lower', { decimals: 3 });
const RES = 'D';
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        return interpolate_sparse_series(land_points_onto_series(h.time, h.close, time), 'constant');
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + e); return series_of(NaN); }
}
const [HURST, C60, UUPLT, VIXLT, USOLT, AAAULT] = await Promise.all([
    feat('#SHADOW_SPY_HURST'), feat('#CORR_SPY_UUP60'),
    feat('#MFR_UUP_LTRP'), feat('#MFR_VIX_LTRP'), feat('#MFR_USO_LTRP'), feat('#MFR_AAAU_LTRP'),
]);
paint(HURST,  { name: 'spy_hurst',  color: '#29b6f6' });
paint(C60,    { name: 'spy_uup60',  color: '#4caf50' });
paint(UUPLT,  { name: 'uup_ltrp',   color: '#90caf9' });
paint(VIXLT,  { name: 'vix_ltrp',   color: '#ef5350' });
paint(USOLT,  { name: 'uso_ltrp',   color: '#8d6e63' });
paint(AAAULT, { name: 'aaau_ltrp',  color: '#ffb300' });
// -------------------------------------------------------------------------


// -------------------------------------------------------------- MFR_DECEL --
// Port of hedgeye-bot volume_signal.py — computed from THIS chart's own volume,
// so training and live are internally consistent. No request.history calls.
//   decel_streak: consecutive sessions ending today with close<prior close AND vol<prior vol
//   decelerating: OLS slope (normalised by mean vol) of the last 3 down-day volumes
//                 within the trailing 12 sessions is < 0  (needs ≥2 down days)
//   price_down_3d: close/close[3] − 1 < 0
//   value: price_down_3d && !decelerating → −1 (distribution); else min(streak,7)
describe_indicator('MFR_DECEL', 'lower', { decimals: 3 });
const isNum = (x) => typeof x === 'number' && isFinite(x);
const n = close.length;
const out = new Array(n).fill(NaN);
const streakS = new Array(n).fill(NaN);
const distS = new Array(n).fill(NaN);
for (let t = 12; t < n; t++) {
    // streak
    let s = 0, k = t;
    while (k >= 1 && close[k] < close[k - 1] && volume[k] < volume[k - 1]) { s++; k--; }
    // decelerating: last 3 down days in trailing 12
    const vols = [];
    for (let j = t; j > t - 12 && j >= 1 && vols.length < 3; j--) {
        if (close[j] < close[j - 1]) vols.push(volume[j]);
    }
    let decel = null;
    if (vols.length >= 2) {
        vols.reverse(); // chronological
        const m = vols.length, xm = (m - 1) / 2;
        const ym = vols.reduce((a, b) => a + b, 0) / m;
        let num = 0, den = 0;
        for (let i = 0; i < m; i++) { num += (i - xm) * (vols[i] - ym); den += (i - xm) * (i - xm); }
        decel = (num / den) / ym < 0;
    }
    const down3 = t >= 3 ? close[t] / close[t - 3] - 1 < 0 : false;
    let v;
    if (down3 && decel === false) v = -1;
    else v = Math.min(s, 7);
    out[t] = v / 7;                 // bounded: −0.14 .. 1
    streakS[t] = s;
    distS[t] = (down3 && decel === false) ? 1 : 0;
}
paint(out,    { name: 'decel',        color: '#ab47bc' });
paint(distS,  { name: 'distribution', color: '#ef5350', hidden: true });
// -------------------------------------------------------------------------
