/**
 * 05_ui_stops.js —— 止损轨迹：页面与账本一致 + 主动制造接口失败时页面如实报错
 *
 * 三个阶段（同一浏览器上下文，每个阶段先 reload 以清掉组件内 stopsHistory 缓存）：
 *   ① 正常：点「止损轨迹」，记请求状态码 + 轨迹盒真实文案 + 截图
 *   ② 接口 500：拦截 /api/v1/paper/stops/** 返回 500 + missing_reason，看页面是否报错
 *   ③ 网络中断：拦截后 abort，看页面是否报错（不是「暂无上移记录」）
 *
 * 只读：不点平仓/开仓，不写任何账本。隔离实例 3001 → 8799。
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const SHOTS = path.join(ROOT, 'shots');
const BASE = process.env.AUDIT_BASE || 'http://127.0.0.1:3001/';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha = (p) => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

async function clickText(page, text) {
  const loc = page.getByText(text, { exact: true });
  const n = await loc.count();
  for (let i = 0; i < Math.min(n, 8); i++) {
    const el = loc.nth(i);
    try {
      if (!(await el.isVisible())) continue;
      await el.click({ timeout: 5000 });
      return { ok: true, index: i, candidates: n };
    } catch (e) { /* next */ }
  }
  return { ok: false, candidates: n };
}

async function panelState(page) {
  return page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const rows = Array.from(document.querySelectorAll('[data-testid="paper-position-row"]'));
    return {
      connected: q('[data-testid="paper-panel"]')?.getAttribute('data-connected') ?? null,
      positionRows: rows.length,
      rowTexts: rows.map((r) => txt(r).slice(0, 600)),
    };
  });
}

async function trailBox(page) {
  return page.evaluate(() => {
    const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const head = Array.from(document.querySelectorAll('div'))
      .find((d) => txt(d) === 'FT-LOW4H 止损上移轨迹');
    if (!head) return null;
    const box = head.parentElement;
    const entries = Array.from(box.querySelectorAll('div > div > div'))
      .map((d) => txt(d)).filter(Boolean);
    return { boxText: txt(box).slice(0, 1200), entries: entries.slice(0, 12) };
  });
}

async function gotoPanel(page) {
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(5000);
  await clickText(page, '交易工作台');
  await sleep(2500);
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
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/')) api.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status() });
  });

  const res = { base: BASE, generated_at: new Date().toISOString(), steps: {} };

  // ① 正常
  await gotoPanel(page);
  const s1 = path.join(SHOTS, '05_1_baseline.png');
  await page.screenshot({ path: s1 });
  res.steps.baseline = { state: await panelState(page), shot: s1, sha: sha(s1) };

  api = []; consoleErrors = [];
  const c1 = await clickText(page, '止损轨迹');
  await sleep(2500);
  const s2 = path.join(SHOTS, '05_2_stoptrail_ok.png');
  await page.screenshot({ path: s2 });
  res.steps.ok = {
    click: c1,
    requests: api.filter((c) => c.url.includes('stops')),
    trail: await trailBox(page),
    state: await panelState(page),
    shot: s2, sha: sha(s2), consoleErrors: consoleErrors.slice(),
  };

  // ② 接口 500
  await page.route('**/api/v1/paper/stops/**', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json',
                    body: JSON.stringify({ missing_reason: '模拟接口失败：止损轨迹查询异常',
                                           quality: { status: 'error' } }) }));
  await gotoPanel(page);
  api = []; consoleErrors = [];
  const c2 = await clickText(page, '止损轨迹');
  await sleep(2500);
  const s3 = path.join(SHOTS, '05_3_stoptrail_500.png');
  await page.screenshot({ path: s3 });
  res.steps.err500 = {
    click: c2,
    requests: api.filter((c) => c.url.includes('stops')),
    trail: await trailBox(page),
    shot: s3, sha: sha(s3), consoleErrors: consoleErrors.slice(),
  };
  await page.unroute('**/api/v1/paper/stops/**');

  // ③ 网络中断
  await page.route('**/api/v1/paper/stops/**', (route) => route.abort('failed'));
  await gotoPanel(page);
  api = []; consoleErrors = [];
  const c3 = await clickText(page, '止损轨迹');
  await sleep(2500);
  const s4 = path.join(SHOTS, '05_4_stoptrail_abort.png');
  await page.screenshot({ path: s4 });
  res.steps.abort = {
    click: c3,
    requests: api.filter((c) => c.url.includes('stops')),
    trail: await trailBox(page),
    shot: s4, sha: sha(s4), consoleErrors: consoleErrors.slice(),
  };
  await page.unroute('**/api/v1/paper/stops/**');

  const shas = Object.entries(res.steps).map(([k, v]) => [k, v.sha]);
  const dup = {};
  shas.forEach(([k, s]) => { (dup[s] = dup[s] || []).push(k); });
  res.dupShots = Object.entries(dup).filter(([, v]) => v.length > 1);

  fs.writeFileSync(path.join(OUT, '05_ui_stops.json'), JSON.stringify(res, null, 1), 'utf8');
  console.log(JSON.stringify({
    rows: res.steps.baseline.state.positionRows,
    okTrail: res.steps.ok.trail,
    okReq: res.steps.ok.requests,
    err500Trail: res.steps.err500.trail,
    abortTrail: res.steps.abort.trail,
    dupShots: res.dupShots,
  }, null, 1));
  await browser.close();
})();
