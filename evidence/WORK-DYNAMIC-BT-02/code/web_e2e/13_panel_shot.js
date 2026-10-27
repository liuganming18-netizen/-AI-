/**
 * 截「整张执行视图卡片」（含卡片头徽标 + 三图），用于人工过目。
 * 用法：node 13_panel_shot.js [runId]
 */
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');
const path = require('path');

const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中/context';
fs.mkdirSync(OUT, { recursive: true });
const RID = process.argv[2] || 'run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1668, height: 1000 } })).newPage();
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200)));
  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`, {
    waitUntil: 'domcontentloaded', timeout: 90000,
  });
  await sleep(15000);
  const card = page.locator('[data-testid="lab-chart-panel"]');
  const file = path.join(OUT, `panel_${RID}.png`);
  await card.screenshot({ path: file });
  const head = await page.evaluate(() => {
    const h = document.querySelector('[data-testid="lab-chart-panel"] .ds-card__head');
    return h ? h.innerText.replace(/\s+/g, ' ') : null;
  });
  console.log(JSON.stringify({ run_id: RID, file, head, console_errors: errs }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
