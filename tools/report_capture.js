// Runs inside a signed-in app.hedgeye.com tab. Pulls each feed item's page and
// the report files it links to, then saves ONE bundle to Downloads.
// Signed links stay in the page: they are never returned or stored.
(() => {
  const IDS = __IDS__, MAX_TOTAL = 150e6, MAX_FILE = 60e6, PACE = 2500;
  const S = window.__hc = {done: false, n: 0, total: IDS.length, files: 0, bytes: 0, errs: 0, auth: false, capped: false, limited: false};
  const nap = ms => new Promise(r => setTimeout(r, ms));
  const wanted = u => u.hostname.includes('hedgeye.com') && (/^\/(mu|ms)\//.test(u.pathname) || /\.pdf$/i.test(u.pathname));
  const blobs = [], items = [];
  let off = 0;
  const grab = async u => {
    const name = decodeURIComponent(u.pathname.split('/').pop()).slice(0, 80);
    try {
      const r = await fetch(u.href, {credentials: u.origin === location.origin ? 'include' : 'omit'});
      if (r.status === 429) throw 'limited';
      if (!r.ok) { S.errs++; return {name, st: r.status}; }
      const b = await r.blob();
      if (b.size > MAX_FILE) return {name, skipped: 'too_big', size: b.size};
      if (S.bytes + b.size > MAX_TOTAL) throw 'cap';
      blobs.push(b);
      const f = {name, ct: r.headers.get('content-type') || '', offset: off, size: b.size};
      off += b.size; S.bytes += b.size; S.files++;
      return f;
    } catch (e) {
      if (e === 'cap' || e === 'limited') throw e;
      S.errs++;
      return {name, err: String(e).slice(0, 100)};
    }
  };
  (async () => {
    try {
      for (const id of IDS) {
        const it = {id, files: []};
        try {
          const r = await fetch('/feed_items/' + id, {credentials: 'include'});
          it.st = r.status;
          if (r.status === 429) { S.limited = true; break; }
          if (/sign_in|login|^\/insights/i.test(new URL(r.url).pathname)) { S.auth = true; break; }
          if (r.ok) {
            const d = new DOMParser().parseFromString(await r.text(), 'text/html');
            const main = d.querySelector('main');
            if (main) {
              it.title = d.title;
              const seen = new Set();
              for (const a of main.querySelectorAll('a[href]')) {
                let u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { continue; }
                const bare = u.origin + u.pathname;
                if (u.search) a.setAttribute('href', bare);
                if (!wanted(u) || seen.has(bare)) continue;
                seen.add(bare);
                it.files.push(await grab(u));
                await nap(PACE);
              }
              it.html = main.innerHTML;
            } else it.st = 'no_main';
          } else S.errs++;
        } catch (e) {
          if (e === 'cap') { S.capped = true; break; }
          if (e === 'limited') { S.limited = true; break; }
          it.err = String(e).slice(0, 100); S.errs++;
        }
        items.push(it); S.n++;
        await nap(PACE);
      }
      if (items.length) {
        const head = JSON.stringify({ts: Date.now(), items}) + '\n';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([head, ...blobs], {type: 'text/plain'}));
        a.download = 'hedgeye_bundle_' + Date.now() + '.txt';
        document.body.appendChild(a); a.click(); a.remove();
        S.saved = a.download;
      }
    } catch (e) { S.fatal = String(e).slice(0, 150); }
    S.done = true;
  })();
  return 'started ' + IDS.length;
})()
