// MFR_USO_CORE_v2 — oil with its OWN macro driver: the dollar.
// 6 fetches: #MFR_USO_LO, #MFR_USO_HI, #MFR_USO_BULLDIST, #MFR_UUP_RP, UUP, SPY (prices → 30d return corrs computed on-chart; #CORR_* exports are no_data).
// (VIX/HYG dropped — stock-market variables; for oil the slots go to the dollar.)
// Key feature: usd_pressure = −corr(USD,oil,30d) × (uup_rp − 0.5)
//   > 0 : dollar extended HIGH in its range AND inversely correlated → bullish oil (Kris's setup)
//   < 0 : dollar at range LOW and inversely correlated → bearish oil;  ≈0 when correlation is weak.
describe_indicator('MFR_USO_CORE_v2', 'lower', { decimals: 3 });
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
const [LO, HI, BD, UUPRP, UUPPX, SPYPX] = await Promise.all([
    feat('#MFR_USO_LO'), feat('#MFR_USO_HI'), feat('#MFR_USO_BULLDIST'),
    feat('#MFR_UUP_RP'), feat('UUP'), feat('SPY'),
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
const uup_rp = for_every(UUPRP, (v) => (isNum(v) ? clamp(v, -0.5, 1.5) : NaN));
// 30d Pearson corr of daily returns USO(chart) vs X — trailing window only, no lookahead
const ret = (px) => { const o = []; for (let i = 0; i < n; i++) o.push(i > 0 && isNum(px[i]) && isNum(px[i - 1]) && px[i - 1] > 0 ? px[i] / px[i - 1] - 1 : NaN); return o; };
const corr30 = (a, b) => {
    const o = [];
    for (let i = 0; i < n; i++) {
        if (i < 30) { o.push(NaN); continue; }
        let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, m = 0;
        for (let j = i - 29; j <= i; j++) { const x = a[j], y = b[j]; if (!isNum(x) || !isNum(y)) continue; sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; m++; }
        if (m < 20) { o.push(NaN); continue; }
        const cov = sxy / m - (sx / m) * (sy / m), vx = sxx / m - (sx / m) ** 2, vy = syy / m - (sy / m) ** 2;
        o.push(vx > 0 && vy > 0 ? clamp(cov / Math.sqrt(vx * vy), -1, 1) : NaN);
    }
    return o;
};
const rU = ret(close), rD = ret(UUPPX), rS = ret(SPYPX);
const usd_oil_corr = corr30(rU, rD);
const spy_oil_corr = corr30(rU, rS);
const spy_roc10 = for_every(pct(SPYPX, lag(SPYPX, 10)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));
const usd_pressure = for_every(usd_oil_corr, uup_rp, (c, r) => (isNum(c) && isNum(r) ? -c * (r - 0.5) : NaN));
const rng_width = for_every(HI, LO, close, (hi, lo, c) => (isNum(hi) && isNum(lo) && c > 0 ? (hi - lo) / c : NaN));

// --- rate of change ---
const rp_d3 = diff(rp, lag(rp, 3));
const bulldist_d3 = diff(bulldist, lag(bulldist, 3));
const hi_d3 = for_every(pct(HI, lag(HI, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HH (+) vs LH (−)
const lo_d3 = for_every(pct(LO, lag(LO, 3)), (v) => (isNum(v) ? clamp(v, -0.2, 0.2) : NaN));   // HL (+) vs LL (−)
const uup_rp_d3 = diff(uup_rp, lag(uup_rp, 3));
const usd_pressure_d3 = diff(usd_pressure, lag(usd_pressure, 3));

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
paint(uup_rp, { name: 'uup_rp', color: '#29b6f6' });
paint(usd_oil_corr, { name: 'usd_oil_corr30', color: '#ef5350' });
paint(spy_oil_corr, { name: 'spy_oil_corr30', color: '#e57373' });
paint(spy_roc10, { name: 'spy_roc10', color: '#ffab91' });
paint(usd_pressure, { name: 'usd_pressure', color: '#ffb300' });
paint(uup_rp_d3, { name: 'uup_rp_d3', color: '#90caf9' });
paint(usd_pressure_d3, { name: 'usd_pressure_d3', color: '#ffe082' });
paint(decel, { name: 'decel', color: '#ffe082' });
