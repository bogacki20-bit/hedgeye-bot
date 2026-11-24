// tools/sg_vol_capture.js - SpotGamma Volatility Dashboard, once a day, for the indexes and every book name
// (Kris 11/24/2026: "it covers everything that trades options ... log it so we have history").
//
// For each symbol it opens /ivol, reads the Term Structure tab (implied vol by expiry, with SpotGamma's normal range
// for each expiry) and the Volatility Skew tab (implied vol by strike for the ~30-day expiry, with its 10th/25th/
// median/75th/90th percentile bands), then once the VIX Term Structure tab (VIX futures curve, today and yesterday).
// Only those small tables are kept; the full option surface the page holds is NOT saved (too big).
//
// Use (capture desk, Kris's FIRST Chrome, once a day after the close with the other SpotGamma pages; tab in front):
//   1. Open any dashboard.spotgamma.com page in a SpotGamma tab of your own.
//   2. Set the symbols:  window.__sgVolSyms = ['SPX','QQQ','IWM', ...book names]
//        (railway run py -m tools.sg_vol --syms   prints that line: the indexes, then the book and the stalk list)
//   3. Paste this file into the javascript tool. It stores itself; if the tab reloads or freezes, resume with
//        (0, eval)(localStorage.getItem('__sgvol'))
//      Progress: JSON.stringify(window.__sgVol.status)     (~15 s per symbol)
//   4. When it says done, click the orange "SG VOL SAVE" button: Downloads/sg_vol_<YYYYMMDD>_<HHMM>.json
//   5. railway run py -m tools.sg_vol --ingest-downloads
//   Same ET day, running it again resumes where it stopped. To start over today: window.__sgVolRestart = true.
// Read-only: it opens pages and tabs the plan already shows, changes no setting, and sends nothing anywhere.
(function sgVol() {
  localStorage.setItem('__sgvol', '(' + sgVol.toString() + ')()');
  const sleep = (() => {   // Web Worker timer: Chrome throttles setTimeout in a hidden tab (same trick as sg_daily_capture.js)
    try {
      const wk = window.__sgWk || (window.__sgWk = (() => {
        const w = new Worker(URL.createObjectURL(new Blob(['onmessage=e=>setTimeout(()=>postMessage(e.data.id),e.data.ms)'])));
        const cb = {}; let n = 0;
        w.onmessage = e => { const f = cb[e.data]; delete cb[e.data]; f && f(); };
        return ms => new Promise(r => { const id = ++n; cb[id] = r; w.postMessage({ id, ms }); });
      })());
      return ms => wk(ms);
    } catch (e) { return ms => new Promise(r => setTimeout(r, ms)); }
  })();
  const go = path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); };
  const clickTab = name => {
    const t = [...document.querySelectorAll('[role=tab]')].find(e => (e.innerText || '').trim() === name);
    if (t) { t.click(); return true; }
    return false;
  };
  const waitFor = async (test, ms) => { for (let i = 0; i < ms / 300; i++) { try { if (test()) return true; } catch (e) { /* keep waiting */ } await sleep(300); } return false; };
  const r5 = v => (typeof v === 'number' && isFinite(v)) ? Math.round(v * 1e5) / 1e5 : null;

  // every array-of-records React state on the page (found by shape; component names change with each SpotGamma build)
  const states = () => {
    const rootEl = document.getElementById('root') || document.body.firstElementChild;
    const rk = Object.keys(rootEl).find(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$'));
    let start = rootEl[rk];
    if (start && start.stateNode && start.stateNode.current) start = start.stateNode.current;
    const out = [], stack = [start];
    let n = 0;
    while (stack.length && n < 400000) {
      const f = stack.pop(); n++;
      if (!f) continue;
      if (typeof f.type !== 'string') {
        let h = f.memoizedState, i = 0;
        while (h && i < 80) {
          const s = h.memoizedState;
          if (Array.isArray(s) && s.length && s[0] && typeof s[0] === 'object') out.push(s);
          else if (s && typeof s === 'object' && Array.isArray(s.data) && s.data.length && typeof s.data[0] === 'object') out.push(s.data);
          h = h.next; i++;
        }
      }
      if (f.child) stack.push(f.child);
      if (f.sibling) stack.push(f.sibling);
    }
    return out;
  };
  const readTerm = () => {   // rows: {daysToExpiry, expirationDate, '<tradeDate>': iv, '<tradeDate>/FIV': fwd iv, 'bounds-<tradeDate>': [lo, hi]}
    const a = states().find(s => 'daysToExpiry' in s[0] && Object.keys(s[0]).some(k => k.startsWith('bounds-')));
    if (!a) return null;
    const bk = Object.keys(a[0]).find(k => k.startsWith('bounds-'));
    const td = bk.slice(7);
    return { trade_date: td, rows: a.map(r => {
      const b = r['bounds-' + td] || [];
      return [r5(r.daysToExpiry), r.expirationDate, r5(r[td]), r5(r[td + '/FIV']), r5(b[0]), r5(b[1])];
    }) };
  };
  const readSkew = () => {   // header {sym, tradeDate, expiryDate, currentPrice} + rows by strike with percentile bands
    const all = states();
    const hd = all.find(s => 'greeksMap' in s[0] && 'sym' in s[0]);
    const a = all.find(s => 'strike' in s[0] && 'moneyness' in s[0]);
    if (!hd || !a) return null;
    const h = hd[0], key = h.key || (h.tradeDate + '-' + h.expiryDate);
    return { sym: h.sym, trade_date: h.tradeDate, expiry: Number(h.expiryDate), px: r5(h.currentPrice), rows: a.map(r =>
      [r5(r.strike), r5(r.moneyness), r5(r[key + '-delta']), r5(r[key]), r5(r['p10_iv-' + key]), r5(r['p25_iv-' + key]),
        r5(r['median_iv-' + key]), r5(r['p75_iv-' + key]), r5(r['p90_iv-' + key]), r5(r['min_iv-' + key]), r5(r['max_iv-' + key])]) };
  };
  const readVix = () => {   // VIX futures: {sym:'/VXZ26:XCBF', close, expiration_date, quoteDate, time}
    const a = states().find(s => s.some(r => r && typeof r.sym === 'string' && r.sym.startsWith('/VX') && 'close' in r));
    if (!a) return null;
    return a.filter(r => r && r.sym).map(r => [r.sym, r.expiration_date, r.quoteDate, r5(r.close), r.time]);
  };

  const et = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date())
    .reduce((a, x) => (a[x.type] = x.value, a), {});
  const day = et.year + '-' + et.month + '-' + et.day;
  let prog = null;
  try { prog = JSON.parse(localStorage.getItem('__sgVolProg') || 'null'); } catch (e) { prog = null; }
  const fresh = window.__sgVolRestart || !prog || prog.day !== day;
  if (fresh) {
    let syms = window.__sgVolSyms;
    if (!syms) { try { syms = JSON.parse(localStorage.getItem('__sgVolSyms') || 'null'); } catch (e) { syms = null; } }
    syms = [...new Set((syms || []).map(s => String(s).toUpperCase()))];
    if (!syms.length) return 'set window.__sgVolSyms first (railway run py -m tools.sg_vol --syms)';
    localStorage.setItem('__sgVolSyms', JSON.stringify(syms));
    prog = { day, started: new Date().toISOString(), syms, next: 0, failed: [], done: false, data: {}, vix: null };
    window.__sgVolRestart = false;
  }
  const save = () => { try { localStorage.setItem('__sgVolProg', JSON.stringify(prog)); } catch (e) { /* storage full: keep going in memory */ } };
  save();
  const st = { done: prog.done, total: prog.syms.length, next: prog.next, got: Object.keys(prog.data).length,
               failed: prog.failed, current: null, phase: 'running' };
  window.__sgVol = { status: st };

  const showSave = () => {
    let btn = document.getElementById('__sgVolSave');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = '__sgVolSave';
      btn.style.cssText = 'position:fixed;top:6px;left:760px;z-index:99999;background:#c4691f;color:#fff;border:0;' +
        'padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      document.body.appendChild(btn);
    }
    const n = Object.keys(prog.data).length;
    btn.textContent = 'SG VOL SAVE (' + n + '/' + prog.syms.length + ')';
    btn.onclick = () => {
      const body = JSON.stringify({ kind: 'sg_vol', day: prog.day, captured_at: new Date().toISOString(),
        started: prog.started, failed: prog.failed, vix: prog.vix, syms: prog.data });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      a.download = 'sg_vol_' + et.year + et.month + et.day + '_' + et.hour + et.minute + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = 'SAVED ' + n + ' symbols';
    };
    st.phase = 'done: click SG VOL SAVE';
  };

  (async () => {
    if (prog.done) { showSave(); return; }
    let first = true;
    while (prog.next < prog.syms.length) {
      const sym = prog.syms[prog.next];
      st.current = sym;
      try {
        // leave the page between symbols so it mounts again with the new one (the Indices page kept old data otherwise)
        if (!first || location.pathname === '/ivol') { go('/home'); await sleep(1500); }
        first = false;
        go('/ivol?ivol_tab=term_structure&ivol_sym=' + encodeURIComponent(sym));
        await sleep(2500);
        await waitFor(() => document.querySelectorAll('[role=tab]').length >= 3, 12000);
        clickTab('Term Structure');
        await waitFor(() => readTerm(), 12000);
        await sleep(1200 + Math.random() * 800);
        const term = readTerm();
        clickTab('Volatility Skew');
        await waitFor(() => { const s = readSkew(); return s && s.sym === sym; }, 14000);
        await sleep(1200 + Math.random() * 800);
        const skew = readSkew();
        if (!term && !skew) prog.failed.push(sym + ' (no data)');
        else if (skew && skew.sym !== sym) prog.failed.push(sym + ' (page still shows ' + skew.sym + ')');
        else prog.data[sym] = { term, skew };
      } catch (e) { prog.failed.push(sym + ' ' + (e && e.message)); }
      prog.next++;
      save();
      Object.assign(st, { next: prog.next, got: Object.keys(prog.data).length });
    }
    try {
      st.current = 'VIX';
      clickTab('VIX Term Structure');
      await waitFor(() => readVix(), 12000);
      await sleep(1000);
      prog.vix = readVix();
      if (!prog.vix) prog.failed.push('VIX term structure (no data)');
    } catch (e) { prog.failed.push('VIX ' + (e && e.message)); }
    st.current = null;
    prog.done = true; save(); st.done = true;
    showSave();
  })();
  return (fresh ? 'started' : 'resumed') + ': symbol ' + Math.min(prog.next + 1, prog.syms.length) + '/' + prog.syms.length +
    (prog.done ? ' (already done: showing SAVE)' : '');
})()
