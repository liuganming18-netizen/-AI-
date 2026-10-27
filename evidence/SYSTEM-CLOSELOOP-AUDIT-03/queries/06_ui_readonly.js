/**
 * 06_ui_readonly.js —— 生产实例（3000 → 8787）**只读**浏览器实测
 *
 * 只做三件事：① 逐视图截图并算 SHA-256（证伪"同一张图冒充多视图"）；
 * ② 记录每个视图期间的 /api 请求与状态码、控制台错误、失败请求；
 * ③ 对"交易工作台"做定点探针：模拟实盘面板连接状态、持仓行、账本止损价、
 *    「止损轨迹」点击后的真实表现（默认路由表缺 `/paper/stops/{id}` ⇒ 404）。
 *
 * **不点任何写操作按钮**（开多/开空/平仓/闸门）—— 生产账本一个字节都不许动。
 *
 * 用法：NODE_PATH=UI界面/web/node_modules node 06_ui_readonly.js
 * 输出：out/06_ui_ro.json + shots/06_<view>.png
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

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
  const loc = page.getByText(text, { exact: opts.exact !== false });
  const n = await loc.count();
  for (let i = 0; i < Math.min(n, 8); i++) {
    const el = loc.nth(i);
    try {
      if (!(await el.isVisible())) continue;
      await el.click({ timeout: 4000 });
      return { ok: true, index: i, candidates: n };
    } catch (e) { /* 试下一个 */ }
  }
  return { ok: false, candidates: n };
}

