// Run: PUPPETEER=<path to puppeteer.js> node nav_test.mjs   (e.g. the copy that ships with @mermaid-js/mermaid-cli)
const puppeteer = (await import(process.env.PUPPETEER)).default;
import fs from 'fs';
const D = new URL('..', import.meta.url).pathname;
const b = await puppeteer.launch({executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args:['--no-sandbox']});
const p = await b.newPage(); await p.setViewport({width:1800,height:1200});
const wait = ms => new Promise(r => setTimeout(r, ms));
const center = async sel => { const r = await p.$eval(sel, e => { const b = e.getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; }); return r; };
const results = [];
async function go(u){ await p.goto('file://' + D + u); await wait(1200); }

await go('ocr-pipeline.html');
let c = await center('#node-classify rect.c-backend');
await Promise.all([p.waitForNavigation({timeout: 5000}).catch(() => null), p.mouse.click(c.x, c.y)]);
results.push(['click Classify box → its detail diagram', p.url().endsWith('detail-classify.html')]);

await Promise.all([p.waitForNavigation({timeout: 5000}).catch(() => null), p.click('a.back-link')]);
results.push(['← Overview goes back', p.url().endsWith('ocr-pipeline.html')]);
await wait(1000);

c = await center('#node-enhance .node-chip.chip-detail rect');
await Promise.all([p.waitForNavigation({timeout: 5000}).catch(() => null), p.mouse.click(c.x, c.y)]);
results.push(['click "details ›" chip on Enhance', p.url().endsWith('detail-enhance.html')]);

await go('ocr-pipeline.html');
c = await center('#node-minio rect.c-database');
await p.mouse.click(c.x, c.y); await wait(600);
results.push(['click MinIO (no detail) → stays on overview, viewer focuses it', p.url().includes('ocr-pipeline.html') && p.url().includes('focus=minio')]);
await go('ocr-pipeline.html');
const popup = new Promise(r => b.once('targetcreated', t => r(t.url())));
c = await center('#node-label .node-chip.chip-live rect');
await p.mouse.click(c.x, c.y);
const opened = await Promise.race([popup, wait(4000).then(() => null)]);
results.push(['click "open ↗" on Label screen → opens the live Label screen', opened === 'http://localhost:8000/label' ? true : String(opened)]);
results.push(['…and the overview stays put', p.url().includes('ocr-pipeline.html')]);
await go('detail-classify.html');
c = await center('#node-in rect.c-backend');
await Promise.all([p.waitForNavigation({timeout: 5000}).catch(() => null), p.mouse.click(c.x, c.y)]);
results.push(['inside Classify, click "Page from step 2" → Enhance diagram', p.url().endsWith('detail-enhance.html')]);

// every detail link on every page points at a file that exists
let missing = [];
for (const f of ['ocr-pipeline.html','detail-classify.html','detail-intake.html','detail-enhance.html','detail-worker.html','detail-extract.html','vlm-first.html','detail-vf-page.html','detail-vf-teacher.html']) {
  const s = fs.readFileSync(D + f, 'utf8');
  for (const m of s.matchAll(/data-node-detail="([^"]+)"/g)) if (!fs.existsSync(D + m[1])) missing.push(f + ' → ' + m[1]);
}
results.push(['every detail link points at an existing file', missing.length === 0 ? true : missing.join(', ')]);
for (const [k, v] of results) console.log((v === true ? 'PASS ' : 'FAIL ') + k + (v === true ? '' : '  ' + v));
await b.close();
