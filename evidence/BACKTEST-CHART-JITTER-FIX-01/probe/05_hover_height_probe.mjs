/**
 * 探针 5：扫过整幅图（含委托/信号所在柱），测读数条高度的真实分布与最大高度。
 * 用途：确定「固定高度」该取多少，避免靠猜。
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
  const host = document.querySelector('[data-chart="lightweight-candles"]');
  const s = (window.__h = { samples: [] });
  const tick = () => {
    const hb = document.querySelector('.ds-hoverbar');
    s.samples.push({
      hoverH: hb ? Math.round(hb.getBoundingClientRect().height * 100) / 100 : null,
      top: Math.round(host.getBoundingClientRect().top * 100) / 100,
      text: hb ? (hb.textContent || '').slice(0, 60) : '',
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cy = box.y + box.height * 0.5;
const m0 = await page.evaluate(() => window.__h.samples.length);

/* 从左到右慢慢扫过整幅图，每一步停 25ms（让读数条完成一次渲染） */
for (let i = 0; i <= 100; i += 1) {
  const x = box.x + 6 + ((box.width - 14) * i) / 100;
  await page.mouse.move(x, cy, { steps: 1 });
  await page.waitForTimeout(25);
}
await page.waitForTimeout(500);
/* 再把鼠标移出图外，记录空态高度 */
await page.mouse.move(box.x + box.width / 2, box.y - 40, { steps: 2 });
await page.waitForTimeout(600);

const samples = await page.evaluate((a) => window.__h.samples.slice(a), m0);
const hist = {};
for (const s of samples) hist[s.hoverH] = (hist[s.hoverH] || 0) + 1;
const tops = {};
for (const s of samples) tops[s.top] = (tops[s.top] || 0) + 1;

const result = {
  n: samples.length,
  heightHistogram: hist,
  maxHeight: Math.max(...samples.map((s) => s.hoverH).filter((v) => v !== null)),
  minHeight: Math.min(...samples.map((s) => s.hoverH).filter((v) => v !== null)),
  topValues: tops,
  worstSamples: samples.filter((s) => s.hoverH >= Math.max(...samples.map((x) => x.hoverH))).slice(0, 3),
};
fs.writeFileSync(`${OUT}/${LABEL}_probe5_hoverheight.json`, JSON.stringify({ run: RUN, result, samples }, null, 2));
console.log(JSON.stringify(result, null, 2));
await browser.close();
