/**
 * BACKTEST-CHART-JITTER-FIX-01 · 探针 0：到达回测图表并截图
 * 只读：不改任何业务文件，只驱动浏览器。
 */
const { chromium } = await import(
  'file:///Users/boos/Desktop/威科夫因子共振趋势/UI%E7%95%8C%E9%9D%A2/web/node_modules/playwright/index.mjs'
);

const RUN = process.env.RUN_ID || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const BASE = process.env.BASE || 'http://127.0.0.1:3000';
const OUT = process.env.OUT || '/Users/boos/Desktop/威科夫因子共振趋势/evidence/BACKTEST-CHART-JITTER-FIX-01/probe';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
page.on('console', (m) => {
  const t = m.text();
  if (m.type() === 'error' || /warn/i.test(t)) console.log('[console]', m.type(), t.slice(0, 200));
});
page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 300)));

await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/shot_00_landing.png` });

console.log('url =', page.url());
const title = await page.title();
console.log('title =', title);

// 找图表容器
const has = await page.locator('[data-chart="lightweight-candles"]').count();
console.log('candles container count =', has);
const panels = await page.locator('[data-testid="lab-chart-panel"]').count();
console.log('lab-chart-panel count =', panels);
if (has > 0) {
  const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
  console.log('candles box =', JSON.stringify(box));
}
await page.screenshot({ path: `${OUT}/shot_01_chart.png`, fullPage: false });
await browser.close();
