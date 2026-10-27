/**
 * 11_bt_ui.js —— 回测复盘 4 个视图的分表取证（B1~B7 回归用）
 *
 * 为什么单独写：08 只记了整页 tbody 行数（5395 行），没法把"批次表 17 行"
 * 和"run 表 1065 行"分开，导致 B1/B3 无法判等。这里按**表**抓：
 * 每张表的表头 + 行数 + 首行文本，并记下该视图期间每个 /backtests/* 接口的 total。
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const SHOTS = path.join(ROOT, 'shots');
const BASE = process.env.AUDIT_BASE || 'http://127.0.0.1:3000/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 1180 } });
  const page = await ctx.newPage();
  let api = [];
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1/backtests')) {
      api.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status(),
                 method: r.request().method() });
    }
  });

  const snap = () => page.evaluate(() => {
    const t = (el) => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
    const tables = Array.from(document.querySelectorAll('table')).map((tb, i) => ({
      i,
      headers: Array.from(tb.querySelectorAll('thead th')).map(t).slice(0, 14),
      rows: tb.querySelectorAll('tbody tr').length,
      firstRow: t(tb.querySelector('tbody tr')),
    }));
    const body = (document.body.innerText || '').replace(/\s+/g, ' ');
    return { tables, bodyLen: body.length, bodyText: body.slice(0, 6000) };
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(6000);
  const res = { base: BASE, at: new Date().toISOString(), steps: {} };

  await clickText(page, '回测复盘'); await sleep(2000);
  await clickText(page, '历史回测（当前页）'); await sleep(1500);
  api = [];
  await clickText(page, '按批次'); await sleep(3000);
  await page.screenshot({ path: path.join(SHOTS, '11_bt_batch.png') });
  res.steps.batch = { dom: await snap(), api: api.slice() };

  // 点第一个批次 → 批次汇总 + run 列表
  api = [];
  const clickedBatch = await page.evaluate(() => {
    const tr = document.querySelector('table tbody tr');
    if (!tr) return null;
    const a = tr.querySelector('a,button');
    if (a) { a.click(); return 'a'; }
    tr.click(); return 'tr';
  });
  await sleep(4000);
  await page.screenshot({ path: path.join(SHOTS, '11_bt_batch_detail.png') });
  res.steps.batchDetail = { clickedBatch, dom: await snap(), api: api.slice() };

  await clickText(page, '按单 run'); await sleep(3000);
  api = [];
  const clickedRun = await page.evaluate(() => {
    const tr = document.querySelector('table tbody tr');
    if (!tr) return null;
    const a = tr.querySelector('a,button');
    if (a) { a.click(); return 'a'; }
    tr.click(); return 'tr';
  });
  await sleep(5000);
  await page.screenshot({ path: path.join(SHOTS, '11_bt_run_detail.png') });
  res.steps.runDetail = { clickedRun, dom: await snap(), api: api.slice() };

  await clickText(page, '验收复盘'); await sleep(2500);
  api = [];
  await clickText(page, '按批次'); await sleep(3000);
  await page.screenshot({ path: path.join(SHOTS, '11_bt_review_batch.png') });
  res.steps.reviewBatch = { dom: await snap(), api: api.slice() };

  await clickText(page, '按单 run'); await sleep(3000);
  await page.screenshot({ path: path.join(SHOTS, '11_bt_review_run.png') });
  res.steps.reviewRun = { dom: await snap(), api: api.slice() };

  fs.writeFileSync(path.join(OUT, '11_bt_ui.json'), JSON.stringify(res, null, 1), 'utf8');
  for (const [k, v] of Object.entries(res.steps)) {
    console.log(`  ${k.padEnd(12)} 表 ${v.dom.tables.length} 张：` +
      v.dom.tables.map((x) => `${x.rows}行[${(x.headers[0] || '').slice(0, 8)}]`).join(' / ') +
      `  接口 ${v.api.length}`);
  }
  await browser.close();
})();
