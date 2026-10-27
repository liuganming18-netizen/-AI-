import pw from '/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'node:fs';

const OUT = process.argv[2] || '/tmp/daemon.png';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1050 } });
const logs = [];
page.on('console', m => logs.push(`[console.${m.type()}] ${m.text()}`));
page.on('response', r => {
  const u = r.url();
  if (u.includes('/api/')) logs.push(`[api] ${r.status()} ${u}`);
});
await page.goto('http://127.0.0.1:3000/', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(5000);

const probe = {};
probe.universe = await page.locator('[data-testid="daemon-universe"]').innerText().catch(e => `ERR:${e.message}`);
// 右栏执行守护卡片整体文本
probe.daemonCard = await page.locator('[data-testid="daemon-universe"]')
  .locator('xpath=ancestor::div[3]').innerText().catch(e => `ERR:${e.message}`);
probe.signalArea = await page.locator('text=交易信号').first()
  .locator('xpath=ancestor::div[2]').innerText().catch(e => `ERR:${e.message}`);
const bodyText = await page.locator('body').innerText();

await page.screenshot({ path: OUT, fullPage: false });
// 单独把执行守护卡片截出来
await page.locator('[data-testid="daemon-universe"]')
  .locator('xpath=ancestor::div[3]')
  .screenshot({ path: OUT.replace(/\.png$/, '-card.png') }).catch(() => {});

const api = await page.evaluate(async () => {
  const out = {};
  for (const p of ['/api/v1/paper/execution_daemon/status?arm=3a', '/api/v1/signals?scope=current']) {
    try {
      const r = await fetch(p);
      out[p] = { status: r.status, body: (await r.text()).slice(0, 400) };
    } catch (e) { out[p] = { error: String(e) }; }
  }
  return out;
});

fs.writeFileSync(OUT + '.json', JSON.stringify({ probe, api, logs: logs.slice(-25), bodyText: bodyText.slice(0, 2500) }, null, 2));
console.log(JSON.stringify(probe, null, 2).slice(0, 2500));
await browser.close();
