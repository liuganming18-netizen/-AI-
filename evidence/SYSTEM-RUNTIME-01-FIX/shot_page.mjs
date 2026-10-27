import pw from '/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'node:fs';

const OUT = process.argv[2] || '/tmp/page.png';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const logs = [];
page.on('console', m => logs.push(`[console.${m.type()}] ${m.text()}`));
page.on('response', async r => {
  const u = r.url();
  if (u.includes('/api/')) logs.push(`[api] ${r.status()} ${u}`);
});
await page.goto('http://127.0.0.1:3000/', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(3500);

// 抓信号区真实文本
const sigText = await page.locator('text=交易信号').first().locator('..').innerText().catch(() => '');
const bodyText = await page.locator('body').innerText();
await page.screenshot({ path: OUT, fullPage: false });

const api = await page.evaluate(async () => {
  const out = {};
  for (const p of ['/api/paper/status', '/api/v1/signals?scope=current']) {
    try {
      const r = await fetch(p);
      out[p] = { status: r.status, body: (await r.text()).slice(0, 600) };
    } catch (e) { out[p] = { error: String(e) }; }
  }
  return out;
});

fs.writeFileSync(OUT + '.json', JSON.stringify({ sigText, api, logs: logs.slice(-30), bodyText: bodyText.slice(0, 1500) }, null, 2));
console.log(JSON.stringify({ sigText: sigText.slice(0, 300), api }, null, 2).slice(0, 2000));
await browser.close();
