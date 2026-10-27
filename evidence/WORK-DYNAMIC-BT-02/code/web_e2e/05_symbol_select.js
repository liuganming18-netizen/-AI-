// WORK-DYNAMIC-BT-02｜最终端到端：切标的（select）+ 各周期计数（识别源周期错标）
const { createRequire } = require('module');
const req = createRequire('/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/package.json');
const { chromium } = req('playwright');
const fs = require('fs');
const RID = process.argv[2] || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const OUTDIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/20_前端端到端';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1680, height: 1050 } })).newPage();
  const reqs = [];
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/backtests/') && u.includes('/chart')) reqs.push(u.replace(/^.*\/api\/v1/, ''));
  });
  await page.goto(`http://127.0.0.1:3000/?nav=backtest&run=${encodeURIComponent(RID)}`,
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(14000);

  const head = () => page.evaluate(() => {
    const el = document.querySelector('[data-lab-section="lab-chart"]');
    return el ? el.innerText.split('\n').map((s) => s.trim()).filter(Boolean).slice(0, 2).join(' | ') : null;
  });

  const steps = [{ step: 'init', head: await head() }];

  // ① 切标的
  await page.selectOption('[data-testid="lab-chart-symbol-select"]', 'BCH').catch((e) => steps.push({ step: 'select_err', err: String(e.message).slice(0, 120) }));
  await sleep(10000);
  steps.push({ step: 'select_BCH', head: await head(), reqs: [...reqs] });

  // ② 各周期计数
  for (const tf of ['15m', '30m', '1h', '4h']) {
    const b = page.getByRole('button', { name: tf, exact: true });
    if (await b.count()) { await b.first().click({ timeout: 8000 }).catch(() => {}); await sleep(4500); }
    steps.push({ step: `tf_${tf}`, head: await head() });
  }

  await page.screenshot({ path: `${OUTDIR}/final_view.png`, fullPage: false });
  const out = { run_id: RID, steps, requests: reqs };
  fs.writeFileSync(`${OUTDIR}/final_e2e.json`, JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
