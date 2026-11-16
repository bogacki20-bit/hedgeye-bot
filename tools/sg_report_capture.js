// tools/sg_report_capture.js - SpotGamma written reports, full text, in one run (Kris 11/15/2026: "build the rest so
// we get all of it"). Works on two pages:
//   https://dashboard.spotgamma.com/reports       FlowPatrol, Opening Setup, Sunday Note (PDF viewer text layer)
//   https://dashboard.spotgamma.com/foundersNotes AM Note / PM Note (the Founder's Note archive)
//
// Use (capture desk, Kris's FIRST Chrome): open one of those pages, wait ~8 s, paste this file into the javascript
// tool. It runs in the background and steps through the list itself, one report at a time (keep the tab in front:
// a hidden tab stops drawing PDFs). Progress:  JSON.stringify(window.__sgRep.status)
// When status.done is true, click the orange "SG REPORTS SAVE" button: it downloads
// Downloads/sg_reports_<YYYYMMDD>_<HHMM>.json; load with:  railway run py -m tools.sg_pages --ingest-downloads
//
// Options (set before pasting): window.__sgRepWant = ['FlowPatrol 2026-11-13', ...] to fetch only those titles;
// window.__sgRepDays = N to take reports dated within N days (default 30). Read-only.
(() => {
  const REP = /^(FlowPatrol|Opening Setup|Sunday Note) (\d{4}-\d{2}-\d{2})$/;
  const FN = /^(AM Note|PM Note): \w{3}, (\w+ \d{1,2}, \d{4})/;
  const parse = t => {
    let m = t.match(REP);
    if (m) return { kind: m[1], date: m[2] };
    m = t.match(FN);
    if (m) {
      const d = new Date(m[2] + ' 12:00');
      if (!isNaN(d)) return { kind: m[1], date: d.toISOString().slice(0, 10) };
    }
    return null;
  };
  const want = window.__sgRepWant || null;
  const days = window.__sgRepDays || 30;
  const cutoff = new Date(Date.now() - days * 864e5).toISOString().slice(0, 10);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const leafs = () => [...document.querySelectorAll('main *')]
    .filter(e => e.children.length === 0 && parse((e.innerText || '').trim()));
  const isPdf = () => document.querySelectorAll('.react-pdf__Page').length > 0;
  const bodyText = title => {
    const layers = [...document.querySelectorAll('.react-pdf__Page__textContent, .textLayer')];
    if (layers.length) return layers.map(e => e.innerText).join('\n\f\n');
    // Founder's Note: the article is the biggest text block that starts with the note's title
    const cands = [...document.querySelectorAll('main div, main article, main section')]
      .filter(e => (e.innerText || '').trim().startsWith(title.slice(0, 20)));
    const best = cands.sort((a, b) => b.innerText.length - a.innerText.length)[0];
    return best ? best.innerText : '';
  };
  const st = { done: false, total: 0, got: 0, failed: [], current: null };
  window.__sgRep = { status: st, reports: [] };

  (async () => {
    const titles = [...new Set(leafs().map(e => e.innerText.trim()))]
      .filter(t => (want ? want.includes(t) : parse(t).date >= cutoff));
    st.total = titles.length;
    const grab = async title => {
      const el = leafs().find(e => e.innerText.trim() === title);
      if (!el) return null;
      const before = bodyText(title);
      el.click();
      let text = '', stable = 0, last = -1;
      for (let i = 0; i < 70; i++) {          // up to ~35 s
        await sleep(500);
        const onIt = decodeURIComponent(location.pathname).includes(title.slice(0, 24));
        text = bodyText(title);
        if (onIt && text && (text !== before || i > 8)) {
          stable = text.length === last ? stable + 1 : 0;
          last = text.length;
          if (stable >= 3) break;
        }
      }
      return text && text.length > 200 ? text : null;
    };
    for (const title of titles) {
      st.current = title;
      let text = await grab(title);
      if (!text) { await sleep(3000); text = await grab(title); }   // one retry
      if (!text) { st.failed.push(title); continue; }
      const p = parse(title);
      window.__sgRep.reports.push({ title, kind: p.kind, report_date: p.date, url: location.href,
        pages: isPdf() ? document.querySelectorAll('.react-pdf__Page').length : null, text });
      st.got++;
      await sleep(1500 + Math.random() * 1500);
    }
    st.done = true; st.current = null;
    const now = new Date();
    const et = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(now)
      .reduce((a, x) => (a[x.type] = x.value, a), {});
    const stamp = et.year + et.month + et.day + '_' + et.hour + et.minute;
    const body = JSON.stringify({ captured_at: now.toISOString(), stamp, reports: window.__sgRep.reports });
    let btn = document.getElementById('__sgRepSave');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = '__sgRepSave';
      btn.style.cssText = 'position:fixed;top:6px;left:340px;z-index:99999;background:#c46a12;color:#fff;border:0;' +
        'padding:6px 10px;font:bold 12px sans-serif;border-radius:4px;cursor:pointer';
      document.body.appendChild(btn);
    }
    btn.textContent = 'SG REPORTS SAVE (' + st.got + ')';
    btn.onclick = () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      a.download = 'sg_reports_' + stamp + '.json';
      document.body.appendChild(a); a.click(); a.remove();
      btn.textContent = 'SAVED ' + st.got + ' reports';
    };
  })();
  return 'started';
})()
