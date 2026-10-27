// 09｜页面取证（Playwright，只读打开页面；不改任何前端代码）
// 用法：
//   node page09.mjs <baseUrl> <outPng> [--open-research] [--limit-rows=8]
// 输出：一行 @@PAGE@@{...}
// 说明：ESM 不认 NODE_PATH，用绝对路径 import 前端工程里已装的 playwright。
const PW = process.env.PW_MODULE
  || '/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/node_modules/playwright/index.mjs';
const { chromium } = await import(PW);

const BASE = process.argv[2] || 'http://127.0.0.1:3000';
const OUT_PNG = process.argv[3] || '/tmp/page09.png';
const OPEN_RESEARCH = process.argv.includes('--open-research');
const LIMIT_ROWS = Number((process.argv.find((a) => a.startsWith('--limit-rows=')) || '--limit-rows=8')
  .split('=')[1]);

const R = { url: BASE, started_utc: new Date().toISOString() };

const browser = await chromium.launch({ headless: true });
try {
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  const sigResp = [];
  page.on('response', async (resp) => {
    const u = resp.url();
    if (!u.includes('/signals')) return;
    const rec = { url: u, status: resp.status() };
    try {
      const j = await resp.json();
      rec.total = j.total ?? null;
      rec.items_n = (j.items || []).length;
      rec.has_more = j.has_more ?? null;
      rec.scope = (j.quality || {}).scope ?? null;
      rec.quality_status = (j.quality || {}).status ?? null;
      rec.excluded_n = (j.quality || {}).excluded_n ?? null;
      rec.top_identity = {
        strategy_version: j.strategy_version ?? null,
        rule_version: j.rule_version ?? null,
        param_hash: j.param_hash ?? null,
      };
      const it = (j.items || [])[0];
      if (it) {
        rec.first_item = {
          signal_id: it.signal_id, origin: it.origin, role: it.role,
          executable_state: it.executable_state, actionable: it.actionable,
          available_at: it.available_at, has_research_key: Object.prototype.hasOwnProperty.call(it, 'research'),
        };
      }
    } catch (e) {
      rec.parse_error = String(e).slice(0, 160);
    }
    sigResp.push(rec);
  });
  const consoleErrs = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => consoleErrs.push('pageerror: ' + String(e).slice(0, 300)));

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });

  let panelFound = true;
  try {
    await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 45000 });
  } catch { panelFound = false; }
  try {
    await page.waitForSelector('[data-testid="signal-row"], [data-testid="signal-empty"], ' +
      '[data-testid="signal-error"], [data-testid="signal-not-connected"]', { timeout: 30000 });
  } catch { /* 如实记录 */ }

  const count = async (sel) => page.locator(sel).count();
  const texts = async (sel, n) => {
    const loc = page.locator(sel);
    const out = [];
    const total = await loc.count();
    for (let i = 0; i < Math.min(total, n); i++) {
      out.push(((await loc.nth(i).innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 400));
    }
    return out;
  };

  R.panel_found = panelFound;
  R.current_rows = await count('[data-testid="signal-row"]');
  R.current_empty = await count('[data-testid="signal-empty"]');
  R.current_error = await count('[data-testid="signal-error"]');
  R.research_section_present = await count('[data-testid="research-signals"]');
  R.research_rows_before_open = await count('[data-testid="research-signal-row"]');

  if (OPEN_RESEARCH && R.research_section_present > 0) {
    try {
      await page.locator('[data-testid="research-signals"] summary').first().click({ timeout: 8000 });
      await page.waitForSelector('[data-testid="research-signal-row"]', { timeout: 30000 });
    } catch (e) { R.open_error = String(e).slice(0, 200); }
    await page.waitForTimeout(1200);
    R.research_rows_after_open = await count('[data-testid="research-signal-row"]');
    R.research_summary_text = (await texts('[data-testid="research-signals"] summary', 1))[0] || '';
    R.research_meta_text = (await texts('[data-testid="research-signals-meta"]', 1))[0] || '';
    // 逐行抓 data-* 属性（用于与 API 逐条对齐，不靠文本猜）
    const rows = page.locator('[data-testid="research-signal-row"]');
    const n = await rows.count();
    const attrs = [];
    for (let i = 0; i < Math.min(n, LIMIT_ROWS); i++) {
      attrs.push(await rows.nth(i).evaluate((el) => ({
        uid: el.getAttribute('data-signal-uid'),
        origin: el.getAttribute('data-origin'),
        role: el.getAttribute('data-role'),
        exec: el.getAttribute('data-executable-state'),
        approved: el.getAttribute('data-approved'),
        run_id: el.getAttribute('data-run-id'),
        event_time: el.getAttribute('data-event-time'),
        confirmed_at: el.getAttribute('data-confirmed-at'),
        available_at: el.getAttribute('data-available-at'),
        avail_status: el.getAttribute('data-available-status'),
        config_hash: el.getAttribute('data-config-hash'),
        text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 900),
      })));
    }
    R.research_rows_sample = attrs;
    R.research_all_uids = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-signal-uid')));
    // 执行类按钮不得存在（研究区里不许有开仓/下单/执行入口）
    R.research_action_buttons = await page.locator(
      '[data-testid="research-signals"] button[data-testid*="order"], '
      + '[data-testid="research-signals"] button[data-testid*="execute"], '
      + '[data-testid="research-signals"] button[data-testid*="open"]').count();
  }

  // 研究行是否混进了当前交易区
  R.current_area_has_research = await page.locator(
    '[data-testid="signal-row"][data-origin="historical_research"]').count();

  const panelText = (await page.locator('[data-testid="signal-panel"]').innerText().catch(() => '')) || '';
  R.panel_text_head = panelText.replace(/\s+/g, ' ').slice(0, 500);

  R.signals_responses = sigResp;
  R.console_errors = consoleErrs.slice(0, 8);

  await page.screenshot({ path: OUT_PNG, fullPage: true });
  R.screenshot = OUT_PNG;
  R.finished_utc = new Date().toISOString();
  console.log('@@PAGE@@' + JSON.stringify(R));
} finally {
  await browser.close();
}
