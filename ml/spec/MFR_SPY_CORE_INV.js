// MFR_SPY_CORE_INV — same 7 features as MFR_SPY_CORE, but computed from SPY's own
// close/volume (fetched) so the script can run on an INVERSE chart (SH, D).
// Purpose: TrendSpider ML is long-only ("TP before SL"); a long on SH = a short on SPY.
// 6 fetches total (SPY + 5 custom symbols) = the strategy-runtime limit.
describe_indicator('MFR_SPY_CORE_INV', 'lower', { decimals: 3 });
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
async function feat(sym) {
    try {
        const h = await request.history(sym, RES);
        const ts = [], vs = [];
        for (let i = 0; i < h.time.length; i++) { if (h.time[i] <= lastT) { ts.push(h.time[i]); vs.push(h.close[i]); } }
        return land(ts, vs);
    } catch (e) { console.log('MISSING: ' + sym + ' -> ' + e); return series_of(NaN); }
}
async function spyBars() {
    try {
        const h = await request.history('SPY', RES);
        const ts = [], cs = [], vs = [];
        for (let i = 0; i < h.time.length; i++) { if (h.time[i] <= lastT) { ts.push(h.time[i]); cs.push(h.close[i]); vs.push(h.volume[i]); } }
        return { c: land(ts, cs), v: land(ts, vs) };
    } catch (e) { console.log('MISSING: SPY -> ' + e); return { c: series_of(NaN), v: series_of(NaN) }; }
}
const [SPY, LO, HI, BD, LTRP, VIXLT] = await Promise.all([
    spyBars(),
    feat('#MFR_SPY_LO'), feat('#MFR_SPY_HI'), feat('#MFR_SPY_BULLDIST'),
    feat('#MFR_SPY_LTRP'), feat('#MFR_VIX_LTRP'),
]);
const C = SPY.c, V = SPY.v;
const rp = for_every(C, LO, HI, (c, lo, hi) => isNum(c) && isNum(lo) && isNum(hi) && hi > lo ? clamp((c - lo) / (hi - lo), -0.5, 1.5) : NaN);
const rp_ma20 = sliding_window_function(rp, 20, (w) => { const v = w.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN; });
const rp_dev = for_every(rp, rp_ma20, (a, b) => (isNum(a) && isNum(b) ? a - b : NaN));
const bulldist = for_every(BD, (v) => (isNum(v) ? clamp(v, -0.3, 0.3) : NaN));
// decel — port of volume_signal.py on SPY close/volume (not the chart's)
const n = C.length;
const decel = [];
for (let i = 0; i < n; i++) decel.push(NaN);
for (let t = 12; t < n; t++) {
    if (!isNum(C[t]) || !isNum(V[t])) continue;
    let s = 0, k = t;
    while (k >= 1 && isNum(C[k - 1]) && isNum(V[k - 1]) && C[k] < C[k - 1] && V[k] < V[k - 1]) { s++; k--; }
    const vols = [];
    for (let j = t; j > t - 12 && j >= 1 && vols.length < 3; j--) {
        if (isNum(C[j]) && isNum(C[j - 1]) && C[j] < C[j - 1]) vols.push(V[j]);
    }
    let dec = null;
    if (vols.length >= 2) {
        vols.reverse();
        const m = vols.length, xm = (m - 1) / 2;
        const ym = vols.reduce((a, b) => a + b, 0) / m;
        let num = 0, den = 0;
        for (let i = 0; i < m; i++) { num += (i - xm) * (vols[i] - ym); den += (i - xm) * (i - xm); }
        dec = ym > 0 ? (num / den) / ym < 0 : null;
    }
    const down3 = t >= 3 && isNum(C[t - 3]) ? C[t] / C[t - 3] - 1 < 0 : false;
    decel[t] = ((down3 && dec === false) ? -1 : Math.min(s, 7)) / 7;
}
paint(rp, { name: 'rp', color: '#4caf50' });
paint(rp_dev, { name: 'rp_dev20', color: '#81c784' });
paint(bulldist, { name: 'bulldist', color: '#ab47bc' });
paint(LTRP, { name: 'spy_ltrp', color: '#29b6f6' });
paint(VIXLT, { name: 'vix_ltrp', color: '#ef5350' });
paint(decel, { name: 'decel', color: '#ffb300' });
