/**
 * 探针 10：空闲期把每次 setVisibleRange 的调用栈抓出来，定位「每 840ms 写一次」的来源。
 */
const { chromium } = await import(
  'file:///Users/boos/Desktop/威科夫因子共振趋势/UI%E7%95%8C%E9%9D%A2/web/node_modules/playwright/index.mjs'
);

const RUN = process.env.RUN_ID || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const BASE = process.env.BASE || 'http://127.0.0.1:3000';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(5000);

await page.evaluate(() => {
  const s = (window.__s = { stacks: [] });
  const ts = window.__labChartHandle.chart.timeScale();
  const orig = ts.setVisibleRange.bind(ts);
  ts.setVisibleRange = (...a) => {
    if (s.stacks.length < 6) {
      const st = (new Error('probe')).stack || '';
      s.stacks.push({
        t: Math.round(performance.now()),
        args: a.map((x) => (x && typeof x === 'object' ? { ...x } : x)),
        stack: st.split('\n').slice(1, 9).map((l) => l.trim()),
      });
    }
    return orig(...a);
  };
});

await page.waitForTimeout(6000);
const stacks = await page.evaluate(() => window.__s.stacks);
console.log('captured =', stacks.length);
for (const s of stacks.slice(0, 3)) {
  console.log('\n--- t=', s.t, JSON.stringify(s.args));
  for (const line of s.stack) console.log('   ', line.slice(0, 160));
}
await browser.close();