function sha256File(p) {
  return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  let consoleErrors = [];
  let failedReq = [];
  let apiCalls = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('requestfailed', (r) => failedReq.push(`${r.method()} ${r.url()} :: ${r.failure()?.errorText}`));
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/')) {
      apiCalls.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status(),
                      method: r.request().method() });
    }
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForTimeout(5000);

  const result = { base: BASE, generated_at: new Date().toISOString(), views: {}, probes: {} };

  for (const v of VIEWS) {
    consoleErrors = []; failedReq = []; apiCalls = [];
    const nav = await clickText(page, v.nav);
    await sleep(1200);
    let tab = null;
    if (v.tab) { tab = await clickText(page, v.tab); await sleep(1200); }
    let mode = null;
    if (v.mode) { mode = await clickText(page, v.mode); await sleep(1500); }
    await sleep(800);

    const shot = path.join(SHOTS, `06_${v.id}.png`);
    await page.screenshot({ path: shot, fullPage: false });

    const info = await page.evaluate(() => {
      const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
      const body = document.body.innerText || '';
      const rows = Array.from(document.querySelectorAll('table tbody tr'));
      return {
        bodyLen: body.length,
        bodyHead: body.replace(/\s+/g, ' ').slice(0, 1500),
        tableCount: document.querySelectorAll('table').length,
        rowCount: rows.length,
        firstRow: rows[0] ? t(rows[0]) : null,
        testids: Array.from(new Set(Array.from(document.querySelectorAll('[data-testid]'))
          .map((e) => e.getAttribute('data-testid')))),
      };
    });

    result.views[v.id] = {
      nav, tab, mode, ...info,
      shot, shot_sha256: sha256File(shot),
      api: apiCalls.slice(),
      consoleErrors: consoleErrors.slice(),
      failedReq: failedReq.slice(),
    };
    process.stdout.write(`  ${v.id.padEnd(22)} 表格 ${info.tableCount} 行 ${info.rowCount}  ` +
      `图 ${sha256File(shot).slice(0, 12)}  接口 ${apiCalls.length}  ` +
      `控制台错误 ${consoleErrors.length}\n`);
  }

  // ── 定点探针：模拟实盘面板 ──────────────────────────────────────────────
  consoleErrors = []; failedReq = []; apiCalls = [];
  await clickText(page, '交易工作台');
  await sleep(2500);

  const panel = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const p = q('[data-testid="paper-panel"]');
    const rows = Array.from(document.querySelectorAll('[data-testid="paper-position-row"]'));
    return {
      panelFound: !!p,
      connected: p ? p.getAttribute('data-connected') : null,
      arms: p ? p.getAttribute('data-arms') : null,
      notConnectedBanner: !!q('[data-testid="paper-not-connected"]'),
      positionRows: rows.length,
      positionRowText: rows.map((r) => txt(r).slice(0, 400)),
      pendingBlock: !!q('[data-testid="paper-pending-block"]'),
      pendingText: txt(q('[data-testid="paper-pending-block"]')),
      openLongBtn: (() => {
        const b = Array.from(document.querySelectorAll('button'))
          .find((x) => txt(x) === '按选中信号开多');
        return b ? { found: true, disabled: b.disabled, title: b.title } : { found: false };
      })(),
      openShortBtn: (() => {
        const b = Array.from(document.querySelectorAll('button'))
          .find((x) => txt(x) === '按选中信号开空');
        return b ? { found: true, disabled: b.disabled, title: b.title } : { found: false };
      })(),
      closeBtns: Array.from(document.querySelectorAll('button'))
        .filter((x) => txt(x) === '平仓')
        .map((b) => ({ disabled: b.disabled, title: b.title })),
      stopTrailLinks: Array.from(document.querySelectorAll('button'))
        .filter((x) => txt(x) === '止损轨迹' || txt(x) === '收起轨迹')
        .map((b) => txt(b)),
      gateBtn: (() => {
        const b = Array.from(document.querySelectorAll('button'))
          .filter((x) => ['运行中', '已暂停'].includes(txt(x)));
        return b.length ? { text: txt(b[0]), pressed: b[0].getAttribute('aria-pressed'),
                            title: b[0].title } : null;
      })(),
    };
  });
  result.probes.panel = panel;

  // 「止损轨迹」点击实测：默认路由表缺 /paper/stops/{id} ⇒ 期望看到 404 被吞掉
  const trail = { clicked: false, requests: [], afterText: null, afterLinkText: null };
  const link = page.getByText('止损轨迹', { exact: true }).first();
  if (await link.count()) {
    apiCalls = [];
    try {
      await link.click({ timeout: 4000 });
      trail.clicked = true;
    } catch (e) { trail.clickError = String(e).slice(0, 200); }
    await sleep(2500);
    trail.requests = apiCalls.filter((c) => c.url.includes('stops'));
    const after = await page.evaluate(() => {
      const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
      const box = Array.from(document.querySelectorAll('div'))
        .find((d) => t(d) === 'FT-LOW4H 止损上移轨迹');
      const wrap = box ? box.parentElement : null;
      const links = Array.from(document.querySelectorAll('button'))
        .filter((b) => ['止损轨迹', '收起轨迹'].includes(t(b))).map(t);
      return { boxText: wrap ? t(wrap).slice(0, 600) : null, links };
    });
    trail.afterText = after.boxText;
    trail.afterLinkText = after.links;
    await page.screenshot({ path: path.join(SHOTS, '06_workbench.stoptrail.png') });
    trail.shot_sha256 = sha256File(path.join(SHOTS, '06_workbench.stoptrail.png'));
  }
  result.probes.stopTrail = trail;
  result.probes.stopTrailConsoleErrors = consoleErrors.slice();

  // 截图像素级去重：所有视图截图 SHA 必须两两不同
  const shas = Object.entries(result.views).map(([k, v]) => [k, v.shot_sha256]);
  const dup = {};
  shas.forEach(([k, s]) => { (dup[s] = dup[s] || []).push(k); });
  result.probes.dupShots = Object.entries(dup).filter(([, v]) => v.length > 1);

  fs.writeFileSync(path.join(OUT, '06_ui_ro.json'),
    JSON.stringify(result, null, 1), 'utf8');
  console.log(`\n写出 ${path.join(OUT, '06_ui_ro.json')}`);
  console.log(`截图重复组：${result.probes.dupShots.length}`);
  console.log(`面板 connected=${panel.connected} 持仓行=${panel.positionRows} ` +
    `NOT_CONNECTED横幅=${panel.notConnectedBanner} 闸门按钮=${JSON.stringify(panel.gateBtn)}`);
  console.log(`止损轨迹：点击=${trail.clicked} 请求=${JSON.stringify(trail.requests)} ` +
    `之后文案=${JSON.stringify(trail.afterText)}`);
  await browser.close();
})();
