// tools/sg_daily_capture.js - the whole SpotGamma dashboard in one run (Kris 11/15/2026: "build the rest so we get all
// of it"). Walks the site inside one tab (no page reloads) and saves every page with tools/sg_page_capture.js:
//   Home            Squeeze Candidates + VRP scanner tabs (index levels, events calendar, QQQ record come along)
//   Indices         SPX, SPY, NDX, QQQ, RUT, IWM x Greeks, Volatility, Open Interest
//   Tape            Contract Data tab (the day's per-contract totals)
//   Equity Hub      Composite View for every ticker in window.__sgDailySyms (the book), History tab for the first 15
//
// Use (capture desk, Kris's FIRST Chrome, once a day after the close ~4:30 PM ET; keep the tab in front):
//   1. Open any dashboard.spotgamma.com page. If this browser has never run tools/sg_page_capture.js, paste that file
//      first (it stores itself in localStorage).
//   2. Set the book tickers:  window.__sgDailySyms = ['XLP', 'WM', ...]   (railway run py -m tools.sg_pages --book-syms)
//   3. Paste this file into the javascript tool. Progress:  JSON.stringify(window.__sgDaily.status)
//   4. When status.done, click the purple "SG DAILY SAVE" button: Downloads/sg_page_daily_<YYYYMMDD>_<HHMM>.json
//   5. railway run py -m tools.sg_pages --ingest-downloads
// Read-only: it only opens pages and tabs the plan already shows. ~6-9 s per page, ~10 min for 40 tickers.
(() => {
  const capSrc = localStorage.getItem('__sgcapfn');
  if (!capSrc) return 'paste tools/sg_page_capture.js once first (it stores itself in localStorage)';
  const cap = (0, eval)('(' + capSrc + ')');
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const go = path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); };
  const clickTab = name => {
    const t = [...document.querySelectorAll('[role=tab], button')].find(e => (e.innerText || '').trim() === name);
    if (t) { t.click(); return true; }
    return false;
  };
  const waitFor = async (test, ms) => { for (let i = 0; i < ms / 250; i++) { if (test()) return true; await sleep(250); } return false; };
  const syms = (window.__sgDailySyms || []).map(s => String(s).toUpperCase());
  const steps = [];
  steps.push({ path: '/home', tab: 'Squeeze Candidates' }, { path: '/home', tab: 'VRP' });
  for (const s of ['SPX', 'SPY', 'NDX', 'QQQ', 'RUT', 'IWM'])
    for (const t of ['Greeks', 'Volatility', 'Open Interest']) steps.push({ path: '/indices?sym=' + s, tab: t });
  steps.push({ path: '/tape', tab: 'Contract Data' });
  syms.forEach((s, i) => {
    steps.push({ path: '/equityhub?sym=' + encodeURIComponent(s), tab: 'Composite View' });
    if (i < 15) steps.push({ path: '/equityhub?sym=' + encodeURIComponent(s), tab: 'History' });
  });
  const st = { done: false, total: steps.length, got: 0, failed: [], current: null };
  window.__sgDaily = { status: st, captures: [] };

  (async () => {
    let lastPath = null;
    for (const step of steps) {
      st.current = step.path + ' / ' + step.tab;
      try {
        if (step.path !== lastPath) {
          go(step.path);
          lastPath = step.path;
          await sleep(2500);
          await waitFor(() => document.querySelectorAll('[role=tab]').length > 0, 12000);
          await sleep(2500 + Math.random() * 1500);
        }
        if (!clickTab(step.tab)) { st.failed.push(st.current + ' (no tab)'); continue; }
        await sleep(3500 + Math.random() * 1500);
        const out = cap('obj');
        out.step = step;
        window.__sgDaily.captures.push(out);
        st.got++;
      } catch (e) { st.failed.push(st.current + ' ' + e.message); }
    }
    st.done = true; st.current = null;
    const now = new Date();
    const et = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(now)
      .reduce((a, x) => (a[x.type] = x.value, a), {});
    const stamp = et.year + et.month + et.day + '_' + et.hour + et.minute;
    let btn = document.getElementById('__sgDailySave');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = '__sgDailySave';
      btn.style.cssText = 'position:fixed;top:6px;left:480px;z-index:99999;background:#7a3fc4;color:#fff;border:0;' +
        'padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      document.body.appendChild(btn);
    }
    btn.textContent = 'SG DAILY SAVE (' + st.got + '/' + st.total + ')';
    btn.onclick = () => {
      const body = JSON.stringify({ captured_at: now.toISOString(), stamp, captures: window.__sgDaily.captures });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      a.download = 'sg_page_daily_' + stamp + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = 'SAVED ' + st.got + ' pages';
    };
  })();
  return 'started: ' + steps.length + ' steps';
})()
