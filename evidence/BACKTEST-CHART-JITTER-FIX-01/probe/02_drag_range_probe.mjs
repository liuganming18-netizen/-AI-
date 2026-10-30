/**
 * 探针 2：逐帧记录可见逻辑范围 + 记录应用层每次 setVisibleRange / setViewport 的入参。
 * 目的：证明①拖动是否真的在平移；②应用层是否在拖动过程中把视窗「弹回」。
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
  const s = (w.__j = { frames: [], calls: [], ro: 0, hover: [], rangeEv: 0 });

  const h = w.__labChartHandle;
  const ts = h.chart.timeScale();
  for (const m of ['setVisibleRange', 'setVisibleLogicalRange', 'scrollToRealTime', 'fitContent']) {
    const orig = ts[m].bind(ts);
    ts[m] = (...a) => {
      s.calls.push({ t: Math.round(performance.now()), m, a: a.map((x) => (typeof x === 'object' ? { ...x } : x)) });
      return orig(...a);
    };
  }
  ts.subscribeVisibleLogicalRangeChange(() => { s.rangeEv += 1; });

  const ro = new ResizeObserver(() => { s.ro += 1; });
  ro.observe(host);

  const tick = () => {
    let lr = null;
    try { lr = ts.getVisibleLogicalRange(); } catch { /* ignore */ }
    const hb = document.querySelector('.ds-hoverbar');
    s.frames.push({
      t: Math.round(performance.now()),
      from: lr ? Math.round(lr.from * 1000) / 1000 : null,
      to: lr ? Math.round(lr.to * 1000) / 1000 : null,
      hoverH: hb ? Math.round(hb.getBoundingClientRect().height) : null,
      hostH: Math.round(host.getBoundingClientRect().height),
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cx = box.x + box.width * 0.6;
const cy = box.y + box.height * 0.5;

const mark = async () => page.evaluate(() => ({
  n: window.__j.frames.length,
  c: window.__j.calls.length,
  ro: window.__j.ro,
  ev: window.__j.rangeEv,
}));

const m0 = await mark();

/* 慢速左拖：每步 3px，共 120 步，模拟真实用户「慢慢往左拖」 */
await page.mouse.move(cx, cy);
await page.mouse.down();
for (let i = 0; i < 120; i += 1) {
  await page.mouse.move(cx - i * 3, cy, { steps: 1 });
  await page.waitForTimeout(30);
}
await page.mouse.up();
await page.waitForTimeout(1200);
const m1 = await mark();

const dump = await page.evaluate(({ a, b }) => ({
  frames: window.__j.frames.slice(a.n),
  calls: window.__j.calls.slice(a.c),
  roDelta: window.__j.ro - a.ro,
  rangeEvents: window.__j.rangeEv - a.ev,
  lastFrames: window.__j.frames.slice(-5),
}), { a: m0, b: m1 });

fs.writeFileSync(`${OUT}/${LABEL}_probe2_drag.json`, JSON.stringify({ run: RUN, ...dump }, null, 2));

const fs0 = dump.frames.filter((f) => f.from !== null);
const froms = fs0.map((f) => f.from);
let reversals = 0;
for (let i = 2; i < froms.length; i += 1) {
  const d1 = froms[i - 1] - froms[i - 2];
  const d2 = froms[i] - froms[i - 1];
  if (d1 * d2 < -0.5) reversals += 1;
}
const jumps = [];
for (let i = 1; i < froms.length; i += 1) {
  const d = Math.abs(froms[i] - froms[i - 1]);
  if (d > 3) jumps.push({ t: fs0[i].t, d: Math.round(d * 100) / 100 });
}
console.log('frames sampled      =', dump.frames.length);
console.log('range from 首/末    =', froms[0], '→', froms[froms.length - 1]);
console.log('总位移(根)          =', Math.round((froms[froms.length - 1] - froms[0]) * 100) / 100);
console.log('方向反转次数        =', reversals);
console.log('单帧跳变>3根次数    =', jumps.length, JSON.stringify(jumps.slice(0, 8)));
console.log('应用层时间轴写入次数=', dump.calls.length);
console.log('  →', JSON.stringify(dump.calls.map((c) => ({ m: c.m, a: c.a })).slice(0, 12)));
console.log('官方范围事件次数    =', dump.rangeEvents);
console.log('图表容器尺寸变化    =', dump.roDelta);
const hoverVals = [...new Set(dump.frames.map((f) => f.hoverH))];
console.log('读数条高度取值      =', JSON.stringify(hoverVals));
let hoverChanges = 0;
for (let i = 1; i < dump.frames.length; i += 1) if (dump.frames[i].hoverH !== dump.frames[i - 1].hoverH) hoverChanges += 1;
console.log('读数条高度变化次数  =', hoverChanges);

await page.screenshot({ path: `${OUT}/${LABEL}_drag_after.png` });
await browser.close();
