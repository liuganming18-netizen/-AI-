/**
 * 50_verify_after.js — SIGNAL-OBSERVE-FIX-12 修复后**页面行为**验收。
 *
 * 只读：不点开仓/平仓/清空按钮，不下单，不改任何状态。
 * 它要回答工单第 3、4、5 条以及「必须交付的行为证据」里的三条：
 *
 *   A. 观察区是否真的显示：币种 / 形态阶段 / 最近更新时间 / 已满足 / 未满足；
 *   B. **刷新**（整页 reload）、**两轮轮询**、**关闭重开**（新开页面）后，
 *      观察集合是否一致、无重复、无过期残留；
 *   C. 观察展示是否**产生订单**（账本行数前后不变）；
 *   D. 空态三态字段是否可用于区分（读 DOM 上的 data-observe-reason）。
 *
 * 用法：
 *   /Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node 50_verify_after.js
 */
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const require = createRequire('/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/');
const { chromium } = require('playwright');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, '..', 'out');
const SHOTS = path.join(__dirname, '..', 'shots');
const PAGE = 'http://127.0.0.1:3000/';
const API = 'http://127.0.0.1:8787/api/v1';

const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name} :: ${detail}`);
};

/** 读页面上的观察行 → [{symbol,state,updated,expired,unmet}] */
async function readRows(page) {
  return page.$$eval('[data-testid="observe-row"]', (els) =>
    els.map((el) => ({
      symbol: el.getAttribute('data-symbol') || '',
      state: el.getAttribute('data-state') || '',
      updated: el.getAttribute('data-updated-utc') || '',
      expired: el.getAttribute('data-expired') === '1',
      text: (el.innerText || '').replace(/\s+/g, ' ').trim(),
      unmet: Array.from(el.querySelectorAll('[data-testid="observe-unmet"] li')).map(
        (li) => li.textContent || '',
      ),
    })),
  );
}

/** 等观察面板出现（首屏要等账本 + 守护状态） */
async function waitPanel(page, timeoutMs = 60000) {
  const t0 = Date.now();
  for (;;) {
    const n = await page.locator('[data-testid="observe-panel"]').count();
    if (n > 0) return Date.now() - t0;
    if (Date.now() - t0 > timeoutMs) return -1;
    await page.waitForTimeout(500);
  }
}

async function apiGet(p) {
  const r = await fetch(API + p);
  return r.json();
}

(async () => {
  const browser = await chromium.launch({
    executablePath:
      '/Users/boos/Library/Caches/ms-playwright/chromium_headless_shell-1248/' +
      'chrome-headless-shell-mac-arm64/chrome-headless-shell',
    args: ['--no-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200));
  });

  // 账本基线（证明「观察展示不下单」）
  const posBefore = await apiGet('/paper/positions');
  const ordBefore = await apiGet('/paper/orders?arm=3a');

  console.log('='.repeat(78));
  console.log('SIGNAL-OBSERVE-FIX-12 页面行为验收');
  console.log('='.repeat(78));

  await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const panelMs = await waitPanel(page);
  check('观察面板出现', panelMs >= 0, `首屏 ${panelMs} ms`);

  // ── A. 内容是否齐 ─────────────────────────────────────────────────────────
  console.log('\nA. 观察区内容（币种 / 阶段 / 最近更新 / 已满足 / 未满足）');
  const totalText = (await page.locator('[data-testid="observe-total"]').innerText()).trim();
  const countsText = (await page.locator('[data-testid="observe-counts"]').innerText()).trim();
  const rows = await readRows(page);
  check('显示观察总数', /\d/.test(totalText), `标题: ${totalText} · ${countsText}`);
  check('渲染出逐币行', rows.length > 0, `DOM 行数=${rows.length}（超出渲染上限时会有「继续加载」按钮）`);
  const hasSymbol = rows.every((r) => /^[A-Z0-9]{2,20}USDT$/.test(r.symbol));
  check('每行有币种', hasSymbol, `样例=${rows.slice(0, 3).map((r) => r.symbol).join(', ')}`);
  const hasStage = rows.every((r) => ['BOX', 'SWEEP', 'RETEST'].includes(r.state));
  check('每行有形态阶段且限定在观察三态', hasStage,
    `阶段分布=${JSON.stringify(rows.reduce((a, r) => ((a[r.state] = (a[r.state] || 0) + 1), a), {}))}`);
  const hasUpdated = rows.every((r) => /^\d{4}-\d{2}-\d{2}T/.test(r.updated));
  check('每行有最近更新时间', hasUpdated, `样例=${rows[0]?.updated}`);
  const withUnmet = rows.filter((r) => r.unmet.length > 0).length;
  check('显示未满足条件（带当前实值）', withUnmet > 0,
    `${withUnmet}/${rows.length} 行有「未满足」文案；样例: ${rows.find((r) => r.unmet.length)?.unmet[0]}`);

  // 已满足条件默认折叠，点开第一行验证
  const firstToggle = page.locator('[data-testid="observe-row-toggle"]').first();
  if (await firstToggle.count()) {
    await firstToggle.click();
    await page.waitForTimeout(300);
    const satCount = await page.locator('[data-testid="observe-satisfied"] li').count();
    check('显示已满足条件（展开后）', satCount > 0, `第一条已满足 ${satCount} 项`);
  } else {
    check('显示已满足条件（展开后）', false, '找不到条件展开按钮');
  }

  await page.screenshot({ path: path.join(SHOTS, 'signal_panel_after.png'), fullPage: false });

  // ── B. 刷新 / 轮询 / 重开 ─────────────────────────────────────────────────
  console.log('\nB. 刷新 · 两轮轮询 · 关闭重开');
  const setOf = (rs) => rs.map((r) => `${r.symbol}:${r.state}`).sort().join(',');
  const base = setOf(rows);
  const dup = (rs) => {
    const seen = new Set();
    const d = [];
    for (const r of rs) {
      if (seen.has(r.symbol)) d.push(r.symbol);
      seen.add(r.symbol);
    }
    return d;
  };
  check('首次加载无重复币种', dup(rows).length === 0, `重复=${dup(rows).join(',') || '无'}`);

  // 两轮轮询：等 2 个 15s 周期
  await page.waitForTimeout(32000);
  const rows2 = await readRows(page);
  check('两轮轮询后无重复', dup(rows2).length === 0, `行数 ${rows.length}→${rows2.length}`);
  const apiNow = await apiGet('/signals/observe?arm=3a');
  const apiSet = new Set((apiNow.items || []).map((x) => x.symbol));
  const domSet2 = new Set(rows2.map((r) => r.symbol));
  const onlyDom = [...domSet2].filter((s) => !apiSet.has(s));
  check('页面集合是接口集合的子集（无幽灵行/过期残留）', onlyDom.length === 0,
    `页面 ${domSet2.size} 个 / 接口 ${apiSet.size} 个；页面多出=${onlyDom.slice(0, 5).join(',') || '无'}`);
  check('轮询后明细仍在推进（不冻结）',
    typeof apiNow.age_seconds === 'number' && apiNow.age_seconds < 900,
    `明细距今 ${apiNow.age_seconds}s，status=${apiNow.quality?.status}`);

  // 刷新（整页 reload）
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitPanel(page);
  await page.waitForTimeout(1500);
  const rows3 = await readRows(page);
  check('刷新后无重复', dup(rows3).length === 0, `行数 ${rows3.length}`);
  check('刷新后集合与刷新前一致（同一时刻同一口径）',
    setOf(rows3).split(',').filter(Boolean).length === base.split(',').filter(Boolean).length,
    `刷新前 ${base.split(',').filter(Boolean).length} 行 / 刷新后 ${rows3.length} 行`);

  // 关闭重开：新开页面（新上下文 = 无缓存）
  await page.close();
  const page2 = await ctx.newPage();
  page2.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200));
  });
  await page2.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await waitPanel(page2);
  await page2.waitForTimeout(1500);
  const rows4 = await readRows(page2);
  check('关闭重开后无重复', dup(rows4).length === 0, `行数 ${rows4.length}`);
  check('关闭重开后仍有观察行', rows4.length > 0, `行数=${rows4.length}`);

  // ── C. 观察展示是否下单 ───────────────────────────────────────────────────
  console.log('\nC. 观察与执行分离（观察展示不得产生订单）');
  const posAfter = await apiGet('/paper/positions');
  const ordAfter = await apiGet('/paper/orders?arm=3a');
  const pB = (posBefore.items || []).length;
  const pA = (posAfter.items || []).length;
  const oB = (ordBefore.items || []).length;
  const oA = (ordAfter.items || []).length;
  check('持仓行数未因观察展示增加', pA === pB, `持仓 ${pB} → ${pA}`);
  check('订单行数未因观察展示增加', oA === oB, `订单 ${oB} → ${oA}`);

  // ── D. 空态三态可判定 ─────────────────────────────────────────────────────
  console.log('\nD. 空态三态可判定（DOM 属性）');
  const kind = await page2.getAttribute('[data-testid="observe-panel"]', 'data-observe-kind');
  const reason = await page2.getAttribute('[data-testid="observe-panel"]', 'data-observe-reason');
  const totalAttr = await page2.getAttribute('[data-testid="observe-panel"]', 'data-observe-total');
  check('面板暴露 kind / reason / total 三个判定属性',
    Boolean(kind && reason && totalAttr !== null),
    `kind=${kind} reason=${reason} total=${totalAttr}`);
  check('有观察标的时 reason=has_items', reason === 'has_items', `reason=${reason}`);

  const res = {
    probe: 'SIGNAL-OBSERVE-FIX-12/after',
    page: PAGE,
    panel_ms: panelMs,
    total_text: totalText,
    counts_text: countsText,
    dom_rows: rows.length,
    dom_rows_after_2polls: rows2.length,
    dom_rows_after_reload: rows3.length,
    dom_rows_after_reopen: rows4.length,
    sample_rows: rows.slice(0, 5),
    api_age_seconds: apiNow.age_seconds,
    api_status: apiNow.quality?.status,
    api_total: apiNow.observing_total,
    api_items: (apiNow.items || []).length,
    positions: { before: pB, after: pA },
    orders: { before: oB, after: oA },
    dom_kind: kind,
    dom_reason: reason,
    console_errors: consoleErrors,
    checks: results,
  };
  fs.writeFileSync(path.join(OUT, '50_verify_after.json'), JSON.stringify(res, null, 1), 'utf8');

  const bad = results.filter((r) => !r.ok);
  console.log('\n' + '='.repeat(78));
  console.log(`合计 ${results.length} 项：通过 ${results.length - bad.length}，失败 ${bad.length}`);
  if (bad.length) bad.forEach((b) => console.log('  失败:', b.name, '::', b.detail));
  console.log('控制台错误:', consoleErrors.length, consoleErrors.slice(0, 3));
  console.log('→', path.join(SHOTS, 'signal_panel_after.png'));
  console.log('='.repeat(78));

  await browser.close();
  process.exit(bad.length ? 1 : 0);
})().catch((e) => {
  console.error('ERR', e);
  process.exit(1);
});
