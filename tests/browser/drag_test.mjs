// Browser test for picking several words on the page viewer: drag across them. Not run by pytest: run by hand, it
// drives real Chrome (see pick_all.mjs for PUPPETEER, BASE and OUT).
//   drag_test.mjs: each case drags on the paper in Fix mode and checks the value the panel takes. Words are named
//   by their copied line and token numbers (data-b, data-i), so the drag covers exactly their boxes. Saves the drag
//   in progress (drag_<n>.png: the words lit, the label by the pointer) and the panel after it (drag_<n>_panel.png).
const puppeteer = (await import(process.env.PUPPETEER)).default;
const BASE = process.env.BASE || 'http://localhost:3002', OUT = process.env.OUT || '/tmp';
const cases = [
  {page: 1, from: ['b1', 1], to: ['b1', 4], want: 'SARANA ABADI MAKMUR BERSAMA'},              // one line
  {page: 1, from: ['b41', 2], to: ['b41', 4], want: '15:16:50'},                                // cut as written
  {page: 12, from: ['b11', 2], to: ['b12', 4], want: 'JL. DR IDE ANAK AGUNG GDE KUNINGAN TIMUR, SETIABUDI'},  // two lines
  {page: 3, region: [163, 13, 190, 80], want: '321'},                                           // no box: read there
];
const b = await puppeteer.launch({executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox']});
const p = await b.newPage(); await p.setViewport({width: 1500, height: 1100, deviceScaleFactor: 2});
let ok = 0;
for (const [n, c] of cases.entries()) {
  await p.goto(BASE + '/batches/b-c80bbbde4d/pages/' + c.page, {waitUntil: 'networkidle0'});
  await p.evaluate(() => localStorage.setItem('rv-name', 'visual-test'));
  await p.reload({waitUntil: 'networkidle0'});
  await (await p.$('.js-fx')).click();
  await (await p.$('#fx-zfit')).click();
  await p.evaluate(() => document.getElementById('fx-zin').click());       // 150%: the words big enough to see
  // the drag's corners, 0-1000 on the page: around the named boxes, or the region given
  const box = await p.evaluate(c => c.region || (() => {
    const a = document.querySelector(`rect.u[data-b="${c.from[0]}"][data-i="${c.from[1]}"]`);
    const z = document.querySelector(`rect.u[data-b="${c.to[0]}"][data-i="${c.to[1]}"]`);
    const g = (e, k) => +e.getAttribute(k);
    return [g(a, 'y') - 2, g(a, 'x') - 2, g(z, 'y') + g(z, 'height') + 2, g(z, 'x') + g(z, 'width') + 2];
  })(), c);
  // the drag in the middle of the paper's own scroller AND of the window (a header that stays on top can't cover it)
  await p.evaluate(box => {
    const sheet = document.getElementById('fx-sheet'), view = document.getElementById('fx-view');
    view.scrollLeft = (box[1] + box[3]) / 2000 * sheet.offsetWidth - view.clientWidth / 2;
    view.scrollTop = (box[0] + box[2]) / 2000 * sheet.offsetHeight - view.clientHeight / 2;
    const s = sheet.getBoundingClientRect();
    window.scrollBy(0, s.top + (box[0] + box[2]) / 2000 * s.height - innerHeight / 2);
  }, box);
  await p.evaluate(() => document.fonts.ready);                             // a web font reflows the page as it lands
  await new Promise(res => setTimeout(res, 300));
  // each corner measured just before the mouse goes there, as a person aims at what they see
  const at = (y, x) => p.evaluate((y, x) => { const s = document.getElementById('fx-sheet').getBoundingClientRect();
    return [s.left + x / 1000 * s.width, s.top + y / 1000 * s.height]; }, y, x);
  const [x0, y0] = await at(box[0], box[1]);
  const under = await p.evaluate((x, y) => !!document.elementFromPoint(x, y)?.closest('#fx-sheet'), x0, y0);
  await p.mouse.move(x0, y0); await p.mouse.down();
  const [x1, y1] = await at(box[2], box[3]);
  await p.mouse.move(x1, y1, {steps: 12});
  const r = {x0, y0, x1, y1, sx: await p.evaluate(() => scrollX), sy: await p.evaluate(() => scrollY)};
  if (!under) console.log(JSON.stringify({case: n, warning: 'the drag started on something above the paper'}));
  const lit = await p.evaluate(() => document.querySelectorAll('rect.u.live').length);
  const tip = await p.evaluate(() => document.getElementById('fx-tip').textContent);
  await p.screenshot({path: `${OUT}/drag_${n}.png`, clip: {x: r.sx + Math.max(0, r.x0 - 60), y: r.sy + Math.max(0, r.y0 - 40),   // page coordinates
    width: Math.min(1400, r.x1 - r.x0 + 320), height: r.y1 - r.y0 + 110}, captureBeyondViewport: false});   // no scrolling mid-drag
  await p.mouse.up();
  await new Promise(res => setTimeout(res, 600));                          // a region is read by the server
  const got = await p.evaluate(() => document.getElementById('fx-value').value);
  const panel = await p.$('#fx-form'); await panel.screenshot({path: `${OUT}/drag_${n}_panel.png`});
  const right = got === c.want; ok += right;
  console.log(JSON.stringify({case: n, page: c.page, lit, tip, got, want: c.want, right}));
}
console.log(JSON.stringify({cases: cases.length, right: ok}));
await b.close();
