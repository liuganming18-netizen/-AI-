/**
 * 探针 9：**完全空闲**（不动鼠标）10 秒，看时间轴写入是否仍在持续。
 * 判断是否存在「自我维持的写入循环」。
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
await page.waitForTimeout(5000);   // 首屏落位 + 静默窗全部走完

await page.evaluate(() => {
  const s = (window.__i = { calls: [] });
  const wrap = (chart, tag) => {
    const ts = chart.timeScale();
    if (ts.__w) return;
    for (const m of ['setVisibleRange', 'setVisibleLogicalRange']) {
      const orig = ts[m].bind(ts);
      ts[m] = (...a) => { s.calls.push({ t: Math.round(performance.now()), tag, m }); return orig(...a); };
    }
    ts.__w = true;
  };
  wrap(window.__labChartHandle.chart, 'candle');
  // 另外两张图也包一层（它们共用同一个 onViewportChange 回调）
  for (const el of document.querySelectorAll('[data-chart]')) {
    if (el.dataset.chart === 'lightweight-candles') continue;
    // 通过 __labChartHandle 拿不到副图实例，改从 hub 之外无法获取 ⇒ 只统计主图
  }
});

const t0 = await page.evaluate(() => performance.now());
await page.waitForTimeout(10000);   // 什么都不做
const calls = await page.evaluate((a) => window.__i.calls.filter((c) => c.t > a), t0);
console.log('空闲 10 秒内主图时间轴写入次数 =', calls.length);
console.log(JSON.stringify(calls.slice(0, 12)));
await browser.close();
