// WORK-DYNAMIC-BT-02｜前端交互验证：切标的 / 切周期 / 展开大图
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');
const path = require('path');

const RID = process.argv[2] || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const OUTDIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/20_前端端到端';
fs.mkdirSync(OUTDIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const secText = (page) => page.evaluate(() => {
  const el = document.querySelector('[data-lab-section="lab-chart"]');
  return el ? el.innerText.replace(/\n+/g, ' | ').slice(0, 300) : null;
});

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
  const page = await ctx.newPage();
  const reqs = [];
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/backtests/') && u.includes('/chart')) reqs.push(u.replace(/^.*\/api\/v1/, ''));
  });
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });

  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`,
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(14000);
  const steps = [];
  steps.push({ step: 'init', text: await secText(page), reqs: [...reqs] });

  // ① 切标的：点 BCH
  const bch = page.getByRole('button', { name: 'BCH', exact: true });
  const bchCount = await bch.count();
  if (bchCount) { await bch.first().click({ timeout: 8000 }).catch(() => {}); await sleep(9000); }
  steps.push({ step: 'click_symbol_BCH', found: bchCount, text: await secText(page), reqs: [...reqs] });

  // ② 切周期：点 4h
  const tf = page.getByRole('button', { name: '4h', exact: true });
  const tfCount = await tf.count();
  if (tfCount) { await tf.first().click({ timeout: 8000 }).catch(() => {}); await sleep(6000); }
  steps.push({ step: 'click_tf_4h', found: tfCount, text: await secText(page), reqs: [...reqs] });

  // ③ 下一笔
  const nextBtn = page.getByRole('button', { name: /下一笔/ });
  const nextCount = await nextBtn.count();
  if (nextCount) { await nextBtn.first().click({ timeout: 8000 }).catch(() => {}); await sleep(9000); }
  steps.push({ step: 'click_next_trade', found: nextCount, text: await secText(page), reqs: [...reqs] });

  await page.screenshot({ path: path.join(OUTDIR, 'interact_final.png') });

  const res = {
    run_id: RID,
    steps,
    all_chart_requests: reqs,
    with_symbol: reqs.filter((u) => /[?&]symbol=/.test(u)),
    with_timeframe: reqs.filter((u) => /[?&]timeframe=/.test(u)),
    console_errors: errs.slice(0, 8),
  };
  fs.writeFileSync(path.join(OUTDIR, 'interact.json'), JSON.stringify(res, null, 2));
  console.log(JSON.stringify(res, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
