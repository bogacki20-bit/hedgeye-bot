// Retrieved 2026-09-07 from the TrendSpider Custom Indicators editor (5,410 chars) — the
// authoritative feature spec for ml/build_features.py. Do not edit; edits happen in TrendSpider.
//
// MFR_CORE_ALL — one script for every enrolled asset. Reads the chart's own ticker and pulls
// #MFR_<TICKER>_* so a single ML model can train on SPY+USO+UUP+AAAU+TLT at once (pooled regimes).
// 6 fetches (runtime limit): LO, HI, BULLDIST, HURST64, HURST256, TRENDLVL (round-2 symbols).
// No macro block here (fetch budget) — the pooled model's "context" is the asset's own regime:
// distance to the TREND duration line (Keith's gate), Hurst (trend persistence), range dynamics.
describe_indicator('MFR_CORE_ALL', 'lower', { decimals: 3 });
const RES = 'D';
const isNum = (x) => typeof x === 'number' && isFinite(x);
const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
const lastT = time[time.length - 1];
// chart ticker -> symbol stem (VIX charts would be $VIX -> 'VIX'; enrolled assets are plain ETFs)
let TK = 'SPY';
try { TK = String(constants.ticker || TK).replace(/^[\$\^#]/, '').split(':').pop().toUpperCase(); } catch (e) {}
console.log('MFR_CORE_ALL ticker=' + TK);
function land(ts, vs) {
    const landed = interpolate_sparse_series(land_points_onto_series(ts, vs, time), 'constant');
    const out = [];
    for (let i = 0; i < time.length; i++) out.push(isNum(landed[i]) ? landed[i] : NaN);
    return out;
}
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        const ts = [], vs = [];
        for (let i = 0; i < h.time.length; i++) { if (h.time[i] <= lastT) { ts.push(h.time[i]); vs.push(h.close[i]); } }
        console.log(sym + ' rows=' + ts.length);
        return land(ts, vs);
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + JSON.stringify(e && (e.message || e))); return series_of(NaN); }
}
const [LO, HI, BD, H64, H256, TRL] = await Promise.all([
    feat('#MFR_' + TK + '_LO'), feat('#MFR_' + TK + '_HI'), feat('#MFR_' + TK + '_BULLDIST'),
    feat('#MFR_' + TK + '_HURST64'), feat('#MFR_' + TK + '_HURST256'), feat('#MFR_' + TK + '_TRENDLVL'),
]);
const n = close.length;
const lag = (s, k) => { const o = []; for (let i = 0; i < n; i++) o.push(i >= k && isNum(s[i - k]) ? s[i - k] : NaN); return o; };
const pct = (a, b) => for_every(a, b, (x, y) => (isNum(x) && isNum(y) && y !== 0 ? x / y - 1 : NaN));
const diff = (a, b) => for_every(a, b, (x, y) => (isNum(x) && isNum(y) ? x - y : NaN));

// --- state ---
const rp = for_every(close, LO, HI, (c, lo, hi) => isNum(lo) && isNum(hi) && hi > lo ? clamp((c - lo) / (hi - lo), -0.5, 1.5) : NaN);
const rp_ma20 = sliding_window_function(rp, 20, (w) => { const v = w.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN; });
const rp_dev = diff(rp, rp_ma20);
const bulldist = for_every(BD, (v) => (isNum(v) ? clamp(v, -0.5, 0.5) : NaN));
const rng_width = for_every(HI, LO, close, (hi, lo, c) => (isNum(hi) && isNum(lo) && c > 0 ? (hi - lo) / c : NaN));
const hurst64 = for_every(H64, (v) => (isNum(v) ? clamp(v, 0, 1) : NaN));
const hurst256 = for_every(H256, (v) => (isNum(v) ? clamp(v, 0, 1) : NaN));
const trend_dist = for_every(close, TRL, (c, t) => (isNum(t) && c > 0 ? clamp((c - t) / c, -0.5, 0.5) : NaN)); // + above TREND, - below
const above_trend = for_every(trend_dist, (v) => (isNum(v) ? (v > 0 ? 1 : 0) : NaN));

// --- rate of change ---
const rp_d3 = diff(rp, lag(rp, 3));
const bulldist_d3 = diff(bulldist, lag(bulldist, 3));
const hi_d3 = for_every(pct(HI, lag(HI, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HH (+) vs LH (-)
const lo_d3 = for_every(pct(LO, lag(LO, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HL (+) vs LL (-)
const trend_dist_d3 = diff(trend_dist, lag(trend_dist, 3));
const hurst64_d5 = diff(hurst64, lag(hurst64, 5));

// --- decel (port of volume_signal.py, chart volume) ---
const decel = [];
for (let i = 0; i < n; i++) decel.push(NaN);
for (let t = 12; t < n; t++) {
    let s = 0, k = t;
    while (k >= 1 && close[k] < close[k - 1] && volume[k] < volume[k - 1]) { s++; k--; }
    const vols = [];
    for (let j = t; j > t - 12 && j >= 1 && vols.length < 3; j--) { if (close[j] < close[j - 1]) vols.push(volume[j]); }
    let dec = null;
    if (vols.length >= 2) {
        vols.reverse();
        const m = vols.length, xm = (m - 1) / 2;
        const ym = vols.reduce((a, b) => a + b, 0) / m;
        let num = 0, den = 0;
        for (let i = 0; i < m; i++) { num += (i - xm) * (vols[i] - ym); den += (i - xm) * (i - xm); }
        dec = ym > 0 ? (num / den) / ym < 0 : null;
    }
    const down3 = t >= 3 ? close[t] / close[t - 3] - 1 < 0 : false;
    decel[t] = ((down3 && dec === false) ? -1 : Math.min(s, 7)) / 7;
}

paint(rp, { name: 'rp', color: '#4caf50' });
paint(rp_dev, { name: 'rp_dev20', color: '#81c784' });
paint(rp_d3, { name: 'rp_d3', color: '#a5d6a7' });
paint(bulldist, { name: 'bulldist', color: '#ab47bc' });
paint(bulldist_d3, { name: 'bulldist_d3', color: '#ce93d8' });
paint(hi_d3, { name: 'hi_d3', color: '#29b6f6' });
paint(lo_d3, { name: 'lo_d3', color: '#90caf9' });
paint(rng_width, { name: 'rng_width', color: '#607d8b' });
paint(hurst64, { name: 'hurst64', color: '#ef5350' });
paint(hurst256, { name: 'hurst256', color: '#e57373' });
paint(hurst64_d5, { name: 'hurst64_d5', color: '#ffab91' });
paint(trend_dist, { name: 'trend_dist', color: '#ffb300' });
paint(trend_dist_d3, { name: 'trend_dist_d3', color: '#ffe082' });
paint(above_trend, { name: 'above_trend', color: '#fff176' });
paint(decel, { name: 'decel', color: '#bcaaa4' });
