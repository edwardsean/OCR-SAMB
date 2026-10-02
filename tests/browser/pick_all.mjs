// Browser test for the page viewer (read-then-map, Stage 2a; the web app's, frontend/). Not run by pytest: run by
// hand, it drives real Chrome (CHROME_PATH). BASE is the web app.
//   PUPPETEER=<path to puppeteer-core's lib/puppeteer/puppeteer-core.js> BASE=http://localhost:3002 node pick_all.mjs <args>
// puppeteer-core ships with @mermaid-js/mermaid-cli (npx caches it under ~/.npm/_npx/*/node_modules/puppeteer-core).
// pick_all.mjs <page_no> [word,word,…]: hover and click EVERY box on the page in Fix mode (zoomed as Fix zooms);
//   prints how many hover and click right; saves hover crops of the words named (hover_<page>_<word>.png) and
//   the zoomed viewer (zoomed_<page>.png) into OUT (default /tmp).
const puppeteer = (await import(process.env.PUPPETEER)).default;
const BASE = process.env.BASE || 'http://localhost:3002', OUT = process.env.OUT || '/tmp';
const page_no = process.argv[2] || '3', want = (process.argv[3] || '').split(',').filter(Boolean);
const b = await puppeteer.launch({executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox']});
const p = await b.newPage(); await p.setViewport({width: 1500, height: 1100, deviceScaleFactor: 2});
await p.goto(BASE + '/batches/b-c80bbbde4d/pages/' + page_no, {waitUntil: 'networkidle0'});
await p.evaluate(() => localStorage.setItem('rv-name', 'visual-test'));
await p.reload({waitUntil: 'networkidle0'});
await (await p.$('.js-fx')).click();                          // Fix mode on the first field
const ids = await p.evaluate(() => [...document.querySelectorAll('rect.u')].map(r => r.dataset.u));
let hoverBad = [], clickBad = [], ok = 0;
for (const id of ids) {
  const r = await p.evaluate(id => { const e = document.querySelector('rect.u[data-u="' + id + '"]'); e.scrollIntoView({block: 'center'});
    const bb = e.getBoundingClientRect(); return {x: bb.x + bb.width / 2, y: bb.y + bb.height / 2, t: e.dataset.t, w: bb.width, h: bb.height}; }, id);
  const under = await p.evaluate((x, y) => { const e = document.elementFromPoint(x, y); return e && e.dataset ? e.dataset.u : null; }, r.x, r.y);
  if (under !== id) { hoverBad.push({id, t: r.t, under}); continue; }
  await p.mouse.click(r.x, r.y);
  const v = await p.evaluate(() => document.getElementById('fx-value').value);
  if (v !== r.t) clickBad.push({id, t: r.t, got: v}); else ok++;
}
const hs = await p.evaluate(() => [...document.querySelectorAll('rect.u')].map(r => r.getBoundingClientRect().height).sort((a, b) => a - b));
console.log(JSON.stringify({page: page_no, zoom: await p.evaluate(() => document.getElementById('fx-zlevel').textContent), median_box_px: Math.round(hs[hs.length >> 1] * 10) / 10, units: ids.length, clicked_right: ok, hover_wrong: hoverBad.length, click_wrong: clickBad.length,
  hover_examples: hoverBad.slice(0, 5), click_examples: clickBad.slice(0, 5)}));
// crops of chosen words while hovered: the box must sit on that printed word
for (const t of want) {
  const id = await p.evaluate(t => { const e = [...document.querySelectorAll('rect.u')].find(r => r.dataset.t === t || r.dataset.tess === t || r.dataset.t.startsWith(t)); return e ? e.dataset.u : null; }, t);
  if (!id) { console.log('no box for', t); continue; }
  const r = await p.evaluate(id => { const e = document.querySelector('rect.u[data-u="' + id + '"]'); e.scrollIntoView({block: 'center'});
    const bb = e.getBoundingClientRect(); return {x: bb.x, y: bb.y, w: bb.width, h: bb.height, t: e.dataset.t, tess: e.dataset.tess}; }, id);
  await p.mouse.move(r.x + r.w / 2, r.y + r.h / 2);
  await p.screenshot({path: OUT + '/hover_' + page_no + '_' + t.replace(/[^A-Za-z0-9]/g, '_') + '.png', clip: {x: Math.max(0, r.x - 90), y: Math.max(0, r.y - 30), width: r.w + 180, height: r.h + 60}});
  console.log('hover', JSON.stringify(r));
}
await (await p.$('.js-fx')).click(); await new Promise(r => setTimeout(r, 300));
await p.evaluate(() => window.scrollTo(0, document.getElementById('fixer').offsetTop - 10));
await p.screenshot({path: OUT + '/zoomed_' + page_no + '.png'});
await b.close();
