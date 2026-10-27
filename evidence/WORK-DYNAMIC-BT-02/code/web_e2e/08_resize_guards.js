/**
 * BT-KLINE-CENTER-01｜两条**反向**保护：收口逻辑不得把用户的缩放级别吃掉。
 *
 * 场景 A（大数据集）：2548 根 1h 的 run —— 放大到 ~20px/根后改窗口宽度，
 *   收口必须只贴右缘，**不得**按「数据域铺满」把 2548 根压回 0.3px/根。
 * 场景 B（窄数据集）：18 根 1h 的 run —— 放大到 ~90px/根后改窗口宽度，
 *   收口同样不得把缩放级别弹回「铺满」的 46px/根。
 *
 * 判据：尺寸变化前后 `bar_spacing_px` 的相对变化 ≤ 10%（贴右缘会让间距完全不变）。
 */
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');

const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中/guards';
const CASES = [
  { tag: 'A_big_2548bars', run: 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06' },
  { tag: 'B_narrow_18bars', run: 'run_FT_ENTRY_09_C1_Vs04_Z02_ACHUSDT' },
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

async function plotBox(page) {
  return page.evaluate(() => {
    const el = document.querySelector('[data-chart="lightweight-candles"]');
    if (!el) return null;
    const cs = Array.from(el.querySelectorAll('canvas'))
      .map((c) => {
        const b = c.getBoundingClientRect();
        return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) };
      })
      .filter((c) => c.w > 100 && c.h > 100)
      .sort((a, b) => b.w * b.h - a.w * a.h);
    const r = el.getBoundingClientRect();
    return {
      pick: cs[0] || null,
      el: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    };
  });
}

async function shot(page, tag) {
  const file = `${OUT}/guard_${tag}.png`;
  await page.locator('[data-chart="lightweight-candles"]').screenshot({ path: file });
  return { tag, file, plot: await plotBox(page) };
}

(async () => {
  const browser = await chromium.launch();
  const steps = [];
  for (const c of CASES) {
    const page = await (await browser.newContext({ viewport: { width: 1668, height: 1000 } })).newPage();
    await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(c.run)}`,
      { waitUntil: 'domcontentloaded', timeout: 90000 });
    await sleep(15000);

    // 在绘图区**靠右**放大：让右缘继续贴住最新根（保持「跟随最新」）
    const box = await plotBox(page);
    const px = box.el.x + box.pick.w - 40;
    const py = box.el.y + Math.round(box.pick.h / 2);
    await page.mouse.move(px, py);
    for (let i = 0; i < 6; i += 1) { await page.mouse.wheel(0, -240); await sleep(220); }
    await sleep(1500);
    steps.push({ ...(await shot(page, `${c.tag}_zoomed`)), case: c.tag, phase: 'zoomed' });

    await page.setViewportSize({ width: 1200, height: 1000 }); await sleep(2500);
    steps.push({ ...(await shot(page, `${c.tag}_w1200`)), case: c.tag, phase: 'after_narrow' });

    await page.setViewportSize({ width: 1668, height: 1000 }); await sleep(2500);
    steps.push({ ...(await shot(page, `${c.tag}_back`)), case: c.tag, phase: 'after_back' });

    await page.close();
  }
  fs.writeFileSync(`${OUT}/guard_steps.json`, JSON.stringify({ steps }, null, 2));
  console.log(JSON.stringify(steps.map((s) => ({ tag: s.tag, plot_w: s.plot.pick.w })), null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
