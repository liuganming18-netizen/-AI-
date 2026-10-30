/**
 * 探针 6：BACKTEST-CHART-JITTER-FIX-01 交付验证（7 项）
 *  ① 左右拖动  ② 滚轮缩放  ③ 十字光标连续移动  ④ 切换标的
 *  ⑤ 切换周期  ⑥ 调整窗口大小  ⑦ 连续拖动 30 秒
 * 每项都给出「布局是否位移 / 应用层是否重写时间轴 / 功能是否仍然可用」三组数字。
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
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 300)));

await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(3500);

/* 统一观测器：每帧采样（不去重），另计应用层时间轴写入。
   注意：容器必须**每帧重新查询** —— 切换标的/周期会让 React 按 key 重建图表子树，
   提前缓存的 DOM 节点会变成游离节点（rect 全 0），那样量到的就不是页面。 */
const install = async () => page.evaluate(() => {
  const s = (window.__v = { frames: [], writes: [], ro: 0, hoverMutations: 0 });
  /* 图表实例会随「切标的 / 切周期」重建（React key）⇒ 每帧检查实例身份，
     换了就重新包一次。否则计数会停在旧实例上（实测踩过：重建后写入次数恒 0）。 */
  const wrapTs = (chart) => {
    const ts = chart.timeScale();
    if (ts.__wrapped) return;
    for (const m of ['setVisibleRange', 'setVisibleLogicalRange', 'scrollToRealTime', 'fitContent']) {
      const orig = ts[m].bind(ts);
      const wrapped = (...a) => { s.writes.push({ t: Math.round(performance.now()), m }); return orig(...a); };
      ts[m] = wrapped;
    }
    ts.__wrapped = true;
  };
  const h0 = window.__labChartHandle;
  if (h0?.chart) wrapTs(h0.chart);
  const hb = document.querySelector('.ds-hoverbar');
  if (hb && !hb.__observed) {
    hb.__observed = true;
    new MutationObserver((l) => { s.hoverMutations += l.length; }).observe(hb, { childList: true, subtree: true, characterData: true });
  }
  const ro = new ResizeObserver(() => { s.ro += 1; });
  const tick = () => {
    const host = document.querySelector('[data-chart="lightweight-candles"]');
    if (host && host !== ro.__target) { ro.__target = host; try { ro.observe(host); } catch { /* ignore */ } }
    const hover = document.querySelector('.ds-hoverbar');
    const h = window.__labChartHandle;
    let axisW = null; let plotW = null; let bs = null; let lr = null;
    if (h?.chart) {
      wrapTs(h.chart);
      try { axisW = Math.round(h.chart.priceScale('right').width() * 10) / 10; } catch { /* ignore */ }
      try { plotW = Math.round(h.chart.timeScale().width() * 10) / 10; } catch { /* ignore */ }
      try { bs = Math.round(h.chart.timeScale().options().barSpacing * 1000) / 1000; } catch { /* ignore */ }
      try { lr = h.chart.timeScale().getVisibleLogicalRange(); } catch { /* ignore */ }
    }
    const r = host ? host.getBoundingClientRect() : null;
    s.frames.push({
      t: Math.round(performance.now()),
      top: r ? Math.round(r.top * 100) / 100 : null,
      w: r ? Math.round(r.width * 100) / 100 : null,
      h: r ? Math.round(r.height * 100) / 100 : null,
      hoverH: hover ? Math.round(hover.getBoundingClientRect().height * 100) / 100 : null,
      axisW, plotW, bs,
      from: lr ? Math.round(lr.from * 1000) / 1000 : null,
      to: lr ? Math.round(lr.to * 1000) / 1000 : null,
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return true;
});
await install();

const mark = () => page.evaluate(() => ({ f: window.__v.frames.length, w: window.__v.writes.length, ro: window.__v.ro, hm: window.__v.hoverMutations }));

const summarize = async (m0, name, extra = {}) => {
  const d = await page.evaluate((a) => ({
    frames: window.__v.frames.slice(a.f),
    writes: window.__v.writes.slice(a.w),
    ro: window.__v.ro - a.ro,
    hm: window.__v.hoverMutations - a.hm,
  }), m0);
  const ch = (k) => {
    let n = 0;
    for (let i = 1; i < d.frames.length; i += 1) if (d.frames[i][k] !== d.frames[i - 1][k]) n += 1;
    return n;
  };
  const vals = (k) => [...new Set(d.frames.map((f) => f[k]).filter((v) => v !== null))];
  /* 视窗回跳检测：逐帧 from 序列里「方向反转」与「单帧跳变」——拖动被应用层拉回去时会成对出现 */
  const seq = d.frames.map((f) => f.from).filter((v) => v !== null);
  let reversals = 0;
  let maxStep = 0;
  for (let i = 1; i < seq.length; i += 1) {
    const step = seq[i] - seq[i - 1];
    if (Math.abs(step) > maxStep) maxStep = Math.abs(step);
    if (i >= 2) {
      const prev = seq[i - 1] - seq[i - 2];
      if (prev * step < -0.3) reversals += 1;
    }
  }
  return {
    item: name,
    frames: d.frames.length,
    rangeReversals: reversals,
    rangeMaxStep: Math.round(maxStep * 100) / 100,
    topChanges: ch('top'),
    topValues: vals('top'),
    hoverHChanges: ch('hoverH'),
    hoverHValues: vals('hoverH'),
    widthChanges: ch('w'),
    widthValues: vals('w'),
    heightChanges: ch('h'),
    axisWChanges: ch('axisW'),
    plotWChanges: ch('plotW'),
    barSpacingValues: vals('bs'),
    axisWrites: d.writes.length,
    writeKinds: d.writes.reduce((acc, x) => { acc[x.m] = (acc[x.m] || 0) + 1; return acc; }, {}),
    resizeObserverFires: d.ro,
    hoverDomMutations: d.hm,
    ...extra,
  };
};

const box0 = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cy = box0.y + box0.height * 0.5;

const results = [];

/* ① 左右拖动 */
{
  const m0 = await mark();
  const r0 = await page.evaluate(() => window.__labChartHandle.visibleRange());
  await page.mouse.move(box0.x + box0.width * 0.7, cy);
  await page.mouse.down();
  for (let i = 0; i < 40; i += 1) { await page.mouse.move(box0.x + box0.width * 0.7 - i * 12, cy, { steps: 1 }); await page.waitForTimeout(20); }
  for (let i = 0; i < 40; i += 1) { await page.mouse.move(box0.x + box0.width * 0.7 - 480 + i * 12, cy, { steps: 1 }); await page.waitForTimeout(20); }
  await page.mouse.up();
  await page.waitForTimeout(900);
  const r1 = await page.evaluate(() => window.__labChartHandle.visibleRange());
  results.push(await summarize(m0, '① 左右拖动', {
    panned: r0 && r1 ? Math.round((r1.fromMs - r0.fromMs) / 3600000) : null,
  }));
}

/* ② 滚轮缩放 */
{
  const m0 = await mark();
  const bs0 = await page.evaluate(() => window.__labChartHandle.chart.timeScale().options().barSpacing);
  await page.mouse.move(box0.x + box0.width * 0.5, cy);
  for (let i = 0; i < 5; i += 1) { await page.mouse.wheel(0, -120); await page.waitForTimeout(140); }
  for (let i = 0; i < 3; i += 1) { await page.mouse.wheel(0, 120); await page.waitForTimeout(140); }
  await page.waitForTimeout(900);
  const bs1 = await page.evaluate(() => window.__labChartHandle.chart.timeScale().options().barSpacing);
  results.push(await summarize(m0, '② 滚轮缩放', {
    barSpacingBefore: Math.round(bs0 * 1000) / 1000,
    barSpacingAfter: Math.round(bs1 * 1000) / 1000,
    zoomWorks: Math.abs(bs1 - bs0) > 0.5,
  }));
}

/* ③ 十字光标连续移动 */
{
  const m0 = await mark();
  for (let i = 0; i <= 90; i += 1) {
    await page.mouse.move(box0.x + 8 + ((box0.width - 20) * i) / 90, cy, { steps: 1 });
    await page.waitForTimeout(18);
  }
  await page.waitForTimeout(500);
  results.push(await summarize(m0, '③ 十字光标连续移动'));
}

/* ④ 切换标的 */
{
  const sel = page.locator('[data-testid="lab-chart-symbol-select"]');
  const hasSel = (await sel.count()) > 0;
  let switched = null;
  if (hasSel) {
    const opts = await sel.locator('option').evaluateAll((els) => els.map((e) => e.value));
    const target = opts[1] ?? opts[0];
    const m0 = await mark();
    await sel.selectOption(target);
    await page.waitForTimeout(3500);
    const info = await page.evaluate(() => ({
      bars: (window.__labChartBars || []).length,
      sym: window.__labChartData?.symbol ?? null,
    }));
    switched = { target, ...info };
    results.push(await summarize(m0, '④ 切换标的', { switched }));
    // 切回第一个，便于后续项在同一标的上做
    await sel.selectOption(opts[0]);
    await page.waitForTimeout(3000);
  } else {
    results.push({ item: '④ 切换标的', skipped: '该 run 没有多标的（无 lab-chart-symbol-select）' });
  }
}

/* ⑤ 切换周期 */
{
  const before = await page.evaluate(() => (window.__labChartBars || []).length);
  const m0 = await mark();
  await page.locator('[data-testid="lab-chart-tf"] button[data-tf="4h"]').click();
  await page.waitForTimeout(3000);
  const after = await page.evaluate(() => ({ bars: (window.__labChartBars || []).length, tf: window.__labChartTf }));
  results.push(await summarize(m0, '⑤ 切换周期(1h→4h)', { barsBefore: before, barsAfter: after.bars, tfAfter: after.tf }));
  // 切回 1h
  await page.locator('[data-testid="lab-chart-tf"] button[data-tf="1h"]').click();
  await page.waitForTimeout(2500);
}

/* ⑥ 调整浏览器窗口大小 */
{
  const m0 = await mark();
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.waitForTimeout(1200);
  await page.setViewportSize({ width: 1680, height: 900 });
  await page.waitForTimeout(1200);
  const b = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
  results.push(await summarize(m0, '⑥ 调整窗口大小', { finalBox: b ? { w: Math.round(b.width), h: Math.round(b.height) } : null }));
}

/* ⑦ 连续拖动 30 秒 */
{
  const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
  const y = box.y + box.height * 0.5;
  const m0 = await mark();
  const r0 = await page.evaluate(() => window.__labChartHandle.visibleRange());
  await page.mouse.move(box.x + box.width * 0.5, y);
  await page.mouse.down();
  const t0 = Date.now();
  let i = 0;
  while (Date.now() - t0 < 30000) {
    const x = box.x + box.width * 0.5 + Math.sin(i / 26) * box.width * 0.33;
    await page.mouse.move(x, y, { steps: 1 });
    await page.waitForTimeout(28);
    i += 1;
  }
  await page.mouse.up();
  await page.waitForTimeout(1200);
  const r1 = await page.evaluate(() => window.__labChartHandle.visibleRange());
  results.push(await summarize(m0, '⑦ 连续拖动 30 秒', {
    steps: i,
    seconds: Math.round((Date.now() - t0) / 1000),
    pannedBack: r0 && r1 ? Math.round((r1.fromMs - r0.fromMs) / 3600000) : null,
  }));
}

const out = { run: RUN, label: LABEL, viewport: '1680x900', pageErrors, results };
fs.writeFileSync(`${OUT}/${LABEL}_probe6_verify.json`, JSON.stringify(out, null, 2));
for (const r of results) {
  console.log('\n==', r.item);
  if (r.skipped) { console.log('   skipped:', r.skipped); continue; }
  console.log('   frames', r.frames, '| topChanges', r.topChanges, '| topValues', JSON.stringify(r.topValues));
  console.log('   rangeReversals', r.rangeReversals, '| rangeMaxStep', r.rangeMaxStep);
  console.log('   hoverHChanges', r.hoverHChanges, '| hoverHValues', JSON.stringify(r.hoverHValues));
  console.log('   widthChanges', r.widthChanges, JSON.stringify(r.widthValues), '| heightChanges', r.heightChanges);
  console.log('   axisWChanges', r.axisWChanges, '| plotWChanges', r.plotWChanges, '| barSpacing', JSON.stringify(r.barSpacingValues));
  console.log('   axisWrites', r.axisWrites, JSON.stringify(r.writeKinds), '| RO fires', r.resizeObserverFires, '| hoverDomMut', r.hoverDomMutations);
  for (const k of ['panned', 'pannedBack', 'barSpacingBefore', 'barSpacingAfter', 'zoomWorks', 'barsBefore', 'barsAfter', 'tfAfter', 'finalBox', 'switched', 'steps', 'seconds']) {
    if (r[k] !== undefined) console.log('   ', k, '=', JSON.stringify(r[k]));
  }
}
if (pageErrors.length) console.log('\nPAGE ERRORS:', pageErrors.slice(0, 5));
await page.screenshot({ path: `${OUT}/${LABEL}_verify_final.png` });
await browser.close();
