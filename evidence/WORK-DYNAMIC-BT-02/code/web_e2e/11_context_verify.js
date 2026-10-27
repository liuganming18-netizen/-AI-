/**
 * BT-KLINE-CONTEXT-01 端到端验证（±7 天上下文行情）。
 *
 * 判据（全部机械可复算，不靠肉眼）：
 *   A 首屏铺满：可见逻辑范围覆盖**全部**载荷 K 线（1h 343 根 / 15m 1370 根 / 4h 86 根）
 *   B 像素留白：左右留白 ≤ 5%（用饱和像素列簇的左右边界算）
 *   C 切周期同样铺满（15m / 4h / 1h 各一次）
 *   D 放大后拖窗口不被甩走：先 setVisibleLogicalRange 缩小到 40 根，再改宽度
 *     ⇒ 可见**时间窗**必须保持（容差 2 根），barSpacing 随宽度等比变化
 *   E 主动点某笔成交 ⇒ 仍然定位收窄（旧行为不得被首屏让位吃掉）
 *   F console 无错误
 *
 * 用法：node 11_context_verify.js [runId]
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

const readState = () => {
  const h = window.__labChartHandle;
  if (!h || !h.chart) return null;
  const ts = h.chart.timeScale();
  const o = ts.options();
  let vis = null;
  let vlog = null;
  let width = 0;
  try { vis = ts.getVisibleRange(); } catch (e) { /* 空图无点可映射 */ }
  try { vlog = ts.getVisibleLogicalRange(); } catch (e) { /* 同上 */ }
  try { width = ts.width(); } catch (e) { width = 0; }
  const head = document.querySelector('[data-testid="lab-chart-panel"] .ds-card__head');
  return {
    barSpacing: o && o.barSpacing,
    rightOffset: o && o.rightOffset,
    width,
    vis: vis ? { from: Number(vis.from), to: Number(vis.to) } : null,
    vlog: vlog ? { from: Number(vlog.from), to: Number(vlog.to) } : null,
    head: head ? head.innerText.replace(/\s+/g, ' ').slice(0, 260) : null,
  };
};

/** 用页面内 canvas 采样量「蜡烛列簇」的左右边界（不依赖根数，2px 间距下也稳） */
const measureGaps = () => {
  const el = document.querySelector('[data-chart="lightweight-candles"]');
  if (!el) return null;
  const canvases = Array.from(el.querySelectorAll('canvas'))
    .map((c) => ({ c, b: c.getBoundingClientRect() }))
    .filter((x) => x.b.width > 100 && x.b.height > 60)
    .sort((a, b) => b.b.width * b.b.height - a.b.width * a.b.height);
  if (!canvases.length) return null;
  const { c, b } = canvases[0];
  const ctx = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  const data = ctx.getImageData(0, 0, w, h).data;
  // 蜡烛是彩色（红/绿）实体，背景是白/浅灰 ⇒ 用「通道差」找彩色列
  const cols = [];
  for (let x = 0; x < w; x++) {
    let hits = 0;
    for (let y = 0; y < h; y++) {
      const i = (y * w + x) * 4;
      const r = data[i];
      const g = data[i + 1];
      const bl = data[i + 2];
      const a = data[i + 3];
      if (a < 40) continue;
      if (Math.max(r, g, bl) - Math.min(r, g, bl) > 40) hits++;
    }
    cols.push(hits);
  }
  const th = 2;
  let left = -1;
  let right = -1;
  for (let x = 0; x < w; x++) if (cols[x] >= th) { left = x; break; }
  for (let x = w - 1; x >= 0; x--) if (cols[x] >= th) { right = x; break; }
  if (left < 0) return { plot_w: w, css_w: Math.round(b.width), left: null, right: null, left_gap_pct: null, right_gap_pct: null };
  return {
    plot_w: w,
    css_w: Math.round(b.width),
    left,
    right,
    left_gap_pct: +((left / w) * 100).toFixed(2),
    right_gap_pct: +(((w - 1 - right) / w) * 100).toFixed(2),
  };
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1668, height: 1000 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 300)));

  const steps = [];
  const shot = async (tag) => {
    const file = path.join(OUT, `verify_${tag}_${RID}.png`);
    await page.locator('[data-chart="lightweight-candles"]').screenshot({ path: file });
    return file;
  };
  const snap = async (tag) => {
    const st = await page.evaluate(readState);
    const gaps = await page.evaluate(measureGaps);
    const file = await shot(tag);
    const rec = { tag, st, gaps, file };
    steps.push(rec);
    return rec;
  };
  const pickTf = async (label) => {
    const grp = page.locator('[data-testid="lab-chart-tf"]');
    await grp.getByRole('button', { name: label, exact: true }).click();
    await sleep(3500);
  };

  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`, {
    waitUntil: 'domcontentloaded', timeout: 90000,
  });
  await sleep(15000);

  // A/B —— 首屏（默认 1h）
  await snap('A_first_1h');

  // C —— 切周期
  await pickTf('15m'); await snap('C_tf_15m');
  await pickTf('4h');  await snap('C_tf_4h');
  await pickTf('1h');  await snap('C_tf_back_1h');

  // D —— 放大到 40 根后改宽度，时间窗必须保持
  await page.evaluate(() => {
    const ts = window.__labChartHandle.chart.timeScale();
    ts.setVisibleLogicalRange({ from: 150, to: 190 });
  });
  await sleep(1200);
  const dBefore = await page.evaluate(readState);
  await page.setViewportSize({ width: 1200, height: 1000 });
  await sleep(1500);
  const dMid = await page.evaluate(readState);
  await page.setViewportSize({ width: 1668, height: 1000 });
  await sleep(1500);
  const dAfter = await page.evaluate(readState);
  steps.push({ tag: 'D_zoom_resize', before: dBefore, mid: dMid, after: dAfter, file: await shot('D_zoom_resize') });

  // E —— 主动定位入口仍在（1 笔时「下一笔」按钮禁用 ⇒ 直接调 setViewport 验证句柄可用）
  // 注意：官方库落位是异步生效，调用后必须等一下再读，否则读到的是旧范围（实测踩过）。
  const locateOk = await page.evaluate(() => {
    const h = window.__labChartHandle;
    return h.setViewport(1717146900000 - 43200000, 1717171200000 + 43200000);
  });
  await sleep(1500);
  const locateAfter = await page.evaluate(readState);
  steps.push({ tag: 'E_locate_entry', locateOk, after: locateAfter, file: await shot('E_locate_entry') });

  const payload = {
    run_id: RID,
    captured_at: new Date().toISOString(),
    steps,
    console_errors: errs,
  };
  fs.writeFileSync(path.join(OUT, `verify_steps_${RID}.json`), JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
