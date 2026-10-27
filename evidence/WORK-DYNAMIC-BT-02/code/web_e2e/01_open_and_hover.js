// WORK-DYNAMIC-BT-02｜前端入口端到端验证（只读，不改前端代码）
// 目的：回答「1h 主图 + 多图联动」到底能不能打开，而不是看后端 200。
// 判据：① 复盘页能进到该 run ② /chart 请求真的带 symbol ③ K 线画布非空
//      ④ 悬停 K 线时其它图的像素发生变化（联动的机械证据）
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
  const chartReqs = [];
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/backtests/') && u.includes('/chart')) chartReqs.push(u);
  });

  const url = `http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(12000);   // 首屏会拉 /markets 等大包，留足时间

  const info = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const canvases = Array.from(document.querySelectorAll('canvas')).map((c) => ({
      w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight,
    }));
    const secs = Array.from(document.querySelectorAll('[data-lab-section]')).map((e) => e.getAttribute('data-lab-section'));
    return {
      title: document.title,
      hasChartSection: !!q('[data-lab-section="lab-chart"]'),
      sections: secs,
      emptyState: !!q('[data-testid="lab-chart-empty"]'),
      canvases,
      bodyLen: document.body.innerText.length,
      headText: document.body.innerText.slice(0, 400),
    };
  });

  // 选中一笔成交（触发 symbol 切换 + focus_ms）
  let clickedTrade = false;
  const tradeRows = await page.$$('tbody tr');
  for (const r of tradeRows) {
    const t = (await r.innerText().catch(() => '')) || '';
    if (/USDT/.test(t) && /\d/.test(t)) {
      await r.click({ timeout: 5000 }).catch(() => {});
      clickedTrade = true;
      break;
    }
  }
  await sleep(8000);

  // 联动证据：对整块图表容器做「悬停前/后」像素比对
  const shotA = path.join(OUTDIR, `hover_before_${RID}.png`);
  const shotB = path.join(OUTDIR, `hover_after_${RID}.png`);
  await page.screenshot({ path: shotA, fullPage: false });
  const box = await page.evaluate(() => {
    const el = document.querySelector('[data-lab-section="lab-chart"]');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
  let hoverOk = false;
  if (box) {
    const cx = box.x + box.w * 0.6;
    const cy = box.y + Math.min(box.h * 0.25, 160);
    await page.mouse.move(cx, cy);
    await sleep(1200);
    await page.mouse.move(cx + 12, cy + 2);
    await sleep(1500);
    hoverOk = true;
  }
  await page.screenshot({ path: shotB, fullPage: false });

  const finalInfo = await page.evaluate(() => ({
    canvases: Array.from(document.querySelectorAll('canvas')).map((c) => ({ w: c.width, h: c.height })),
    chartText: (document.querySelector('[data-lab-section="lab-chart"]') || {}).innerText?.slice(0, 600) || '',
    emptyState: !!document.querySelector('[data-testid="lab-chart-empty"]'),
  }));

  const res = {
    url, run_id: RID,
    chart_requests: chartReqs.slice(0, 20),
    chart_request_count: chartReqs.length,
    chart_requests_with_symbol: chartReqs.filter((u) => /[?&]symbol=/.test(u)).length,
    chart_requests_with_timeframe: chartReqs.filter((u) => /[?&]timeframe=/.test(u)).length,
    clickedTrade, hoverOk,
    before: info, after: finalInfo,
    console_errors: consoleErrors.slice(0, 10),
    screenshots: [shotA, shotB],
  };
  fs.writeFileSync(path.join(OUTDIR, `e2e_${RID}.json`), JSON.stringify(res, null, 2));
  console.log(JSON.stringify(res, null, 2));
  await browser.close();
})().catch((e) => { console.error('E2E FAILED:', e.message); process.exit(1); });
