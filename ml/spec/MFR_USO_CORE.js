// MFR_USO_CORE — v3 feature set on USO (oil) daily. Non-SPY control asset: mixed regimes 2019–26.
// 6 fetches (runtime limit): #MFR_USO_LO, #MFR_USO_HI, #MFR_USO_BULLDIST, #MFR_VIX_LO, #MFR_VIX_HI, HYG.
// ($VIX index is not reachable via request.history → VIX level proxied by the bot's VIX range midpoint;
//  mid/close ≈ 0.99, same Hedgeye bucket 87% of days per TVC_VIX_1D check. RORO = HYG 10d ROC, credit-only.)
// Adds vs v2: 3-day deltas (rp, bulldist), range dynamics (hi/lo 3d change = HH/HL vs LH/LL),
// VIX level + Hedgeye bucket + 5d ROC, HYG 10d ROC (credit RORO). Drops vix_ltrp/spy_ltrp
// (fetch budget) — VIX level+ROC covers the vol regime.
describe_indicator('MFR_USO_CORE', 'lower', { decimals: 3 });
const RES = 'D';
const isNum = (x) => typeof x === 'number' && isFinite(x);
const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
const lastT = time[time.length - 1];
function land(ts, vs) {
    const landed = interpolate_sparse_series(land_points_onto_series(ts, vs, time), 'constant');
    const out = [];
    for (let i = 0; i < time.length; i++) out.push(isNum(landed[i]) ? landed[i] : NaN);
    return out;
}
async function feat(sym, alt) {
    try {
        let h;
        try { h = await request.history(sym, RES); }
        catch (e1) { if (!alt) throw e1; h = await request.history(alt, RES); }
        const ts = [], vs = [];
        for (let i = 0; i < h.time.length; i++) { if (h.time[i] <= lastT) { ts.push(h.time[i]); vs.push(h.close[i]); } }
        return land(ts, vs);
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + JSON.stringify(e && (e.message || e))); return series_of(NaN); }
}
const [LO, HI, BD, VLO, VHI, HYG] = await Promise.all([
    feat('#MFR_USO_LO'), feat('#MFR_USO_HI'), feat('#MFR_USO_BULLDIST'),
    feat('#MFR_VIX_LO'), feat('#MFR_VIX_HI'), feat('HYG'),
]);
const VIX = for_every(VLO, VHI, (a, b) => (isNum(a) && isNum(b) ? (a + b) / 2 : NaN)); // level proxy
const n = close.length;
const lag = (s, k) => { const o = []; for (let i = 0; i < n; i++) o.push(i >= k && isNum(s[i - k]) ? s[i - k] : NaN); return o; };
const pct = (a, b) => for_every(a, b, (x, y) => (isNum(x) && isNum(y) && y !== 0 ? x / y - 1 : NaN));
const diff = (a, b) => for_every(a, b, (x, y) => (isNum(x) && isNum(y) ? x - y : NaN));

// --- state ---
const rp = for_every(close, LO, HI, (c, lo, hi) => isNum(lo) && isNum(hi) && hi > lo ? clamp((c - lo) / (hi - lo), -0.5, 1.5) : NaN);
const rp_ma20 = sliding_window_function(rp, 20, (w) => { const v = w.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN; });
const rp_dev = diff(rp, rp_ma20);
const bulldist = for_every(BD, (v) => (isNum(v) ? clamp(v, -0.5, 0.5) : NaN));
const vix_level = for_every(VIX, (v) => (isNum(v) ? clamp(v, 5, 90) : NaN));
const vix_bucket = for_every(VIX, (v) => (isNum(v) ? (v < 20 ? 0 : v <= 30 ? 1 : 2) : NaN));
const rng_width = for_every(HI, LO, close, (hi, lo, c) => (isNum(hi) && isNum(lo) && c > 0 ? (hi - lo) / c : NaN));

// --- rate of change ---
const rp_d3 = diff(rp, lag(rp, 3));
const bulldist_d3 = diff(bulldist, lag(bulldist, 3));
const hi_d3 = for_every(pct(HI, lag(HI, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HH (+) vs LH (−)
const lo_d3 = for_every(pct(LO, lag(LO, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HL (+) vs LL (−)
const vix_roc5 = for_every(pct(VIX, lag(VIX, 5)), (v) => (isNum(v) ? clamp(v, -0.6, 1.5) : NaN));
const hyg_roc10 = for_every(pct(HYG, lag(HYG, 10)), (v) => (isNum(v) ? clamp(v, -0.15, 0.15) : NaN));

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
paint(vix_level, { name: 'vix_level', color: '#ef5350' });
paint(vix_bucket, { name: 'vix_bucket', color: '#e57373' });
paint(vix_roc5, { name: 'vix_roc5', color: '#ffab91' });
paint(hyg_roc10, { name: 'hyg_roc10', color: '#ffb300' });
paint(decel, { name: 'decel', color: '#ffe082' });
