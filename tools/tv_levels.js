// tools/tv_levels.js — injected into Kris's TradingView tab by the overnight
// task (docs/TV_LEVELS.md). Reads MFR Trend + Vola2vola daily values straight
// from the chart engine (works even while the tab isn't drawing).
// Needs both indicators on the chart: "Myfractalrange Trend" and
// "Vola2vola volatility Trade & Trend". Never saves the layout.
//
//   __tvStart(pairs)   pairs = [[botTicker, tvSymbol], ...] — runs in background
//   __tvPeek(from, n)  -> {done, total, have, rows: [...]}  (non-destructive, small chunks)
//   __tvStop()         -> ends the running job after the current symbol
//
// 9/26 lessons: (1) Chrome throttles timers in hidden tabs to ~1/min after 5 min,
// so every wait goes through a Web Worker timer; (2) collect in small,
// non-destructive chunks — tool output truncates long strings.
(() => {
  if (!window.__tvWorker) {
    const blob = new Blob(["onmessage = (e) => setTimeout(() => postMessage(e.data), e.data.ms);"],
                          {type: 'text/javascript'});
    window.__tvWorker = new Worker(URL.createObjectURL(blob));
    window.__tvWaiters = new Map();
    window.__tvWorker.onmessage = (e) => { const f = window.__tvWaiters.get(e.data.id); if (f) { window.__tvWaiters.delete(e.data.id); f(); } };
  }
  let seq = 0;
  const sleep = (ms) => new Promise(r => { const id = ++seq; window.__tvWaiters.set(id, r); window.__tvWorker.postMessage({id, ms}); });
  const CH = window.__tvCH || (window.__tvCH = window.TradingViewApi.activeChart());
  const inner = () => CH._chartWidget.model().model();
  const src = (name) => inner().dataSources().find(x => {
    try { return x.metaInfo().description === name; } catch (e) { return false; }
  });
  const rows = (s) => { const out = []; if (s) s.data().each((i, v) => { out.push(v); return false; }); return out; };
  const r6 = (x) => (x == null ? null : +(+x).toPrecision(7));
  // session date: futures/FX/bond sessions open the prior evening (22:00 UTC)
  const sessionDate = (t) => new Date((t + 6 * 3600) * 1000).toISOString().slice(0, 10);

  // 11/11 ranges-only pass: only MFR Range, with a long wait (k < waitK x 400 ms).
  // Rows carry ro:1 and go in with `--ingest-ranges`, which never touches TRADE/TREND.
  // A long-lived tab can leave MFR Range stuck in status 1 on every symbol (11/9-11/11);
  // a fresh page load cleared it, so run this pass in a freshly loaded chart tab.
  async function readRange(ticker, sym, waitK) {
    await Promise.race([new Promise(r => CH.setSymbol(sym, r)), sleep(12000)]);
    for (let k = 0; k < waitK; k++) {
      const bars = rows(inner().mainSeries());
      const R = src('MFR Range');
      if (!R) return {t: ticker, s: sym, miss: 'MFR Range not on chart'};
      const rg = rows(R);
      const lastT = bars.length ? bars[bars.length - 1][0] : null;
      if (bars.length > 0 && rg.length > 0 && rg[rg.length - 1][0] === lastT) {
        const type = (CH.symbolExt && CH.symbolExt().type) || '';
        let i = bars.length - 1;
        const roundTheClock = /futures|crypto|forex|cfd|spot|bond/i.test(type) || /!$/.test(sym);
        if (roundTheClock && bars[i][0] > Date.now() / 1000 - 20 * 3600) i -= 1;
        const b = bars[i], rr = rg.find(x => x[0] === b[0]);
        if (!rr) return {t: ticker, s: sym, miss: 'range bar missing'};
        return {t: ticker, s: CH.symbol(), type, bar: sessionDate(b[0]), close: r6(b[4]), ro: 1,
                rh: r6(rr[1]), rl: r6(rr[2]), lh: r6(rr[3]), ll: r6(rr[4])};
      }
      await sleep(400);
    }
    return {t: ticker, s: sym, miss: 'MFR Range never computed'};
  }

  async function readOne(ticker, sym) {
    await Promise.race([new Promise(r => CH.setSymbol(sym, r)), sleep(12000)]);
    // MFR Range (st/lt ranges, 9/29) computes slowly — up to ~25 s on a symbol
    // TradingView hasn't loaded before — so it gets a longer wait, but never
    // blocks the TRADE/TREND read: past the wait the row ships without ranges.
    for (let k = 0; k < 80; k++) {
      const bars = rows(inner().mainSeries());
      const m = rows(src('Myfractalrange Trend'));
      const v = rows(src('Vola2vola volatility Trade & Trend'));
      const R = src('MFR Range'), rg = rows(R);
      const lastT = bars.length ? bars[bars.length - 1][0] : null;
      // 11/11: MFR Range sat in status 1 (computing, 0 rows) on every symbol, so the
      // old 24 s wait (k >= 60) made a full run ~6.5 h and the overnight task never
      // finished. Ranges are the last-resort tier: wait ~3 s, then ship without them.
      const rangeReady = !R || (rg.length > 30 && rg[rg.length - 1][0] === lastT) ||
                         k >= 8 || (R.status && R.status().type === 2 && rg.length === 0);
      // 11/10: new listings (SKHY from 7/10/2026) have < 30 indicator rows — MFR Trend
      // needs ~63 bars of warmup. Accept a short series once the study reports done
      // (status type 2); Vola may still be null and ships as null.
      const done = (s) => { try { return s && s.status().type === 2; } catch (e) { return false; } };
      const okLen = (n, s) => n > 30 || (n > 0 && bars.length > 0 && bars.length < 160 && done(s));
      if (bars.length > 0 && okLen(m.length, src('Myfractalrange Trend')) &&
          okLen(v.length, src('Vola2vola volatility Trade & Trend')) && rangeReady &&
          m[m.length - 1][0] === lastT && v[v.length - 1][0] === lastT) {
        const type = (CH.symbolExt && CH.symbolExt().type) || '';
        const now = Date.now() / 1000;
        let i = bars.length - 1;
        const roundTheClock = /futures|crypto|forex|cfd|spot|bond/i.test(type) || /!$/.test(sym);
        if (roundTheClock && bars[i][0] > now - 20 * 3600) i -= 1;   // session still forming
        const b = bars[i];
        const mm = m.find(x => x[0] === b[0]), vv = v.find(x => x[0] === b[0]);
        const rr = rg.find(x => x[0] === b[0]) || [];
        if (!mm || !vv) return {t: ticker, s: sym, miss: 'indicator bar missing'};
        return {t: ticker, s: CH.symbol(), type, bar: sessionDate(b[0]), close: r6(b[4]),
                mu: r6(mm[1]), ml: r6(mm[2]), va: r6(vv[1]), vb: r6(vv[3]),
                rh: r6(rr[1]), rl: r6(rr[2]), lh: r6(rr[3]), ll: r6(rr[4])};
      }
      await sleep(400);
    }
    return {t: ticker, s: sym, miss: 'no study data (symbol unresolved or no access)'};
  }

  // opts: {rangesOnly: true, waitK: 75}  (75 x 400 ms = 30 s range wait per symbol)
  window.__tvStart = (pairs, opts) => {
    if (window.__tvJob && !window.__tvJob.done) return {error: 'a job is already running'};
    const o = opts || {};
    const job = window.__tvJob = {done: false, stop: false, total: pairs.length, rows: [], started: Date.now(),
                                  rangesOnly: !!o.rangesOnly};
    (async () => {
      if (!/^1?D$/.test(CH.resolution())) await new Promise(r => CH.setResolution('1D', r));
      for (const [t, s] of pairs) {
        if (job.stop) break;
        try { job.rows.push(o.rangesOnly ? await readRange(t, s, o.waitK || 75) : await readOne(t, s)); }
        catch (e) { job.rows.push({t, s, miss: String(e).slice(0, 80)}); }
      }
      job.done = true;
    })();
    return {started: pairs.length};
  };

  window.__tvPeek = (from, n) => {
    const job = window.__tvJob;
    if (!job) return {error: 'no job'};
    return {done: job.done, total: job.total, have: job.rows.length,
            elapsed_s: Math.round((Date.now() - job.started) / 1000),
            rows: job.rows.slice(from || 0, (from || 0) + (n || 25))};
  };

  window.__tvStop = () => { if (window.__tvJob) window.__tvJob.stop = true; return 'stopping'; };
  return 'tv_levels.js loaded';
})();
