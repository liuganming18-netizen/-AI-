/**
 * UI-PENDING-01 · 三态截图（playwright）
 *
 * 对象：`tests_pending/harness.html` 里的**真实组件** PositionPanel，
 *       入参是 TEST FIXTURE；页面顶部常驻红色夹具横幅，截图不会被误读成真实账本。
 *
 * 同时把「画面里能看到什么」结构化落盘（data-state / 行数 / 每行文本 / 是否残留旧行），
 * 避免「截图是唯一证据」——断言由 `pending_states.test.tsx` 负责，这里只做交叉核对。
 *
 * 用法：node tests_pending/shots.mjs <输出目录>
 * 前置：vite dev 已在 3000 端口（npm run dev）
 */

import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const OUT = path.resolve(process.argv[2] || './_shots');
const BASE = process.env.HARNESS_BASE || 'http://127.0.0.1:3000/tests_pending/harness.html';

const STATES = [
  ['has_pending', '01_has_pending'],
  ['empty', '02_empty'],
  ['unreadable', '03_unreadable'],
  ['not_connected', '04_not_connected'],
];

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 520, height: 1200 }, deviceScaleFactor: 2 });

const facts = [];
for (const [state, name] of STATES) {
  await page.goto(`${BASE}?state=${state}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="fixture-banner"]', { timeout: 15000 });
  // 等字体/布局稳定
  await page.waitForTimeout(300);

  const block = page.locator('[data-testid="paper-pending-block"]');
  const hasBlock = (await block.count()) > 0;

  if (hasBlock) {
    await block.screenshot({ path: path.join(OUT, `${name}_block.png`) });
  }
  await page.screenshot({ path: path.join(OUT, `${name}_full.png`), fullPage: true });

  const fact = await page.evaluate(() => {
    const b = document.querySelector('[data-testid="paper-pending-block"]');
    const rows = Array.from(document.querySelectorAll('[data-testid="paper-pending-row"]'));
    const panel = document.querySelector('[data-testid="paper-panel"]');
    return {
      harness_state: document.querySelector('[data-harness-state]')?.getAttribute('data-harness-state') ?? null,
      panel_present: !!panel,
      block_present: !!b,
      block_state: b ? b.getAttribute('data-state') : null,
      block_n_pending: b ? b.getAttribute('data-n-pending') : null,
      row_count: rows.length,
      row_ids: rows.map((r) => r.getAttribute('data-pending-id')),
      row_symbols: rows.map((r) => r.getAttribute('data-symbol')),
      row_status: rows.map((r) => r.getAttribute('data-status')),
      block_text: b ? (b.innerText || '').replace(/\s+/g, ' ').trim() : null,
      contains_stale_symbol: document.body.innerText.includes('TESTFIXUSDT'),
      banner_text: document.querySelector('[data-testid="fixture-banner"]')?.innerText ?? null,
    };
  });

  facts.push({ name, state, ...fact, shots: { block: hasBlock ? `${name}_block.png` : null, full: `${name}_full.png` } });
  console.log(`[shot] ${state} → block=${fact.block_present} state=${fact.block_state} rows=${fact.row_count}`);
}

await browser.close();

const payload = {
  ticket: 'UI-PENDING-01',
  kind: 'screenshots',
  target: 'tests_pending/harness.html（真实组件 + TEST FIXTURE 入参）',
  viewport: { width: 520, height: 1200, device_scale_factor: 2 },
  note: '截图只证明「给定夹具输入下的渲染结果」，不构成真实接口联调或真实成交闭环证据',
  generated_at_utc: new Date().toISOString(),
  facts,
};
fs.writeFileSync(path.join(OUT, 'shots_facts.json'), JSON.stringify(payload, null, 2), 'utf8');
console.log(`[shot] 结构化事实已写入 ${path.join(OUT, 'shots_facts.json')}`);
