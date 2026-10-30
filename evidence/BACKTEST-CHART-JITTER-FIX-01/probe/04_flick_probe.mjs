/**
 * 探针 4：快速甩动拖动（真实用户「一把拉过去」）下的视窗回弹测量。
 * 判据：逐帧 from 出现「方向反转」或「单帧回跳」即证明应用层把视窗拉回去了。
 */
import fs from 'node:fs';

const { chromium } = await import(
  'file:///Users/boos/Desktop/威科夫因子共振趋势/UI%E7%95%8C%E9%9D%A2/web/node_modules/playwright/index.mjs'
);

const RUN = process.env.RUN_ID || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const BASE = process.env.BASE || 'http://127.0.0.1:3000';
const LABEL = process.env.LABEL || 'baseline';
const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/BACKTEST-CHART-JITTER-FIX-01/probe/out';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(3000);

await page.evaluate(() => {
  const w = window;
  const host = document.querySelector('[data-chart="lightweight-candles"]');
  const s = (w.__f = { frames: [], writes: [] });
  const ts = w.__labChartHandle.chart.timeScale();
  for (const m of ['setVisibleRange', 'setVisibleLogicalRange', 'scrollToRealTime', 'fitContent']) {
    const orig = ts[m].bind(ts);
    ts[m] = (...a) => { s.writes.push({ t: Math.round(performance.now()), m }); return orig(...a); };
  }
  const tick = () => {
    let lr = null;
    try { lr = ts.getVisibleLogicalRange(); } catch { /* ignore */ }
    s.frames.push({
      t: Math.round(performance.now()),
      from: lr ? Math.round(lr.from * 1000) / 1000 : null,
      to: lr ? Math.round(lr.to * 1000) / 1000 : null,
      hostTop: Math.round(host.getBoundingClientRect().top * 100) / 100,
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cy = box.y + box.height * 0.5;

const m0 = await page.evaluate(() => ({ f: window.__f.frames.length, w: window.__f.writes.length }));

/* 快速甩动：每步 24px、每步间隔 12ms，模拟一把拖到左边 */
await page.mouse.move(box.x + box.width * 0.85, cy);
await page.mouse.down();
for (let i = 0; i < 45; i += 1) {
  await page.mouse.move(box.x + box.width * 0.85 - i * 24, cy, { steps: 1 });
  await page.waitForTimeout(12);
}
await page.mouse.up();
await page.waitForTimeout(1500);

const dump = await page.evaluate((a) => ({
  frames: window.__f.frames.slice(a.f),
  writes: window.__f.writes.slice(a.w),
}), m0);

const fs0 = dump.frames.filter((f) => f.from !== null);
const froms = fs0.map((f) => f.from);
const diffs = [];
for (let i = 1; i < froms.length; i += 1) diffs.push(froms[i] - froms[i - 1]);
let reversals = 0;
const reversalPoints = [];
for (let i = 1; i < diffs.length; i += 1) {
  if (diffs[i - 1] < -0.3 && diffs[i] > 0.3) { reversals += 1; reversalPoints.push({ t: fs0[i].t, from: froms[i], d: Math.round(diffs[i] * 100) / 100 }); }
  if (diffs[i - 1] > 0.3 && diffs[i] < -0.3) { reversals += 1; reversalPoints.push({ t: fs0[i].t, from: froms[i], d: Math.round(diffs[i] * 100) / 100 }); }
}
const jumps = [];
for (let i = 1; i < froms.length; i += 1) {
  if (Math.abs(diffs[i - 1]) >= 2.5) jumps.push({ t: fs0[i].t, d: Math.round(diffs[i - 1] * 100) / 100 });
}
const topChanges = (() => {
  let n = 0;
  for (let i = 1; i < dump.frames.length; i += 1) if (dump.frames[i].hostTop !== dump.frames[i - 1].hostTop) n += 1;
  return n;
})();

const result = {
  frames: dump.frames.length,
  fromStart: froms[0],
  fromEnd: froms[froms.length - 1],
  netTravel: Math.round((froms[froms.length - 1] - froms[0]) * 100) / 100,
  maxStep: Math.round(Math.max(...diffs.map(Math.abs)) * 100) / 100,
  reversals,
  reversalPoints: reversalPoints.slice(0, 10),
  bigJumps: jumps.slice(0, 10),
  axisWrites: dump.writes.length,
  hostTopChanges: topChanges,
  hostTopValues: [...new Set(dump.frames.map((f) => f.hostTop))],
};
fs.writeFileSync(`${OUT}/${LABEL}_probe4_flick.json`, JSON.stringify({ run: RUN, result, frames: dump.frames, writes: dump.writes }, null, 2));
console.log(JSON.stringify(result, null, 2));
await browser.close();
