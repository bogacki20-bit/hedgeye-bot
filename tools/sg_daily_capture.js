// tools/sg_daily_capture.js - the whole SpotGamma dashboard in one run (Kris 11/15/2026: "build the rest so we get all
// of it"). Walks the site inside one tab and saves every page with tools/sg_page_capture.js:
//   Home            Squeeze Candidates + VRP scanner tabs (index levels, events calendar, QQQ record come along)
//   Indices         SPX, SPY, NDX, QQQ, RUT, IWM x Greeks, Volatility, Open Interest
//   Tape            Contract Data tab (the day's per-contract totals)
//   Equity Hub      Composite View for every ticker in window.__sgDailySyms (the book), History tab for the first 15
//
// BATCHES (11/19): Equity Hub pages freeze the tab after ~6-15 pages. The run now goes in batches of
// window.__sgDailyBatch pages (default 8). Each page is stored in this browser's IndexedDB the moment it is captured
// and progress in localStorage (__sgDailyProg), so after every batch the script reloads the tab itself and a frozen
// tab loses nothing. After a reload (or a freeze + manual reload) resume with ONE line in the javascript tool:
//     (0, eval)(localStorage.getItem('__sgdaily'))
//
// Use (capture desk, Kris's FIRST Chrome, once a day after the close ~4:30 PM ET; keep the tab in front):
//   1. Open any dashboard.spotgamma.com page. If this browser has never run tools/sg_page_capture.js, paste that file
//      first (it stores itself in localStorage).
//   2. Set the book tickers:  window.__sgDailySyms = ['XLP', 'WM', ...]   (railway run py -m tools.sg_pages --book-syms)
//      Optional: window.__sgDailyBatch = 8 (pages per tab load; remembered).
//   3. Paste this file into the javascript tool. It stores itself, so later you only run the one-line resume above.
//   4. After each batch the tab reloads: run the resume line again. Progress (survives reloads):
//        localStorage.getItem('__sgDailyProg')          (or JSON.stringify(window.__sgDaily.status) before a reload)
//   5. When done, click the purple "SG DAILY SAVE" button (run the resume line first if the tab has reloaded):
//      Downloads/sg_page_daily_<YYYYMMDD>_<HHMM>.json with every page of the run, as before.
//   6. railway run py -m tools.sg_pages --ingest-downloads
//   Same day, pasting again resumes; a new ET day starts a new run. To start over today: window.__sgDailyRestart = true.
// Read-only: it only opens pages and tabs the plan already shows. ~6-9 s per page.
(function sgDaily() {
  localStorage.setItem('__sgdaily', '(' + sgDaily.toString() + ')()');
  const capSrc = localStorage.getItem('__sgcapfn');
  if (!capSrc) return 'paste tools/sg_page_capture.js once first (it stores itself in localStorage)';
  const cap = (0, eval)('(' + capSrc + ')');
  // 11/19 (capture desk, tested live): Chrome throttles setTimeout in a hidden tab to ~1/min after 5 min, so the
  // run crawled at ~2.7 min/page. Waits go through a Web Worker timer (same trick as tools/tv_levels.js); ~15 s/page.
  // Falls back to setTimeout if the page blocks blob workers.
  const sleep = (() => {
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
  const lastUpx = out => {   // price on the newest point of the first state that carries one (tilt / level history)
    for (const v of Object.values(out.states || {}))
      if (Array.isArray(v) && v.length && v[v.length - 1] && typeof v[v.length - 1].upx === 'number') return v[v.length - 1].upx;
    return null;
  };
  const clickTab = name => {
    const t = [...document.querySelectorAll('[role=tab], button')].find(e => (e.innerText || '').trim() === name);
    if (t) { t.click(); return true; }
    return false;
  };
  const waitFor = async (test, ms) => { for (let i = 0; i < ms / 250; i++) { if (test()) return true; await sleep(250); } return false; };
  // 11/20: the Indices page reads ?sym= only when it mounts, so a pushState from /indices?sym=SPY to ?sym=QQQ kept
  // SPY's data (the URL, and so the saved label, said QQQ). Leaving through /home makes it mount again for each symbol.
  let mounted = location.pathname + location.search;
  const open = async (path, force) => {
    if (force || (path.split('?')[0] === mounted.split('?')[0] && path !== mounted)) {
      go('/home');
      await sleep(1500);
      await waitFor(() => location.pathname === '/home' && document.querySelectorAll('[role=tab]').length > 0, 8000);
    }
    go(path);
    mounted = path;
    await sleep(2500);
    await waitFor(() => document.querySelectorAll('[role=tab]').length > 0, 12000);
    await sleep(2500 + Math.random() * 1500);
  };
  const etParts = d => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d)
    .reduce((a, x) => (a[x.type] = x.value, a), {});
  const et = etParts(new Date());
  const day = et.year + '-' + et.month + '-' + et.day;

  // IndexedDB: one record per captured page (localStorage is too small for ~125 pages)
  const idb = () => new Promise((res, rej) => {
    const r = indexedDB.open('sgDaily', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('caps', { autoIncrement: true });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  const idbTx = async (mode, fn) => {
    const db = await idb();
    return new Promise((res, rej) => {
      const tx = db.transaction('caps', mode);
      const out = fn(tx.objectStore('caps'));
      tx.oncomplete = () => { db.close(); res(out && 'result' in out ? out.result : undefined); };
      tx.onerror = () => { db.close(); rej(tx.error); };
    });
  };
  const idbAdd = v => idbTx('readwrite', s => { s.add(v); });
  const idbAll = () => idbTx('readonly', s => s.getAll());
  const idbClear = () => idbTx('readwrite', s => { s.clear(); });

  if (window.__sgDailyBatch) localStorage.setItem('__sgDailyBatch', String(window.__sgDailyBatch));
  const BATCH = Math.max(1, Number(localStorage.getItem('__sgDailyBatch') || 8));
  let prog = null;
  try { prog = JSON.parse(localStorage.getItem('__sgDailyProg') || 'null'); } catch (e) { prog = null; }
  const saveProg = () => localStorage.setItem('__sgDailyProg', JSON.stringify(prog));
  const fresh = window.__sgDailyRestart || !prog || prog.day !== day;

  let syms = window.__sgDailySyms;
  if (!syms) { try { syms = JSON.parse(localStorage.getItem('__sgDailySyms') || 'null'); } catch (e) { syms = null; } }
  if (fresh) {
    syms = (syms || []).map(s => String(s).toUpperCase());
    if (!syms.length) return 'set window.__sgDailySyms first (railway run py -m tools.sg_pages --book-syms)';
    localStorage.setItem('__sgDailySyms', JSON.stringify(syms));
    const steps = [];
    steps.push({ path: '/home', tab: 'Squeeze Candidates' }, { path: '/home', tab: 'VRP' });
    for (const s of ['SPX', 'SPY', 'NDX', 'QQQ', 'RUT', 'IWM'])
      for (const t of ['Greeks', 'Volatility', 'Open Interest']) steps.push({ path: '/indices?sym=' + s, tab: t });
    steps.push({ path: '/tape', tab: 'Contract Data' });
    syms.forEach((s, i) => {
      steps.push({ path: '/equityhub?sym=' + encodeURIComponent(s), tab: 'Composite View' });
      if (i < 15) steps.push({ path: '/equityhub?sym=' + encodeURIComponent(s), tab: 'History' });
    });
    prog = { day, started: new Date().toISOString(), steps, next: 0, got: 0, failed: [], loads: 0, done: false, px: {} };
    window.__sgDailyRestart = false;
  }
  prog.loads = (prog.loads || 0) + 1;
  prog.px = prog.px || {};
  saveProg();
  const st = { done: prog.done, total: prog.steps.length, got: prog.got, next: prog.next, failed: prog.failed,
               batch: BATCH, loads: prog.loads, current: null, phase: 'running' };
  window.__sgDaily = { status: st };

  const showSave = async () => {
    const stamp = et.year + et.month + et.day + '_' + et.hour + et.minute;
    let btn = document.getElementById('__sgDailySave');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = '__sgDailySave';
      btn.style.cssText = 'position:fixed;top:6px;left:480px;z-index:99999;background:#7a3fc4;color:#fff;border:0;' +
        'padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      document.body.appendChild(btn);
    }
    const caps = await idbAll();
    btn.textContent = 'SG DAILY SAVE (' + caps.length + '/' + prog.steps.length + ')';
    btn.onclick = () => {
      const body = JSON.stringify({ captured_at: prog.started, stamp, captures: caps });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      a.download = 'sg_page_daily_' + stamp + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = 'SAVED ' + caps.length + ' pages';
    };
    st.phase = 'done: click SG DAILY SAVE';
  };

  (async () => {
    if (fresh) await idbClear();
    if (prog.done) { await showSave(); return; }
    let lastPath = null, inLoad = 0;
    while (prog.next < prog.steps.length && inLoad < BATCH) {
      const step = prog.steps[prog.next];
      st.current = step.path + ' / ' + step.tab;
      try {
        if (step.path !== lastPath) {
          await open(step.path);
          lastPath = step.path;
        }
        if (!clickTab(step.tab)) {
          prog.failed.push(st.current + ' (no tab)');
        } else {
          await sleep(3500 + Math.random() * 1500);
          let out = cap('obj');
          // 11/20: same-page symbol switches kept the old symbol's data. If this Indices page still shows the price
          // another symbol showed, re-open it through Home once; if it still matches, skip it (failed list).
          const sym = (step.path.match(/[?&]sym=([^&]+)/) || [])[1];
          if (step.path.startsWith('/indices') && sym) {
            let px = lastUpx(out);
            const other = px != null && Object.entries(prog.px).find(([s, v]) => s !== sym && v === px);
            if (other) {
              await open(step.path, true);
              clickTab(step.tab);
              await sleep(4500);
              out = cap('obj');
              px = lastUpx(out);
            }
            if (px != null && Object.entries(prog.px).some(([s, v]) => s !== sym && v === px)) {
              prog.failed.push(st.current + ' (still shows ' + Object.entries(prog.px).find(([s, v]) => s !== sym && v === px)[0] + ' data)');
              out = null;
            } else if (px != null && !prog.px[sym]) prog.px[sym] = px;
          }
          if (out) {
            out.step = step;
            await idbAdd(JSON.parse(JSON.stringify(out)));   // plain data, as the old JSON save wrote it
            prog.got++;
          }
        }
      } catch (e) { prog.failed.push(st.current + ' ' + e.message); }
      prog.next++;
      inLoad++;
      saveProg();
      Object.assign(st, { got: prog.got, next: prog.next });
    }
    st.current = null;
    if (prog.next >= prog.steps.length) {
      prog.done = true; saveProg(); st.done = true;
      await showSave();
      return;
    }
    // batch finished: fresh page load clears the Equity Hub memory build-up; resume with the one-liner
    st.phase = 'reloading: run (0, eval)(localStorage.getItem(\'__sgdaily\')) after the page loads';
    await sleep(800);
    location.reload();
  })();
  return (fresh ? 'started' : 'resumed') + ': step ' + Math.min(prog.next + 1, prog.steps.length) + '/' + prog.steps.length +
    ', batch ' + BATCH + (prog.done ? ' (already done: showing SAVE)' : '');
})()
