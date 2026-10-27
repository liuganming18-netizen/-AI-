/**
 * 02_ui_capture.js —— 真实浏览器逐视图取证（只采集，不判定）
 *
 * 覆盖冻结清单里的 16 个视图：交易工作台 1 + 数据中心 8 + 策略管理 3 + 回测复盘 4。
 * 每个视图记录：截图、可见文本、表格行数、关键数值文本、以及**该视图期间发生的全部 /api 请求**。
 *
 * 输出：out/02_ui_capture.json + shots/<view>.png
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const SHOTS = path.join(ROOT, 'shots');
const BASE = process.env.AUDIT_BASE || 'http://127.0.0.1:3000/';

const VIEWS = [
  { id: 'workbench.main', nav: '交易工作台', tab: null },
  { id: 'dc.dashboard', nav: '数据中心', tab: '全域数据名称与红绿灯看板主视窗' },
  { id: 'dc.pending', nav: '数据中心', tab: '待补数据与核销清单' },
  { id: 'dc.matrix', nav: '数据中心', tab: '全资产协同矩阵' },
  { id: 'dc.kline', nav: '数据中心', tab: 'K线 (8周期)' },
  { id: 'dc.oi', nav: '数据中心', tab: 'OI' },
  { id: 'dc.funding', nav: '数据中心', tab: '费率' },
  { id: 'dc.smartmoney', nav: '数据中心', tab: '聪明钱' },
  { id: 'dc.metrics', nav: '数据中心', tab: '指标' },
  { id: 'st.rules', nav: '策略管理', tab: '规则说明（当前页）' },
  { id: 'st.deps', nav: '策略管理', tab: '数据依赖' },
  { id: 'st.versions', nav: '策略管理', tab: '版本对比' },
  { id: 'bt.workbench.batch', nav: '回测复盘', tab: '历史回测（当前页）', mode: '按批次' },
  { id: 'bt.workbench.run', nav: '回测复盘', tab: '历史回测（当前页）', mode: '按单 run' },
  { id: 'bt.review.batch', nav: '回测复盘', tab: '验收复盘', mode: '按批次' },
  { id: 'bt.review.run', nav: '回测复盘', tab: '验收复盘', mode: '按单 run' },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function clickText(page, text, opts = {}) {
  const exact = opts.exact !== false;
  const loc = page.getByText(text, { exact });
  const n = await loc.count();
  for (let i = 0; i < Math.min(n, 6); i++) {
    const el = loc.nth(i);
    try {
      if (!(await el.isVisible())) continue;
      await el.click({ timeout: 4000 });
      return { ok: true, index: i, candidates: n };
    } catch (e) { /* 继续试下一个 */ }
  }
  return { ok: false, candidates: n };
}

async function capture(page, view) {
  return page.evaluate((vid) => {
    const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const rows = Array.from(document.querySelectorAll('table tbody tr'));
    const ths = Array.from(document.querySelectorAll('table thead th')).map(t);
    const body = document.body.innerText || '';
    // 抓"数字+单位"片段，供 Python 侧核对具体数值
    const nums = (body.match(/[-+]?[\d,]+\.?\d*\s*(?:%|USDT|U|张|笔|单|根|条|个|秒|分钟|小时)?/g) || [])
      .map((s) => s.trim()).filter((s) => /\d/.test(s)).slice(0, 400);
    return {
      view: vid,
      bodyLen: body.length,
      bodyText: body.replace(/\s+/g, ' ').slice(0, 6000),
      tableCount: document.querySelectorAll('table').length,
      rowCount: rows.length,
      firstRow: rows[0] ? t(rows[0]) : null,
      headers: ths,
      h2: Array.from(document.querySelectorAll('h2')).map(t).filter(Boolean),
      h3: Array.from(document.querySelectorAll('h3')).map(t).filter(Boolean),
      nums,
    };
  }, view.id);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 }, locale: 'zh-CN' });
  const page = await ctx.newPage();

  let bucket = [];
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push({ view: currentView, text: m.text().slice(0, 300) }); });
  page.on('pageerror', (e) => consoleErrors.push({ view: currentView, text: 'PAGEERROR ' + e.message.slice(0, 300) }));
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/')) {
      const p = u.replace(/^https?:\/\/[^/]+/, '');
      bucket.push({ url: p, status: r.status(), method: r.request().method(), view: currentView });
    }
  });
  page.on('requestfailed', (r) => {
    const u = r.url();
    if (u.includes('/api/')) bucket.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: 'FAILED', method: r.method(), view: currentView });
  });

  let currentView = 'boot';
  const result = { base: BASE, generated_at: new Date().toISOString(), views: {}, calls: [], consoleErrors };

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(4500);

  for (const v of VIEWS) {
    currentView = v.id;
    bucket = [];
    const steps = [];
    const r1 = await clickText(page, v.nav);
    steps.push({ step: `nav:${v.nav}`, ...r1 });
    await sleep(2500);
    if (v.tab) {
      const r2 = await clickText(page, v.tab, { exact: false });
      steps.push({ step: `tab:${v.tab}`, ...r2 });
      await sleep(3000);
    }
    if (v.mode) {
      const r3 = await clickText(page, v.mode, { exact: false });
      steps.push({ step: `mode:${v.mode}`, ...r3 });
      await sleep(2500);
    }
    const info = await capture(page, v);
    await page.screenshot({ path: path.join(SHOTS, `${v.id}.png`), fullPage: false });
    result.views[v.id] = { ...info, steps, calls: bucket.slice() };
    result.calls.push(...bucket);
    console.log(`${v.id}: rows=${info.rowCount} tables=${info.tableCount} calls=${bucket.length} bodyLen=${info.bodyLen}`);
  }

  // 深链
  currentView = 'deeplink';
  bucket = [];
  const runId = (result.calls.find((c) => c.url.includes('/api/v1/backtests')) || {}).url || '';
  await page.goto(BASE + '?run=run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT', { waitUntil: 'domcontentloaded' }).catch(() => {});
  await sleep(4000);
  const dl = await capture(page, { id: 'deeplink' });
  await page.screenshot({ path: path.join(SHOTS, 'deeplink.run.png') });
  result.views['deeplink.run'] = { ...dl, calls: bucket.slice(), runIdProbe: runId };
  result.calls.push(...bucket);
  console.log('deeplink.run: rows=' + dl.rowCount + ' calls=' + bucket.length);

  fs.writeFileSync(path.join(OUT, '02_ui_capture.json'), JSON.stringify(result, null, 2), 'utf-8');
  await browser.close();
  console.log('consoleErrors:', consoleErrors.length);
})();
