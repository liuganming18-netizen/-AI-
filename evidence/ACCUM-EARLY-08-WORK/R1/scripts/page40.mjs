// 40b｜页面取证（Playwright，只读打开生产前端 3000，不改任何前端代码）
// 用法：node page40.mjs <out.png> <symbol1,symbol2,...>
// 说明：ESM 不认 NODE_PATH，故用绝对路径动态 import 前端工程里已装好的 playwright，
//       不额外安装、不往 UI 工程里加文件。
const PW = process.env.PW_MODULE
  || '/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/node_modules/playwright/index.mjs';
const { chromium } = await import(PW);

const OUT_PNG = process.argv[2] || '/tmp/page40.png';
const SYMBOLS = (process.argv[3] || '').split(',').filter(Boolean);
// 研究信号的确定性 uid（形如 accum05_4a_ACTUSDT_1784116799999）——比 symbol 强得多，
// 因为 symbol 可能合法地出现在「观察区」里，而 uid 只可能来自研究信号本身。
const UIDS = (process.argv[4] || '').split(',').filter(Boolean);

const R = { url: 'http://127.0.0.1:3000/', started_utc: new Date().toISOString() };

const browser = await chromium.launch({ headless: true });
try {
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  const sigResp = [];
  const observeSyms = new Set();
  page.on('response', async (resp) => {
    const u = resp.url();
    if (u.includes('/signals/observe')) {
      try {
        const j = await resp.json();
        for (const it of (j.items || [])) if (it.symbol) observeSyms.add(it.symbol);
      } catch { /* 忽略 */ }
    }
    if (!u.includes('/signals?')) return;
    const rec = { url: u, status: resp.status() };
    try {
      const j = await resp.json();
      rec.total = j.total ?? null;
      rec.items_n = (j.items || []).length;
      rec.has_more = j.has_more ?? null;
      rec.scope = (j.quality || {}).scope ?? null;
      rec.quality_status = (j.quality || {}).status ?? null;
      rec.excluded_n = (j.quality || {}).excluded_n ?? null;
    } catch (e) {
      rec.parse_error = String(e).slice(0, 200);
    }
    sigResp.push(rec);
  });
  const consoleErrs = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => consoleErrs.push('pageerror: ' + String(e).slice(0, 300)));

  await page.goto(R.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // 等信号面板出现（最多 45s；不把「没等到」当失败，如实记录）
  let panelFound = true;
  try {
    await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 45000 });
  } catch { panelFound = false; }
  // 再等一轮数据（空态或行）
  try {
    await page.waitForSelector('[data-testid="signal-row"], [data-testid="signal-empty"], ' +
      '[data-testid="signal-error"], [data-testid="signal-not-connected"]', { timeout: 30000 });
  } catch { /* 如实记录 */ }

  const dump = async (sel) => {
    const loc = page.locator(sel);
    const n = await loc.count();
    const texts = [];
    for (let i = 0; i < Math.min(n, 5); i++) {
      texts.push(((await loc.nth(i).innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 300));
    }
    return { count: n, sample: texts };
  };

  R.panel_found = panelFound;
  R.rows = await dump('[data-testid="signal-row"]');
  R.empty = await dump('[data-testid="signal-empty"]');
  R.error_state = await dump('[data-testid="signal-error"]');
  R.not_connected = await dump('[data-testid="signal-not-connected"]');

  // 展开「查看诊断与历史候选明细」——它是否会把研究信号列出来？
  let diagExpanded = false;
  try {
    const sum = page.locator('[data-testid="signal-empty"] summary').first();
    if (await sum.count()) { await sum.click({ timeout: 5000 }); diagExpanded = true; await page.waitForTimeout(2500); }
  } catch { /* 如实记录 */ }
  R.diag_expanded = diagExpanded;
  // 等诊断真加载出来（守护状态接口首次较慢）；三种结局都要如实记录
  let diagState = 'absent';
  try {
    await page.waitForSelector(
      '[data-testid="signal-empty-diagnostics"], [data-testid="signal-empty-nodiag"]',
      { timeout: 30000 });
    diagState = (await page.locator('[data-testid="signal-empty-diagnostics"]').count())
      ? 'loaded' : 'nodiag';
  } catch {
    diagState = (await page.locator('[data-testid="signal-empty-pending"]').count())
      ? 'pending' : 'absent';
  }
  R.diag_state = diagState;
  R.empty_diagnostics = await dump('[data-testid="signal-empty-diagnostics"]');
  // 若有「历史回放候选明细」按钮，点开再抓一次
  const discToggle = page.locator('[data-testid="signal-discarded-toggle"]');
  if (await discToggle.count()) {
    await discToggle.first().click().catch(() => {});
    await page.waitForTimeout(1500);
  }
  R.discarded_toggle = await dump('[data-testid="signal-discarded-toggle"]');
  R.discarded_rows = await dump('[data-testid="signal-discarded-row"]');

  // 研究信号 symbol 是否出现在**信号面板内**（面板外可能出现在观察区，另有来源）
  const panelText = (await page.locator('[data-testid="signal-panel"]').innerText().catch(() => '')) || '';
  R.panel_text_len = panelText.length;
  R.panel_symbol_hits = SYMBOLS.filter((s) => panelText.includes(s));
  R.panel_uid_hits = UIDS.filter((u) => panelText.includes(u));
  R.panel_text_head = panelText.replace(/\s+/g, ' ').slice(0, 400);

  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  R.body_len = body.length;
  const hits = SYMBOLS.filter((s) => body.includes(s));
  R.body_symbol_hits = hits;
  R.body_hits_explained = hits.map((s) => ({
    symbol: s, in_observe_list: observeSyms.has(s),
    note: observeSyms.has(s)
      ? '出现在「观察区」（来源 /api/v1/signals/observe，与本次研究信号无关）'
      : '出现在页面但不在观察区列表内，需人工确认来源',
  }));
  R.observe_symbols_n = observeSyms.size;
  R.has_hist_word = body.includes('历史研究');
  R.body_head = body.replace(/\s+/g, ' ').slice(0, 600);

  R.signals_responses = sigResp;
  R.console_errors = consoleErrs.slice(0, 8);

  await page.screenshot({ path: OUT_PNG, fullPage: true });
  R.screenshot = OUT_PNG;
  R.finished_utc = new Date().toISOString();
  console.log('@@PAGE@@' + JSON.stringify(R));
} finally {
  await browser.close();
}
