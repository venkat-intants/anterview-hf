import { chromium } from 'playwright';
const b = await chromium.launch();
const ctx = await b.newContext();
const p = await ctx.newPage();
const log = [];
p.on('request', r => {
  if (r.url().includes('/auth/me')) log.push('REQ  /auth/me');
});
p.on('response', async r => {
  if (r.url().includes(':8002')) {
    let extra = '';
    if (r.url().includes('/auth/me')) {
      try { extra = ' body=' + (await r.text()).slice(0, 120); } catch (e) { extra = ' (body read err)'; }
    }
    log.push('RESP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().replace('http://localhost:8002', '') + extra);
  }
});
p.on('requestfailed', r => {
  if (r.url().includes(':8002')) log.push('FAIL ' + r.url().replace('http://localhost:8002','') + ' :: ' + r.failure()?.errorText);
});
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
p.on('console', m => { if (m.type() === 'error') errs.push('console.error: ' + m.text()); });

let navs = [];
p.on('framenavigated', f => { if (f === p.mainFrame()) navs.push(f.url().replace('http://localhost:5173','')); });

await p.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
await p.fill('input[type=email]', 'realtest@intants.com');
await p.fill('input[type=password]', 'Test1234!');
await p.click('button[type=submit]');
await p.waitForTimeout(10000);
console.log('FINAL_URL=' + p.url());
console.log('NAVS=' + JSON.stringify(navs));
console.log('ERRORS=' + JSON.stringify(errs));
console.log('TRACE:');
for (const l of log) console.log('  ' + l);
await b.close();
