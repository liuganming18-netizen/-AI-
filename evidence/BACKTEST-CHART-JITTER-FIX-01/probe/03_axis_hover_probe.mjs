/**
 * 探针 3：量化「纵向位移」与「横向位移」两个来源。
 *  · hostTop  —— 图表容器相对视口的纵坐标：读数条高度一变，整块往下顶（页面跳动）
 *  · axisW    —— 右价格轴宽度：一变，绘图区宽度变，蜡烛相对坐标轴横向抖
 *  · plotW    —— 时间轴绘图区宽度
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
  const vol = document.querySelectorAll('[data-chart]');
  const s = (w.__k = { frames: [], marks: [] });
  const h = w.__labChartHandle;
  const chart = h.chart;
  const tick = () => {
    let axisW = null;
    let plotW = null;
    try { axisW = Math.round(chart.priceScale('right').width() * 100) / 100; } catch { /* ignore */ }
    try { plotW = Math.round(chart.timeScale().width() * 100) / 100; } catch { /* ignore */ }
    const hb = document.querySelector('.ds-hoverbar');
    s.frames.push({
      t: Math.round(performance.now()),
      hostTop: Math.round(host.getBoundingClientRect().top * 100) / 100,
      hostH: Math.round(host.getBoundingClientRect().height * 100) / 100,
      hoverH: hb ? Math.round(hb.getBoundingClientRect().height * 100) / 100 : null,
      axisW,
      plotW,
      charts: vol.length,
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cx = box.x + box.width * 0.55;
const cy = box.y + box.height * 0.5;

const mark = () => page.evaluate(() => window.__k.frames.length);
const m0 = await mark();

/* 阶段 A：十字光标来回扫（模拟用户找某根 K 线） */
await page.mouse.move(cx - 250, cy);
for (let i = 0; i < 80; i += 1) {
  const dx = i < 40 ? i * 12 : (80 - i) * 12;
  await page.mouse.move(cx - 250 + dx, cy, { steps: 1 });
  await page.waitForTimeout(20);
}
await page.waitForTimeout(400);
const m1 = await mark();

/* 阶段 B：快速左拖（每步 18px） */
await page.mouse.move(cx + 200, cy);
await page.mouse.down();
for (let i = 0; i < 60; i += 1) {
  await page.mouse.move(cx + 200 - i * 18, cy, { steps: 1 });
  await page.waitForTimeout(16);
}
await page.mouse.up();
await page.waitForTimeout(800);
const m2 = await mark();

const dump = await page.evaluate(({ a, b, c }) => ({
  sweep: window.__k.frames.slice(a, b),
  drag: window.__k.frames.slice(b, c),
}), { a: m0, b: m1, c: m2 });

const summarize = (frames, name) => {
  const uniq = (k) => [...new Set(frames.map((f) => f[k]))];
  const changes = (k) => {
    let n = 0;
    for (let i = 1; i < frames.length; i += 1) if (frames[i][k] !== frames[i - 1][k]) n += 1;
    return n;
  };
  const range = (k) => {
    const v = frames.map((f) => f[k]).filter((x) => x !== null);
    if (!v.length) return null;
    return [Math.min(...v), Math.max(...v)];
  };
  return {
    name,
    frames: frames.length,
    hoverH: { values: uniq('hoverH'), changes: changes('hoverH') },
    hostTop: { values: uniq('hostTop'), changes: changes('hostTop'), range: range('hostTop') },
    axisW: { values: uniq('axisW'), changes: changes('axisW') },
    plotW: { values: uniq('plotW'), changes: changes('plotW') },
    hostH: { values: uniq('hostH'), changes: changes('hostH') },
  };
};

const r = [summarize(dump.sweep, 'sweep'), summarize(dump.drag, 'drag')];
fs.writeFileSync(`${OUT}/${LABEL}_probe3.json`, JSON.stringify({ run: RUN, summary: r, dump }, null, 2));
console.log(JSON.stringify(r, null, 2));
await browser.close();
