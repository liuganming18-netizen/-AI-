/**
 * 00_discover.js —— 结构勘察（只读，不做验收判定）
 *
 * 目的：在冻结验收清单之前，先把**实际**的页面/菜单/页签/按钮/表格/接口调用枚举出来，
 * 免得清单凭空写、漏页。
 *
 * 输出：out/00_结构勘察.json + shots/00_*.png
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const SHOTS = path.join(ROOT, 'shots');
const BASE = process.env.AUDIT_BASE || 'http://127.0.0.1:3000/';

const NAV = ['workbench', 'datacenter', 'strategy', 'backtest'];

async function snapshotPage(page, key) {
  // 收集结构信息（不看数值对错，只看"有什么"）
  const info = await page.evaluate(() => {
    const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const uniq = (a) => Array.from(new Set(a.filter(Boolean)));
    return {
      title: document.title,
      h1: uniq(Array.from(document.querySelectorAll('h1')).map(txt)),
      h2: uniq(Array.from(document.querySelectorAll('h2')).map(txt)).slice(0, 40),
      h3: uniq(Array.from(document.querySelectorAll('h3')).map(txt)).slice(0, 40),
      buttons: uniq(Array.from(document.querySelectorAll('button')).map((b) => txt(b) || b.getAttribute('aria-label'))).slice(0, 120),
      tabs: uniq(Array.from(document.querySelectorAll('[role=tab],[data-tab],.tab,.ds-tab')).map(txt)).slice(0, 60),
      tables: document.querySelectorAll('table').length,
      testids: uniq(Array.from(document.querySelectorAll('[data-testid]')).map((e) => e.getAttribute('data-testid'))).slice(0, 200),
      navItems: uniq(Array.from(document.querySelectorAll('[data-nav-id], nav button, aside button')).map((b) => txt(b) || b.getAttribute('data-nav-id'))).slice(0, 30),
      bodyLen: (document.body.innerText || '').length,
      bodyHead: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 600),
    };
  });
  await page.screenshot({ path: path.join(SHOTS, `00_${key}.png`), fullPage: false });
  return info;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  const failedReq = [];
  const apiCalls = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('requestfailed', (r) => failedReq.push(`${r.method()} ${r.url()} :: ${r.failure()?.errorText}`));
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes(':8787/api/')) apiCalls.push({ url: u.replace(/^.*:8787/, ''), status: r.status(), method: r.request().method() });
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);

  const result = { base: BASE, generated_at: new Date().toISOString(), pages: {}, consoleErrors, failedReq };

  for (const nav of NAV) {
    // 用导航按钮切换（真实点击，不直接改 state）
    const clicked = await page.evaluate((id) => {
      const el = document.querySelector(`[data-nav-id="${id}"]`) ||
        Array.from(document.querySelectorAll('button')).find((b) => (b.getAttribute('data-nav-id') === id));
      if (el) { el.click(); return true; }
      return false;
    }, nav);
    if (!clicked) {
      // 退路：按文案点
      const labels = { workbench: '交易工作台', datacenter: '数据中心', strategy: '策略管理', backtest: '回测复盘' };
      try { await page.getByText(labels[nav], { exact: true }).first().click({ timeout: 3000 }); } catch (e) { /* 记录下面 */ }
    }
    await page.waitForTimeout(3500);
    result.pages[nav] = { clicked_by_testid: clicked, ...(await snapshotPage(page, nav)) };
  }

  // 深链入口
  await page.goto(BASE + '?run=__probe__', { waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForTimeout(2500);
  result.deeplink_run_param = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 300));

  result.apiCalls = apiCalls;
  fs.writeFileSync(path.join(OUT, '00_结构勘察.json'), JSON.stringify(result, null, 2), 'utf-8');
  await browser.close();
  console.log('pages:', Object.keys(result.pages).join(', '));
  console.log('consoleErrors:', consoleErrors.length, 'failedReq:', failedReq.length, 'apiCalls:', apiCalls.length);
  for (const [k, v] of Object.entries(result.pages)) {
    console.log(`-- ${k}: h2=${v.h2.length} buttons=${v.buttons.length} tables=${v.tables} bodyLen=${v.bodyLen}`);
  }
})();
