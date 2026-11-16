// tools/sg_tape_capture.js - one SpotGamma Tape read (Kris 11/15/2026: "the tape needs to be read every 15 min").
//
// Run in Kris's signed-in Chrome on https://dashboard.spotgamma.com/tape (reload the tab first so the
// day's flow totals are fresh, wait ~8 s). Paste this whole file into the javascript tool, then click
// the green "SG TAPE SAVE" button it adds (top-left). That downloads sg_tape_<YYYYMMDD>_<HHMM>.json to
// Downloads; ingest it with:  railway run py -m tools.sg_tape --ingest-downloads
//
// It reads only what the page already shows (React state; nothing is sent anywhere):
//   flow    - the day's put/call totals the page draws as pies: volume, premium, delta, gamma, vega
//   lists   - Largest Daily Trades (20), Top Options Volume, Top Daily Gamma Notional, Top Daily Movers
//   prints  - every print in the Flow Data grid (the latest ~160), with greeks and block/sweep/retail flags
// The javascript tool returns only ~1,000 characters, so the data leaves through the download.
(() => {
  const rootEl = document.getElementById('root') || document.body.firstElementChild;
  const rk = Object.keys(rootEl).find(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$'));
  let start = rootEl[rk];
  if (start && start.stateNode && start.stateNode.current) start = start.stateNode.current;
  const big = (k, v) => (typeof v === 'bigint' ? v.toString() : v);

  // walk every hook's state once, matching by shape (hook order changes between releases)
  let flow = null, lists = null;
  const stack = [start];
  let n = 0;
  const isPC = o => o && typeof o === 'object' && 'put' in o && 'call' in o && 'total' in o;
  while (stack.length && n < 300000 && !(flow && lists)) {
    const f = stack.pop(); n++;
    if (!f) continue;
    let h = f.memoizedState, i = 0;
    while (h && i < 60) {
      const s = h.memoizedState;
      if (s && typeof s === 'object' && !Array.isArray(s)) {
        if (!flow && isPC(s.volume) && isPC(s.premium)) flow = s;
        if (!lists && Array.isArray(s.premium) && Array.isArray(s.volume) && Array.isArray(s.gamma) && Array.isArray(s.movers)) lists = s;
      }
      h = h.next; i++;
    }
    if (f.child) stack.push(f.child);
    if (f.sibling) stack.push(f.sibling);
  }

  // grid rows from the MUI DataGrid props
  let prints = [];
  const grid = document.querySelector('.MuiDataGrid-root');
  if (grid) {
    const gk = Object.keys(grid).find(k => k.startsWith('__reactFiber$'));
    let g = grid[gk], d = 0;
    while (g && d < 60) {
      const p = g.memoizedProps;
      if (p && Array.isArray(p.rows)) {
        prints = p.rows.map(r => ({
          tns: String(r.tnsIndex ?? r.id), ts: Number(r.ts), sym: r.underlying, aggressor: r.aggressor,
          side: r.trade_side, put: !!r.is_put, strike: r.strike, expiry: Number(r.expiry), size: r.size,
          premium: r.premium, spot: r.stock_price, price: r.price, bid: r.bid, ask: r.ask, iv: r.ivol,
          delta: r.delta, gamma: r.gamma, vega: r.vega, vol: r.daily_vol_cumsum, oi: r.prev_oi,
          block: !!r.is_block, sweep: !!r.is_sweep, spread: !!r.is_spread, cross: !!r.is_cross,
          retail: !!(r.flags && r.flags.isRetail), next_exp: !!(r.flags && r.flags.isNextExp),
          cond: r.exchangeSaleCondition || null,
        }));
        break;
      }
      g = g.return; d++;
    }
  }

  const now = new Date();
  const et = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(now)
    .reduce((a, p) => (a[p.type] = p.value, a), {});
  const stamp = `${et.year}${et.month}${et.day}_${et.hour}${et.minute}`;
  const out = { read_at: now.toISOString(), stamp, url: location.href, flow, lists, prints };
  const body = JSON.stringify(out, big);

  window.__sgTapeLast = { stamp, bytes: body.length, flow: !!flow, lists: !!lists, prints: prints.length };
  let btn = document.getElementById('__sgTapeSave');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = '__sgTapeSave';
    btn.textContent = 'SG TAPE SAVE';
    btn.style.cssText = 'position:fixed;top:6px;left:70px;z-index:99999;background:#1b8a5a;color:#fff;' +
      'border:0;padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
    document.body.appendChild(btn);
  }
  btn.onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
    a.download = `sg_tape_${stamp}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    btn.textContent = 'SAVED ' + stamp;
  };
  return JSON.stringify(window.__sgTapeLast);
})()
