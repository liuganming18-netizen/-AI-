// WORK-DYNAMIC-BT-02｜联动机械验证：定位 K 线画布 → 悬停 → 按画布分区比对像素
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');
const path = require('path');

const RID = process.argv[2] || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const OUTDIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/20_前端端到端';
fs.mkdirSync(OUTDIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
  const page = await ctx.newPage();
  const reqs = [];
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/backtests/') && u.includes('/chart')) reqs.push(u);
  });

  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`,
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(14000);

  // 1) 列出所有画布及其视口坐标（lightweight-charts 每张图 2 个 canvas：系列 + 十字线层）
  const canvases = await page.evaluate(() => Array.from(document.querySelectorAll('canvas')).map((c, i) => {
    const r = c.getBoundingClientRect();
    return { i, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  }).filter((c) => c.w > 60 && c.h > 60));

  // 2) 按 y 聚成「图表带」，取面积最大的那条当 K 线主图
  const big = canvases.slice().sort((a, b) => b.w * b.h - a.w * a.h)[0];
  const target = { x: big.x + big.w * 0.55, y: big.y + big.h * 0.5 };

  // 3) 先离开绘图区（悬停到页面空白处）拍基准图
  await page.mouse.move(5, 5);
  await sleep(800);
  const before = path.join(OUTDIR, 'link_before.png');
  await page.screenshot({ path: before });

  // 4) 悬停 K 线画布中心
  await page.mouse.move(target.x, target.y);
  await sleep(600);
  await page.mouse.move(target.x + 20, target.y + 3);
  await sleep(1600);
  const after = path.join(OUTDIR, 'link_after.png');
  await page.screenshot({ path: after });

  const chartText = await page.evaluate(() => {
    const el = document.querySelector('[data-lab-section="lab-chart"]');
    return el ? el.innerText.slice(0, 800) : null;
  });

  const res = { run_id: RID, canvases, hover_target: target, big_canvas: big, before, after, chartText };
  fs.writeFileSync(path.join(OUTDIR, 'link_probe.json'), JSON.stringify(res, null, 2));
  console.log(JSON.stringify({ canvases: canvases.length, big, target, chartText: (chartText || '').slice(0, 200) }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
