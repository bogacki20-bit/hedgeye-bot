// tools/sg_page_capture.js - save everything a SpotGamma dashboard page holds (Kris 11/15/2026: "build the rest so
// we get all of it"). Works on any dashboard.spotgamma.com page: Home, Indices (each symbol and tab), Reports,
// Equity Hub (a ticker), Tape (Contract Data tab), Founder's Notes.
//
// Use (capture desk, Kris's FIRST Chrome):
//   1. Open the page (and the symbol / tab you want), wait ~8 s.
//   2. First time in a session: paste this whole file into the javascript tool. It also stores itself in the
//      page's localStorage, so afterwards (same browser, any SpotGamma page) just run:
//          (0, eval)(localStorage.getItem('__sgcap'))
//   3. Click the blue "SG PAGE SAVE" button it adds. That downloads
//      Downloads/sg_page_<page>[_<sym>][_<tab>]_<YYYYMMDD>_<HHMM>.json
//   4. railway run py -m tools.sg_pages --ingest-downloads
//
// What it saves (read-only; nothing is sent anywhere):
//   states - every data-like React state on the page (arrays of records, objects holding numbers), found by shape,
//            keyed by component + hook, deduped; chart datasets (props.data); theme/layout/config skipped.
//   grids  - rows of every data grid on the page.
//   text   - the page's visible text (reports, level tables, calendars).
// The javascript tool returns only ~1,000 characters, so the data leaves through the download.
(() => {
  const __f = (mode) => {   // mode 'obj': return the capture instead of adding the save button (daily runner)
    const MAX_ITEM = 400000, MAX_TOTAL = 12000000;
    const SKIP_KEYS = /^(palette|breakpoints|typography|transitions|shadows|zIndex|mixins|spacing|shape|components|width|minWidth|maxWidth|hideable|renderCell|valueFormatter|sortComparator|filterOperators|ref|current|_owner|stateNode|queue|baseQueue|deps|create|destroy)$/;
    const prim = v => v === null || ['string', 'number', 'boolean', 'bigint'].includes(typeof v);
    const plain = v => v && typeof v === 'object' && !Array.isArray(v) &&
      (Object.getPrototypeOf(v) === Object.prototype || Object.getPrototypeOf(v) === null);
    const ser = v => {
      const seen = new WeakSet();
      try {
        return JSON.stringify(v, (k, x) => {
          if (typeof x === 'bigint') return x.toString();
          if (typeof x === 'function' || (typeof x === 'object' && x !== null && (x instanceof Node || x === window))) return undefined;
          if (x && typeof x === 'object') { if (seen.has(x)) return undefined; seen.add(x); }
          if (k && SKIP_KEYS.test(k)) return undefined;
          return x;
        });
      } catch (e) { return null; }
    };
    const recordish = a => Array.isArray(a) && a.length > 0 && plain(a[0]) && Object.values(a[0]).filter(prim).length >= 2 &&
      !('width' in a[0] && 'field' in a[0]) && !('breakpoints' in a[0]);
    const numish = o => {
      if (!plain(o)) return false;
      const ks = Object.keys(o);
      if (ks.some(k => /palette|breakpoints|typography|unstable_/.test(k))) return false;
      let nums = 0;
      for (const k of ks.slice(0, 200)) {
        const v = o[k];
        if (typeof v === 'number') nums++;
        else if (plain(v) && Object.values(v).some(x => typeof x === 'number')) nums++;
        else if (recordish(v)) nums += 3;
      }
      return nums >= 3;
    };
    const rootEl = document.getElementById('root') || document.body.firstElementChild;
    const rk = Object.keys(rootEl).find(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$'));
    let start = rootEl[rk];
    if (start && start.stateNode && start.stateNode.current) start = start.stateNode.current;
    const states = {}, hashes = new Set(), grids = [];
    let total = 0, n = 0;
    const keep = (name, v) => {
      const s = ser(v);
      if (!s || s.length < 30 || s.length > MAX_ITEM || total + s.length > MAX_TOTAL) return;
      const h = s.length + ':' + s.slice(0, 200) + s.slice(-200);
      if (hashes.has(h)) return;
      hashes.add(h); total += s.length;
      states[name] = JSON.parse(s);
    };
    const stack = [start];
    while (stack.length && n < 400000) {
      const f = stack.pop(); n++;
      if (!f) continue;
      const cname = (typeof f.type === 'string' ? f.type : f.type && (f.type.displayName || f.type.name)) || 'anon';
      let h = f.memoizedState, i = 0;
      if (typeof f.type !== 'string') {
        while (h && i < 80) {
          const s = h.memoizedState;
          if (recordish(s) || numish(s)) keep(cname + '#' + i, s);
          else if (plain(s)) {
            for (const k of Object.keys(s).slice(0, 40)) {
              if (SKIP_KEYS.test(k)) continue;
              const v = s[k];
              if (recordish(v) || numish(v)) keep(cname + '#' + i + '.' + k, v);
            }
          }
          h = h.next; i++;
        }
      }
      const p = f.memoizedProps;
      if (p && typeof f.type !== 'string' && recordish(p.data)) keep(cname + '.data', p.data);   // chart datasets
      if (p && Array.isArray(p.rows) && p.rows.length && plain(p.rows[0]) && Array.isArray(p.columns)) {
        const s = ser(p.rows);
        if (s && s.length < MAX_ITEM * 5) grids.push({ comp: cname, cols: p.columns.map(c => c.field), rows: JSON.parse(s) });
      }
      if (f.child) stack.push(f.child);
      if (f.sibling) stack.push(f.sibling);
    }
    const now = new Date();
    const et = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(now)
      .reduce((a, x) => (a[x.type] = x.value, a), {});
    const stamp = et.year + et.month + et.day + '_' + et.hour + et.minute;
    const page = (location.pathname.split('/')[1] || 'home').replace(/[^a-z0-9]/gi, '') || 'home';
    const qs = new URLSearchParams(location.search);
    const sym = (qs.get('sym') || qs.get('symbol') || qs.get('ivol_sym') || '').replace(/[^A-Z0-9^/:._-]/gi, '');
    const tab = [...document.querySelectorAll('[role=tab][aria-selected=true]')]   // every selected tab, e.g. 'New Filter-Contract Data'
      .map(e => (e.innerText || '').trim()).filter(Boolean).join('-');
    const text = (document.querySelector('main') || document.body).innerText.slice(0, 400000);
    const out = { captured_at: now.toISOString(), stamp, page, sym, tab, url: location.href, title: document.title,
      text, states, grids, fibers: n };
    if (mode === 'obj') return out;
    const body = JSON.stringify(out);
    const fname = 'sg_page_' + page + (sym ? '_' + sym.replace(/[^A-Z0-9]/gi, '') : '') +
      (tab ? '_' + tab.replace(/[^A-Za-z0-9-]/g, '').slice(0, 40) : '') + '_' + stamp + '.json';
    window.__sgPageLast = { fname, bytes: body.length, states: Object.keys(states).length, grids: grids.length, fibers: n };
    let btn = document.getElementById('__sgPageSave');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = '__sgPageSave';
      btn.style.cssText = 'position:fixed;top:6px;left:200px;z-index:99999;background:#1f5fbf;color:#fff;border:0;' +
        'padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      document.body.appendChild(btn);
    }
    btn.textContent = 'SG PAGE SAVE';
    btn.onclick = () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      a.download = fname;
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = 'SAVED ' + fname.slice(8, 48);
    };
    return JSON.stringify(window.__sgPageLast);
  };
  try {
    localStorage.setItem('__sgcap', '(' + __f.toString() + ')()');   // run again: (0, eval)(localStorage.getItem('__sgcap'))
    localStorage.setItem('__sgcapfn', __f.toString());                // used by tools/sg_daily_capture.js
  } catch (e) { /* private window */ }
  return __f();
})()
