// tools/sg_tap.js - keep EVERYTHING a SpotGamma page loads (Kris 11/24/2026: "I want to capture all the data in
// all the products ... I don't care how much data it is ... we need a robust data set that we can clean up").
//
// The whole dashboard runs on about 60 data feeds from api.spotgamma.com and api.stream.spotgamma.com
// (docs/playbook/SG_MAP.md). This script listens to every answer the page gets from those two servers and keeps
// each one whole, with the time and the address it came from. Nothing is picked out or thrown away, so a field
// nobody thought of today is still there to study later. Tables are built from the raw files afterwards
// (tools/sg_tap.py).
//
// Read-only: it only listens to what the page itself loads. It makes no requests of its own, changes no setting
// and sends nothing anywhere. It never keeps anything from /v1/me/ (account, sign-in refresh, saved filters) and
// never reads request headers.
//
// Use (capture desk, Kris's FIRST Chrome, a SpotGamma tab of your own, tab in front):
//   1. Open any dashboard.spotgamma.com page and paste this file into the javascript tool. It stores itself;
//      after a reload turn it back on with   (0, eval)(localStorage.getItem('__sgtap'))
//   2. Move around WITHOUT reloading so the listener stays on:
//        __sgTap.go('/hiro?tab=stock_screener')          one page
//        __sgTap.run([{go:'/home', wait:6000}, {tabs:true, wait:3000},
//                     {go:'/ivol?ivol_tab=fixed_strike_matrix&ivol_sym=SPX', wait:9000}, {tabs:true, wait:4000}])
//      {go} opens a page, {tab:'Name'} clicks one tab, {tabs:true} clicks every tab on the page in turn,
//      {scroll:true} scrolls every list to its end, {grab:['field','field'], name:'x'} keeps the biggest on-page
//      table whose rows have those fields (for tables the listener cannot hear being filled).
//      ALL 406 HIRO NAMES IN ONE GO (tested 11/24):
//        __sgTap.run([{go:'/hiro?tab=stock_screener', wait:9000}, {tab:'Stock Screener', wait:6000},
//                     {grab:['instrument','signal'], name:'hiro_screener'}])
//      Each row: HIRO signal now, 1-day low/current/high, 5-day and 30-day low/high, price, last close, sector.
//      Progress: JSON.stringify(__sgTap.status)     What has been kept: JSON.stringify(__sgTap.byPath)
//   3. Click the green "SG TAP SAVE" button (a real click): Downloads/sg_tap_<YYYYMMDD>_<HHMMSS>.jsonl.gz
//      One line per answer: {t, page, host, path, q, status, ctype, b64, body}. The buffer empties after a save.
//   4. py -m tools.sg_tap --ingest-downloads     files it under data/sg_tap/<date>/ and indexes it.
(function sgTap() {
  localStorage.setItem('__sgtap', '(' + sgTap.toString() + ')()');
  if (window.__sgTap && window.__sgTap.on) return 'already on: ' + window.__sgTap.n + ' answers kept';
  const HOSTS = /^(https|wss):\/\/(api|api\.stream|stream)\.spotgamma\.com\//;
  const SKIP = /\/v1\/me\/|\/manifest|login|logout|auth|token|refresh|password|session/i;   // account + sign-in: never kept
  const SECRET_Q = /^(token|key|auth|jwt|sig|signature|access_token|apikey|api_key)$/i;
  const T = window.__sgTap = { on: true, n: 0, bytes: 0, skipped: 0, errs: 0, recs: [], byPath: {}, started: new Date().toISOString(),
    status: { running: false, step: 0, steps: 0, at: null, done: false, err: [] } };

  const sleep = (() => {   // Web Worker timer: Chrome slows setTimeout in a hidden tab
    try {
      const w = new Worker(URL.createObjectURL(new Blob(['onmessage=e=>setTimeout(()=>postMessage(e.data.id),e.data.ms)'])));
      const cb = {}; let n = 0;
      w.onmessage = e => { const f = cb[e.data]; delete cb[e.data]; f && f(); };
      return ms => new Promise(r => { const id = ++n; cb[id] = r; w.postMessage({ id, ms }); });
    } catch (e) { return ms => new Promise(r => setTimeout(r, ms)); }
  })();
  const b64 = ab => { const u = new Uint8Array(ab); let s = ''; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
  const keep = (url, status, ctype, body, isB64) => {
    try {
      const u = new URL(url, location.href);
      const q = [...u.searchParams.entries()].filter(([k]) => !SECRET_Q.test(k)).map(([k, v]) => k + '=' + v).join('&');
      T.recs.push({ t: new Date().toISOString(), page: location.pathname + location.search, host: u.host, path: u.pathname, q,
        status, ctype: ctype || '', b64: !!isB64, body });
      T.n++; T.bytes += body.length; T.byPath[u.pathname] = (T.byPath[u.pathname] || 0) + 1;
      paint();
    } catch (e) { T.errs++; }
  };
  const wanted = url => { if (!HOSTS.test(url)) return false; if (SKIP.test(url)) { T.skipped++; return false; } return true; };

  // fetch
  const oFetch = window.fetch;
  window.fetch = function (input) {
    const p = oFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || String(input);
      if (wanted(url)) p.then(r => {
        try {
          const c = r.clone(), ct = c.headers.get('content-type') || '';
          if (/json|text|csv|xml/.test(ct)) c.text().then(b => keep(url, r.status, ct, b, false)).catch(() => { T.errs++; });
          else c.arrayBuffer().then(ab => keep(url, r.status, ct, b64(ab), true)).catch(() => { T.errs++; });
        } catch (e) { T.errs++; }
      }).catch(() => {});
    } catch (e) { T.errs++; }
    return p;
  };
  // XMLHttpRequest
  const oOpen = XMLHttpRequest.prototype.open, oSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, url) { try { this.__sgUrl = new URL(url, location.href).href; } catch (e) { this.__sgUrl = String(url); } return oOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    try {
      const url = this.__sgUrl;
      if (url && wanted(url)) this.addEventListener('load', () => {
        try {
          const ct = this.getResponseHeader('content-type') || '', rt = this.responseType;
          if (rt === '' || rt === 'text') keep(url, this.status, ct, this.responseText || '', false);
          else if (rt === 'json') keep(url, this.status, ct, JSON.stringify(this.response), false);
          else if (rt === 'arraybuffer' && this.response) keep(url, this.status, ct, b64(this.response), true);
          else if (rt === 'blob' && this.response) this.response.arrayBuffer().then(ab => keep(url, this.status, ct, b64(ab), true)).catch(() => { T.errs++; });
        } catch (e) { T.errs++; }
      });
    } catch (e) { T.errs++; }
    return oSend.apply(this, arguments);
  };
  // WebSocket (live streams in market hours): every message kept as it arrives
  try {
    const OW = window.WebSocket;
    window.WebSocket = new Proxy(OW, { construct(target, args) {
      const ws = new target(...args);
      try {
        const url = String(args[0]);
        if (wanted(url)) ws.addEventListener('message', ev => {
          try {
            const d = ev.data;
            if (typeof d === 'string') keep(url, 0, 'ws', d, false);
            else if (d instanceof ArrayBuffer) keep(url, 0, 'ws', b64(d), true);
            else if (d && d.arrayBuffer) d.arrayBuffer().then(ab => keep(url, 0, 'ws', b64(ab), true)).catch(() => { T.errs++; });
          } catch (e) { T.errs++; }
        });
      } catch (e) { T.errs++; }
      return ws;
    } });
  } catch (e) { T.errs++; }

  // moving around without a reload (a reload would switch the listener off)
  const txt = e => (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
  T.go = path => { history.pushState(null, '', path); window.dispatchEvent(new PopStateEvent('popstate')); return path; };
  T.tab = name => { const t = [...document.querySelectorAll('[role=tab]')].find(e => txt(e) === name); if (t) { t.click(); return true; } return false; };
  // Some tables are filled before the listener is on or through a channel it cannot hear (the HIRO Stock Screener's
  // 406 names). grab() takes the biggest on-page table whose rows carry all the given fields and keeps it whole.
  T.grab = (keys, name) => {
    const rootEl = document.getElementById('root') || document.body.firstElementChild;
    const rk = Object.keys(rootEl).find(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$'));
    let start = rootEl[rk]; if (start && start.stateNode && start.stateNode.current) start = start.stateNode.current;
    const stack = [start]; let n = 0, best = null;
    const test = s => { try { if (s instanceof Map) s = [...s.values()]; if (s && !Array.isArray(s) && Array.isArray(s.data)) s = s.data;
      if (Array.isArray(s) && s.length && s[0] && typeof s[0] === 'object' && keys.every(k => k in s[0]) && (!best || s.length > best.length)) best = s; } catch (e) { /* skip */ } };
    while (stack.length && n < 500000) {
      const f = stack.pop(); n++; if (!f) continue;
      if (typeof f.type !== 'string') { let h = f.memoizedState, i = 0; while (h && i < 80) { test(h.memoizedState); h = h.next; i++; }
        const p = f.memoizedProps; if (p) for (const k of ['data', 'rows', 'items', 'list']) test(p[k]); }
      if (f.child) stack.push(f.child); if (f.sibling) stack.push(f.sibling);
    }
    if (!best) return 0;
    keep('https://page.state/state/' + name, 200, 'application/json', JSON.stringify(best), false);
    return best.length;
  };
  T.run = async steps => {
    const st = T.status = { running: true, step: 0, steps: steps.length, at: null, done: false, err: [] };
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i]; st.step = i + 1; st.at = s.go || s.tab || (s.tabs ? 'every tab' : '');
      try {
        if (s.go) { if (s.leave !== false) { T.go('/home'); await sleep(1200); } T.go(s.go); await sleep(s.wait || 6000); }
        else if (s.tab) { if (!T.tab(s.tab)) st.err.push('no tab ' + s.tab); await sleep(s.wait || 3000); }
        else if (s.tabs) { const names = [...document.querySelectorAll('[role=tab]')].map(txt).filter(Boolean); for (const nm of names) { st.at = nm; T.tab(nm); await sleep(s.wait || 3000); } }
        else if (s.grab) { if (!T.grab(s.grab, s.name || s.grab.join('_'))) st.err.push('no table with ' + s.grab.join(',')); }
        else if (s.scroll) {   // walk every scrolling list on the page to its end, so lists that load as they scroll load fully
          const boxes = [...document.querySelectorAll('div')].filter(e => e.scrollHeight > e.clientHeight + 200 && e.clientHeight > 150 && /auto|scroll/.test(getComputedStyle(e).overflowY));
          for (const el of boxes) { for (let y = 0, k = 0; y < el.scrollHeight && k < 400; y += Math.max(200, el.clientHeight * 0.8), k++) { el.scrollTop = y; await sleep(s.wait || 700); } el.scrollTop = 0; }
        }
        else if (s.wait) await sleep(s.wait);
      } catch (e) { st.err.push(String(e && e.message)); }
    }
    st.running = false; st.done = true; st.at = null; paint();
  };

  // save: one gzipped file, one line per answer; the buffer empties after it
  const stamp = () => { const p = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).formatToParts(new Date()).reduce((a, x) => (a[x.type] = x.value, a), {}); return p.year + p.month + p.day + '_' + p.hour + p.minute + p.second; };
  T.save = async () => {
    const recs = T.recs; T.recs = []; const n = recs.length;
    const parts = recs.map(r => JSON.stringify(r) + '\n');
    let blob = new Blob(parts, { type: 'application/x-ndjson' }), ext = '.jsonl';
    try { blob = await new Response(blob.stream().pipeThrough(new CompressionStream('gzip'))).blob(); ext = '.jsonl.gz'; } catch (e) { /* no gzip: save plain */ }
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'sg_tap_' + stamp() + ext;
    document.body.appendChild(a); a.click(); a.remove();
    T.saved = (T.saved || 0) + n; T.bytes = 0; paint();
    return n + ' answers saved, ' + Math.round(blob.size / 1024) + ' KB';
  };
  function paint() {
    let b = document.getElementById('__sgTapSave');
    if (!b) {
      if (!document.body) return;
      b = document.createElement('button'); b.id = '__sgTapSave';
      b.style.cssText = 'position:fixed;top:6px;left:520px;z-index:99999;background:#1f7a3a;color:#fff;border:0;padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      b.onclick = () => { T.save(); };
      document.body.appendChild(b);
    }
    b.textContent = 'SG TAP SAVE (' + T.recs.length + ' answers, ' + (T.bytes / 1048576).toFixed(1) + ' MB)';
  }
  paint();
  return 'listening';
})()
