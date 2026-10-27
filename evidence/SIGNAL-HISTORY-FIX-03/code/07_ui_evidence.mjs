// SIGNAL-HISTORY-FIX-03｜07 用户入口证据（浏览器实测，只读）
//
// 目的（工单「提供真实 API 与用户入口证据」）：
//   1. 前端确实能打开，且把 **运行中的真实数据** 渲染出来（不是占位符）；
//   2. 屏幕上显示的三块诊断（ATR 口径 / 1h 水位 / 数据质量闸门）与 API 返回逐项相同；
//   3. 明确记录本轮补了什么：`atrCaliber` 之前**声明了但映射从未赋值**，
//      页面上那一行永远不出现（声明 ≠ 接上）；`watermark_check` / `data_quality`
//      之前完全没有用户入口。三块都是**只读展示**，不参与任何决策。
//
// 用法：node 07_ui_evidence.mjs
// 产物：out/07_用户入口.json、out/07_用户入口.png
import path from 'node:path';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, '..', '..', '..', 'UI界面', 'web');
const req = createRequire(path.join(WEB, 'package.json'));
const { chromium } = req('playwright');

const OUT = path.resolve(__dirname, '..', 'out');
const URL = process.env.WX_UI_URL || 'http://127.0.0.1:3000';
const API = 'http://127.0.0.1:8787/api/v1/paper/execution_daemon/status?arm=3a';

const BLOCKS = [
  { id: 'daemon-atr-caliber', key: 'atr_caliber', label: 'ATR 口径' },
  { id: 'daemon-watermark', key: 'watermark_check', label: '1h 水位' },
  { id: 'daemon-data-quality', key: 'data_quality', label: '数据质量闸门' },
];

async function apiItem() {
  const r = await fetch(API, { signal: AbortSignal.timeout(20000) });
  const d = await r.json();
  return d.item || (d.data && d.data.item) || d;
}

function collectNumbers(o, acc) {
  if (o === null || o === undefined) return acc;
  if (typeof o === 'number') { acc.add(String(o)); return acc; }
  if (typeof o === 'string') { (o.match(/-?\d+(?:\.\d+)?/g) || []).forEach((x) => acc.add(x)); return acc; }
  if (Array.isArray(o)) { o.forEach((x) => collectNumbers(x, acc)); return acc; }
  if (typeof o === 'object') Object.values(o).forEach((x) => collectNumbers(x, acc));
  return acc;
}

const res = { generated_at_utc: new Date().toISOString(), url: URL, blocks: [] };
const item = await apiItem();
const se = item.signal_engine || {};
res.api = {
  pid: (item.runtime_identity || {}).pid,
  cycle_count: (item.runtime_identity || {}).cycle_count,
  status: (item.runtime_identity || {}).status,
  universe: se.universe,
  bootstrap_data_source: (se.bootstrap || {}).data_source,
  bootstrap_used: (se.bootstrap || {}).used || null,
  has_atr_caliber: !!se.atr_caliber,
  has_watermark_check: !!se.watermark_check,
  has_data_quality: !!se.data_quality,
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } });
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + String(e).slice(0, 200)));

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForSelector('[data-testid="execution-daemon-card"]', { timeout: 60000 });

for (const b of BLOCKS) {
  const rec = { ...b };
  try {
    await page.waitForSelector(`[data-testid="${b.id}"]`, { timeout: 30000 });
    rec.visible = true;
    rec.text = (await page.$eval(`[data-testid="${b.id}"]`, (e) => e.innerText)).trim();
  } catch (e) {
    rec.visible = false;
    rec.error = String(e).slice(0, 200);
  }
  // parity：屏幕上出现的数字必须能在该块对应的 API 对象里找到
  // 先把**合约名**剔掉：`1000000BOBUSDT` 里的 1000000 会被正则当成数字，
  // 那是我的比对口径错，不是页面显示错。
  const apiNums = collectNumbers(se[b.key], new Set());
  const cleaned = (rec.text || '').replace(/\b[A-Z0-9]{3,}USDT\b/g, ' ');
  const uiNums = cleaned.match(/-?\d+(?:\.\d+)?/g) || [];
  const missing = uiNums.filter((n) => !apiNums.has(n));
  rec.parity = {
    n_ui_numbers: uiNums.length,
    ui_numbers: uiNums.slice(0, 30),
    n_missing_in_api: missing.length,
    missing_sample: missing.slice(0, 10),
    pass: rec.visible && uiNums.length > 0 && missing.length === 0,
  };
  res.blocks.push(rec);
}

// 面板级证据：监控标的数也要与 API 一致（526）
const uniText = await page.$eval('[data-testid="daemon-universe"]', (e) => e.innerText).catch(() => '');
res.universe_block = {
  text: uniText.trim(),
  api_universe: se.universe,
  pass: typeof se.universe === 'number' && uniText.includes(String(se.universe)),
};

const shot = path.join(OUT, '07_用户入口.png');
await page.screenshot({ path: shot, fullPage: true });
res.screenshot = shot;
res.screenshot_bytes = fs.statSync(shot).size;

const card = await page.$('[data-testid="execution-daemon-card"]');
if (card) {
  const shot2 = path.join(OUT, '07_用户入口_守护卡.png');
  await card.screenshot({ path: shot2 });
  res.screenshot_card = shot2;
  res.screenshot_card_bytes = fs.statSync(shot2).size;
}

res.console_errors = consoleErrors.slice(0, 10);
res.n_blocks_pass = res.blocks.filter((b) => b.parity.pass).length;
res.n_blocks = res.blocks.length;
res.ok = res.n_blocks_pass === res.n_blocks && res.universe_block.pass
  && res.console_errors.length === 0;

await browser.close();
fs.writeFileSync(path.join(OUT, '07_用户入口.json'), JSON.stringify(res, null, 1), 'utf-8');
console.log(JSON.stringify({
  ok: res.ok,
  n_blocks_pass: res.n_blocks_pass,
  n_blocks: res.n_blocks,
  universe_block: res.universe_block,
  console_errors: res.console_errors,
  blocks: res.blocks.map((b) => ({
    label: b.label, visible: b.visible, text: (b.text || '').replace(/\s+/g, ' ').slice(0, 120),
    parity: b.parity && { n_ui_numbers: b.parity.n_ui_numbers, missing: b.parity.n_missing_in_api, pass: b.parity.pass },
  })),
  screenshot_bytes: res.screenshot_bytes,
}, null, 1));
