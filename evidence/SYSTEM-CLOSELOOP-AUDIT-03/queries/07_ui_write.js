/**
 * 07_ui_write.js —— 隔离实例（3001 → 8799）**核心操作**浏览器实测
 *
 * 生产实例只读；这里所有点击都落在 /tmp 隔离账本上（vite 用 API_PROXY_TARGET 指到 8799），
 * 生产 8787 与 数据/paper 一个字节都不碰（由 07_ui_isolated.py 前后 SHA 复核）。
 *
 * 实测顺序（顺序不能换：止损轨迹必须在持仓还开着时点）：
 *   ① 面板基线（开仓数、账本止损价）
 *   ② 点「止损轨迹」→ 记请求与状态码、记点击后真实文案
 *   ③ 点「平仓」→ 记请求、记面板变化、记订单/成交结果
 *   ④ 点闸门按钮（运行中/已暂停）→ 记请求与按钮文案变化
 *   ⑤ 点「按选中信号开多」→ 记是否可用、请求与返回
 * 每步截图并算 SHA-256（相邻步骤必须不同，否则等于没测）。
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
    } catch (e) { /* 试下一个 */ }
  }
  return { ok: false, candidates: n };
}

async function panelState(page) {
  return page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const rows = Array.from(document.querySelectorAll('[data-testid="paper-position-row"]'));
    const btn = (label) => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => txt(x) === label);
      return b ? { found: true, disabled: b.disabled, title: b.title } : { found: false };
    };
    const gate = Array.from(document.querySelectorAll('button'))
      .filter((x) => ['运行中', '已暂停'].includes(txt(x)));
    return {
      connected: q('[data-testid="paper-panel"]')?.getAttribute('data-connected') ?? null,
      positionRows: rows.length,
      rowTexts: rows.map((r) => txt(r).slice(0, 500)),
      pending: txt(q('[data-testid="paper-pending-block"]')),
      gateBtn: gate.length ? { text: txt(gate[0]), pressed: gate[0].getAttribute('aria-pressed') } : null,
      openLong: btn('按选中信号开多'),
      openShort: btn('按选中信号开空'),
      closeBtns: Array.from(document.querySelectorAll('button')).filter((x) => txt(x) === '平仓').length,
      trailLinks: Array.from(document.querySelectorAll('button'))
        .filter((x) => ['止损轨迹', '收起轨迹'].includes(txt(x))).map(txt),
      notice: txt(q('[data-testid="paper-panel"]')?.querySelector('.text-amber-200, .text-amber-300') ?? null),
      bodyTail: (document.body.innerText || '').replace(/\s+/g, ' ').slice(-1200),
    };
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();

  let api = [];
  let consoleErrors = [];
  const failed = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('requestfailed', (r) => failed.push(`${r.method()} ${r.url()} :: ${r.failure()?.errorText}`));
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/')) {
      api.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status(),
                 method: r.request().method() });
    }
  });

  const res = { base: BASE, generated_at: new Date().toISOString(), steps: {} };

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(6000);
  await clickText(page, '交易工作台');
  await sleep(2500);

  // ① 基线
  const s1 = path.join(SHOTS, '07_1_baseline.png');
  await page.screenshot({ path: s1 });
  res.steps.baseline = { state: await panelState(page), shot: s1, sha: sha(s1),
                         consoleErrors: consoleErrors.slice() };

  // ② 止损轨迹
  api = []; consoleErrors = [];
  const t2 = await clickText(page, '止损轨迹');
  await sleep(2500);
  const s2 = path.join(SHOTS, '07_2_stoptrail.png');
  await page.screenshot({ path: s2 });
  res.steps.stopTrail = {
    click: t2,
    requests: api.filter((c) => c.url.includes('stops') || c.url.includes('positions')),
    trailBoxText: await page.evaluate(() => {
      const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
      const head = Array.from(document.querySelectorAll('div'))
        .find((d) => txt(d) === 'FT-LOW4H 止损上移轨迹');
      return head ? txt(head.parentElement).slice(0, 800) : null;
    }),
    state: await panelState(page), shot: s2, sha: sha(s2),
    consoleErrors: consoleErrors.slice(),
  };

  // ③ 平仓
  api = []; consoleErrors = [];
  const t3 = await clickText(page, '平仓');
  await sleep(4000);
  const s3 = path.join(SHOTS, '07_3_afterclose.png');
  await page.screenshot({ path: s3 });
  res.steps.close = {
    click: t3,
    requests: api.slice(),
    state: await panelState(page), shot: s3, sha: sha(s3),
    consoleErrors: consoleErrors.slice(),
  };

  // ④ 闸门
  api = []; consoleErrors = [];
  let gateLabel = null;
  {
    const st = await panelState(page);
    gateLabel = st.gateBtn ? st.gateBtn.text : null;
  }
  const t4 = gateLabel ? await clickText(page, gateLabel) : { ok: false, candidates: 0 };
  await sleep(3500);
  const s4 = path.join(SHOTS, '07_4_gate.png');
  await page.screenshot({ path: s4 });
  res.steps.gate = {
    clickedLabel: gateLabel, click: t4, requests: api.slice(),
    state: await panelState(page), shot: s4, sha: sha(s4),
    consoleErrors: consoleErrors.slice(),
  };

  // ⑤ 按选中信号开多
  api = []; consoleErrors = [];
  const before5 = await panelState(page);
  const t5 = await clickText(page, '按选中信号开多');
  await sleep(4000);
  const s5 = path.join(SHOTS, '07_5_openlong.png');
  await page.screenshot({ path: s5 });
  res.steps.openLong = {
    before: { disabled: before5.openLong?.disabled, title: before5.openLong?.title },
    click: t5, requests: api.slice(),
    state: await panelState(page), shot: s5, sha: sha(s5),
    consoleErrors: consoleErrors.slice(),
  };

  // 收尾：全页面截图哈希是否两两不同
  const shas = Object.entries(res.steps).map(([k, v]) => [k, v.sha]);
  const dup = {};
  shas.forEach(([k, s]) => { (dup[s] = dup[s] || []).push(k); });
  res.dupShots = Object.entries(dup).filter(([, v]) => v.length > 1);
  res.failedReq = failed.slice();

  fs.writeFileSync(path.join(OUT, '07_ui_rw.json'), JSON.stringify(res, null, 1), 'utf8');
  console.log(JSON.stringify({
    baselineRows: res.steps.baseline.state.positionRows,
    baselineGate: res.steps.baseline.state.gateBtn,
    stopTrailRequests: res.steps.stopTrail.requests,
    stopTrailBox: res.steps.stopTrail.trailBoxText,
    closeRequests: res.steps.close.requests,
    rowsAfterClose: res.steps.close.state.positionRows,
    gateAfter: res.steps.gate.state.gateBtn,
    openLongDisabled: res.steps.openLong.before.disabled,
    openLongRequests: res.steps.openLong.requests,
    dupShots: res.dupShots,
  }, null, 1));
  await browser.close();
})();
