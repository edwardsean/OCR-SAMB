// Browser test for the page viewer (read-then-map, Stage 2a). Not run by pytest: run by hand, it drives real Chrome.
//   PUPPETEER=<path to puppeteer-core's lib/puppeteer/puppeteer-core.js> BASE=http://localhost:8002 node row_test.mjs <args>
// puppeteer-core ships with @mermaid-js/mermaid-cli (npx caches it under ~/.npm/_npx/*/node_modules/puppeteer-core).
// row_test.mjs : Fix row 1's qty on page 3, check the row's copied cells appear as buttons, click 0.00,
//   check the value; saves row_fix.png into OUT.
const puppeteer = (await import(process.env.PUPPETEER)).default;
const BASE = process.env.BASE || 'http://localhost:8002', OUT = process.env.OUT || '/tmp';
const b = await puppeteer.launch({executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox']});
const p = await b.newPage(); await p.setViewport({width: 1500, height: 1000});
await p.goto(BASE + '/batches/b-c80bbbde4d/pages/3', {waitUntil: 'networkidle0'});
await p.evaluate(() => localStorage.setItem('rv-name', 'Edward')); await p.reload({waitUntil: 'networkidle0'});
await p.evaluate(() => { document.querySelector('.fx-rows').open = true; });
const btn = await p.$('.fx-rowitem[data-f="row1"] .js-fx[data-f$="].qty"]'); await btn.click();
await new Promise(r => setTimeout(r, 500));
const chips = await p.evaluate(() => [...document.querySelectorAll('#fx-chips .fx-chip')].map(x => x.textContent));
const pick = await p.$$('#fx-chips .fx-chip'); await pick[chips.indexOf('0.00')].click();
await new Promise(r => setTimeout(r, 300));
const st = await p.evaluate(() => ({zoom: document.getElementById('fx-zlevel').textContent, value: document.getElementById('fx-value').value,
  region: document.querySelector('input[name=region]').value, field: document.querySelector('input[name=field]').value,
  row_key: document.querySelector('input[name=row_key]').value, save: !document.getElementById('fx-save').disabled}));
console.log(JSON.stringify({chips, ...st}));
await p.evaluate(() => window.scrollTo(0, document.getElementById('fixer').offsetTop - 10));
await new Promise(r => setTimeout(r, 300));
await p.screenshot({path: OUT + '/row_fix.png'});
await b.close();
