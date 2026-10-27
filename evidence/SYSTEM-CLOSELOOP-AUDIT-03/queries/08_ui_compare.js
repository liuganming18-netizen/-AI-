/**
 * 08_ui_compare.js —— 16 个视图的「页面 vs 接口」逐项取证（只读）
 *
 * 与 06 的区别：06 只截图表、证明"渲染出来了"；08 要把**接口返回体**一起抓下来，
 * 好让 Python 侧做「页面行数 == 接口条数」「页面数值 == 接口数值」这类硬比对。
 *
 * 额外两项定点操作（工单 W5 / W9）：
 *   · W5：工作台 K 线面板 8 个周期逐个点击，记每次 /candles 请求与图是否有数据；
 *   · W9：浅/深主题切换，记切换前后关键数值文本是否同口径、有无白屏。
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
const sha = (p) => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

async function clickText(page, text) {
  const loc = page.getByText(text, { exact: true });
  const n = await loc.count();
  for (let i = 0; i < Math.min(n, 8); i++) {
    const el = loc.nth(i);
    try {
      if (!(await el.isVisible())) continue;
      await el.click({ timeout: 4000 });
      return { ok: true, index: i, candidates: n };
    } catch (e) { /* next */ }
  }
  return { ok: false, candidates: n };
}

/** 接口体摘要：只留"有几条"，不把 8MB 塞进证据文件 */
function shape(obj, depth = 0) {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return { __array: obj.length };
  if (typeof obj !== 'object') return obj;
  if (depth >= 3) return { __obj: Object.keys(obj).slice(0, 12) };
  const o = {};
  for (const k of Object.keys(obj).slice(0, 30)) o[k] = shape(obj[k], depth + 1);
  return o;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  let api = [];
  let consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('response', async (r) => {
    const u = r.url();
    if (!u.includes('/api/v1/')) return;
    const rec = { url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status(),
                  method: r.request().method() };
    if (r.request().method() === 'GET' && r.status() === 200) {
      try {
        const j = await r.json();
        rec.shape = shape(j);
      } catch (e) { rec.shape = null; }
    }
    api.push(rec);
  });

  const res = { base: BASE, generated_at: new Date().toISOString(), views: {}, probes: {} };

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(6000);

  const grab = () => page.evaluate(() => {
    const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const body = document.body.innerText || '';
    const rows = Array.from(document.querySelectorAll('table tbody tr'));
    return {
      bodyLen: body.length,
      bodyText: body.replace(/\s+/g, ' ').slice(0, 9000),
      tableCount: document.querySelectorAll('table').length,
      rowCount: rows.length,
      headers: Array.from(document.querySelectorAll('table thead th')).map(t).slice(0, 30),
      rowTexts: rows.slice(0, 5).map((r) => t(r).slice(0, 400)),
      signalRows: document.querySelectorAll('[data-testid="signal-row"]').length,
      posRows: document.querySelectorAll('[data-testid="paper-position-row"]').length,
      topbar: t(document.querySelector('[data-testid="top-status-bar"]')),
    };
  });

  for (const v of VIEWS) {
    api = []; consoleErrors = [];
    await clickText(page, v.nav); await sleep(1200);
    if (v.tab) { await clickText(page, v.tab); await sleep(1200); }
    if (v.mode) { await clickText(page, v.mode); await sleep(1500); }
    await sleep(1000);
    const shot = path.join(SHOTS, `08_${v.id}.png`);
    await page.screenshot({ path: shot });
    const dom = await grab();
    res.views[v.id] = { dom, api: api.slice(), consoleErrors: consoleErrors.slice(),
                        shot, sha: sha(shot) };
    process.stdout.write(`  ${v.id.padEnd(22)} 行${String(dom.rowCount).padStart(5)} ` +
      `接口${String(api.length).padStart(3)} 图 ${sha(shot).slice(0, 10)}\n`);
  }

  // ── W5：K 线 8 周期逐个点击 ────────────────────────────────────────────
  await clickText(page, '交易工作台'); await sleep(3000);
  const tfProbe = await page.evaluate(() => {
    const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    return Array.from(document.querySelectorAll('button,[role=tab]'))
      .map((b) => t(b)).filter((x) => x && /^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|1w)$/.test(x));
  });
  const tf = [];
  for (const label of Array.from(new Set(tfProbe))) {
    api = [];
    const c = await clickText(page, label);
    await sleep(1800);
    const canvas = await page.evaluate(() => {
      const cs = Array.from(document.querySelectorAll('canvas, svg'));
      return cs.map((c) => ({ tag: c.tagName, w: c.clientWidth, h: c.clientHeight }))
        .filter((x) => x.w > 100 && x.h > 60).length;
    });
    tf.push({ label, click: c,
              candleReq: api.filter((x) => x.url.includes('/candles')).length,
              candleStatus: api.filter((x) => x.url.includes('/candles')).map((x) => x.status),
              bigCharts: canvas });
  }
  res.probes.timeframes = tf;
  await page.screenshot({ path: path.join(SHOTS, '08_workbench.tf.png') });

  // ── W9：主题切换 ──────────────────────────────────────────────────────
  const beforeTheme = await grab();
  const themeBtn = await page.evaluate(() => {
    const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const b = Array.from(document.querySelectorAll('button'))
      .filter((x) => /主题|浅色|深色|夜间|日间/.test(t(x) || ''));
    return b.map((x) => ({ text: t(x), title: x.title })).slice(0, 6);
  });
  let themeAfter = null;
  if (themeBtn.length) {
    const c = await clickText(page, themeBtn[0].text);
    await sleep(2000);
    themeAfter = await grab();
    res.probes.theme = { buttons: themeBtn, clicked: c,
                         beforeLen: beforeTheme.bodyLen, afterLen: themeAfter.bodyLen,
                         afterText: themeAfter.bodyText.slice(0, 2500) };
    await page.screenshot({ path: path.join(SHOTS, '08_theme.png') });
  } else {
    res.probes.theme = { buttons: [], note: '页面没有找到主题切换按钮' };
  }

  const shas = Object.entries(res.views).map(([k, v]) => [k, v.sha]);
  const dup = {};
  shas.forEach(([k, s]) => { (dup[s] = dup[s] || []).push(k); });
  res.dupShots = Object.entries(dup).filter(([, v]) => v.length > 1);

  fs.writeFileSync(path.join(OUT, '08_ui_compare.json'), JSON.stringify(res, null, 1), 'utf8');
  console.log(`\n写出 08_ui_compare.json；截图重复组=${res.dupShots.length}；` +
    `K线周期按钮=${JSON.stringify(Array.from(new Set(tfProbe)))}`);
  console.log(`周期点击结果=${JSON.stringify(tf.map((x) => [x.label, x.candleReq, x.bigCharts]))}`);
  console.log(`主题按钮=${JSON.stringify(themeBtn)}`);
  await browser.close();
})();
