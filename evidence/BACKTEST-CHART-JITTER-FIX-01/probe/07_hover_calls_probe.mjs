/**
 * 探针 7：只做「十字光标连续移动」，把期间每一次 setVisibleRange 的入参打出来。
 * 目的：确认剩余的时间轴写入是「重复落位同一个窗口」（无害）还是「改窗口」（有害）。
 */
import fs from 'node:fs';

const { chromium } = await import(
  'file:///Users/boos/Desktop/威科夫因子共振趋势/UI%E7%95%8C%E9%9D%A2/web/node_modules/playwright/index.mjs'
);

const RUN = process.env.RUN_ID || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const BASE = process.env.BASE || 'http://127.0.0.1:3000';
const LABEL = process.env.LABEL || 'after';
const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/BACKTEST-CHART-JITTER-FIX-01/probe/out';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(3500);

await page.evaluate(() => {
  const s = (window.__c = { calls: [] });
  const ts = window.__labChartHandle.chart.timeScale();
  for (const m of ['setVisibleRange', 'setVisibleLogicalRange']) {
    const orig = ts[m].bind(ts);
    ts[m] = (...a) => {
      let before = null;
      try { const r = ts.getVisibleRange(); before = r ? [r.from, r.to] : null; } catch { /* ignore */ }
      s.calls.push({ t: Math.round(performance.now()), m, args: a.map((x) => (x && typeof x === 'object' ? { ...x } : x)), before });
      return orig(...a);
    };
  }
});

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cy = box.y + box.height * 0.5;
const m0 = await page.evaluate(() => window.__c.calls.length);

for (let i = 0; i <= 90; i += 1) {
  await page.mouse.move(box.x + 8 + ((box.width - 20) * i) / 90, cy, { steps: 1 });
  await page.waitForTimeout(18);
}
await page.waitForTimeout(800);

const calls = await page.evaluate((a) => window.__c.calls.slice(a), m0);
fs.writeFileSync(`${OUT}/${LABEL}_probe7_hover_calls.json`, JSON.stringify({ run: RUN, calls }, null, 2));
console.log('calls during pure hover =', calls.length);
for (const c of calls) console.log(' ', c.t, c.m, JSON.stringify(c.args), 'before=', JSON.stringify(c.before));
await browser.close();
