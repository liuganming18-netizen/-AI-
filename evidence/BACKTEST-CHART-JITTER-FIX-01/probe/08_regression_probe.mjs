/**
 * 探针 8：回归确认（不只看「不抖了」，还要确认功能没被削弱）
 *  (a) 深链手动视窗 `?vf=&vt=` 仍能落位
 *  (b) 读数条内容没有被裁掉（scrollHeight ≤ clientHeight）
 *  (c) 两种状态各截一张图
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

/* (a) 深链：显式指定 2021-12-13 00:00 ~ 2021-12-16 00:00 UTC */
const VF = 1639353600000;
const VT = 1639612800000;
await page.goto(
  `${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}&vf=${VF}&vt=${VT}`,
  { waitUntil: 'domcontentloaded' },
);
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(4000);

const restored = await page.evaluate(() => {
  const h = window.__labChartHandle;
  return h ? h.visibleRange() : null;
});
const badge = await page.locator('[data-testid="lab-viewport-mode"]').count();

/* (b)(c) 读数条是否被裁 */
const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5, { steps: 3 });
await page.waitForTimeout(500);
const filled = await page.evaluate(() => {
  const el = document.querySelector('.ds-hoverbar');
  return { h: el.getBoundingClientRect().height, scrollH: el.scrollHeight, clientH: el.clientHeight, text: (el.textContent || '').slice(0, 80) };
});
await page.screenshot({ path: `${OUT}/${LABEL}_hover_filled.png`, clip: { x: box.x - 20, y: box.y - 120, width: box.width + 40, height: box.height + 200 } });

await page.mouse.move(box.x + box.width * 0.5, box.y - 60, { steps: 3 });
await page.waitForTimeout(600);
const empty = await page.evaluate(() => {
  const el = document.querySelector('.ds-hoverbar');
  return { h: el.getBoundingClientRect().height, scrollH: el.scrollHeight, clientH: el.clientHeight, text: (el.textContent || '').slice(0, 80) };
});
await page.screenshot({ path: `${OUT}/${LABEL}_hover_empty.png`, clip: { x: box.x - 20, y: box.y - 120, width: box.width + 40, height: box.height + 200 } });

const result = {
  deepLink: {
    target: { fromMs: VF, toMs: VT },
    restored,
    fromErrHours: restored ? Math.round((restored.fromMs - VF) / 3600000) : null,
    toErrHours: restored ? Math.round((restored.toMs - VT) / 3600000) : null,
    badgeVisible: badge > 0,
  },
  hoverBar: { filled, empty, clipped: filled.scrollH > filled.clientH + 1 || empty.scrollH > empty.clientH + 1 },
};
fs.writeFileSync(`${OUT}/${LABEL}_probe8_regression.json`, JSON.stringify({ run: RUN, ...result }, null, 2));
console.log(JSON.stringify(result, null, 2));
await browser.close();
