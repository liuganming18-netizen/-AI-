/**
 * BT-KLINE-CENTER-01｜「K 线没居中 / 右侧大片空白」的尺寸变化复现与回归。
 *
 * 复现路径（用户截图对应的真实操作）：打开回测复盘页 → 浏览器窗口宽度变化
 * （拖动窗口 / 开关侧栏 / 缩放页面都会走同一条 setWidth 链）。
 *
 * 每一步都截 `[data-chart="lightweight-candles"]` 容器（含价格轴），
 * 并把「蜡烛画布」的实际像素尺寸一起落盘，供 07_measure_candles.py 逐像素测量。
 *
 * 用法：node 06_resize_center.js [run_id]
 */
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');

const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中';
const RID = process.argv[2] || 'run_FT_ENTRY_09_C1_Vs04_Z02_ACHUSDT';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

/** 取「面积最大的 canvas」= 蜡烛主画布（价格轴是窄长条，排除） */
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
  const file = `${OUT}/center_${tag}.png`;
  await page.locator('[data-chart="lightweight-candles"]').screenshot({ path: file });
  const box = await plotBox(page);
  return { tag, file, plot: box };
}

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1668, height: 1000 } })).newPage();
  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`,
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(15000);

  const steps = [];
  steps.push(await shot(page, '0_load_1668'));

  // ① 缩窄再复原（用户截图那一类路径）
  await page.setViewportSize({ width: 1200, height: 1000 }); await sleep(3000);
  steps.push(await shot(page, '1_narrow_1200'));
  await page.setViewportSize({ width: 1668, height: 1000 }); await sleep(3000);
  steps.push(await shot(page, '2_back_1668'));

  // ② 只变宽（不经历缩窄）：更极端的放大
  await page.setViewportSize({ width: 1080, height: 1000 }); await sleep(3000);
  steps.push(await shot(page, '3_narrow_1080'));
  await page.setViewportSize({ width: 1920, height: 1000 }); await sleep(3000);
  steps.push(await shot(page, '4_widen_1920'));

  // ③ 连续小步变化（模拟拖动窗口，一次拖 60px）
  for (let i = 0; i < 6; i += 1) {
    await page.setViewportSize({ width: 1920 - (i + 1) * 60, height: 1000 });
    await sleep(700);
  }
  steps.push(await shot(page, '5_drag_down_to_1560'));

  fs.writeFileSync(`${OUT}/center_steps.json`, JSON.stringify({ run_id: RID, steps }, null, 2));
  console.log(JSON.stringify({ run_id: RID, steps }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
