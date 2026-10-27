/**
 * 20_shot_before.js — 修复前页面实拍：观察区「没有反应」的视觉证据。
 *
 * 只读：不点任何按钮、不下单、不改任何状态；只打开页面、等渲染、截图、抓文本。
 *
 * 用法（在 evidence/SIGNAL-OBSERVE-FIX-12/probes 下）：
 *   /Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node 20_shot_before.js
 */
import { createRequire } from 'module';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// ESM 不认 NODE_PATH ⇒ 用 createRequire 从绝对路径加载 playwright
const require = createRequire('/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/');
const { chromium } = require('playwright');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, '..', 'out');
const SHOTS = path.join(__dirname, '..', 'shots');
const PAGE = 'http://127.0.0.1:3000/';

const tag = process.argv[2] || 'before';

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

  await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // 等信号区出现（页面首屏先拉账本，实测 2~5s）
  const t0 = Date.now();
  let found = false;
  for (let i = 0; i < 60; i++) {
    const n = await page.locator('[data-testid="signal-empty"]').count();
    if (n > 0) { found = true; break; }
    await page.waitForTimeout(500);
  }
  const firstSeenMs = Date.now() - t0;

  // 等诊断数据落地（守护状态接口首次约 13s，实测 4.4s 渲染）
  let diagText = null;
  for (let i = 0; i < 40; i++) {
    const n = await page.locator('[data-testid="signal-empty-diagnostics"]').count();
    if (n > 0) {
      diagText = await page.locator('[data-testid="signal-empty-diagnostics"]').innerText();
      break;
    }
    await page.waitForTimeout(500);
  }

  const emptyText = found
    ? await page.locator('[data-testid="signal-empty"]').innerText()
    : null;

  await page.screenshot({ path: path.join(SHOTS, `signal_panel_${tag}.png`), fullPage: false });

  // 有没有任何「观察」字样的区块 / 逐币列表
  const bodyText = await page.locator('body').innerText();
  const hasObserveWord = /观察/.test(bodyText);
  const observeHits = (bodyText.match(/观察/g) || []).length;
  // 逐币行的判据：页面上同时出现 ≥3 个 "USDT" 且各自成行（列表特征）
  const usdtLines = bodyText.split('\n').filter((l) => /^[A-Z0-9]{2,20}USDT\b/.test(l.trim())).length;

  const res = {
    probe: 'SIGNAL-OBSERVE-FIX-12/shot',
    tag,
    page: PAGE,
    signal_empty_seen: found,
    first_seen_ms: firstSeenMs,
    diagnostics_rendered: diagText !== null,
    diagnostics_text: diagText,
    empty_panel_text: emptyText,
    has_观察_字样: hasObserveWord,
    观察_出现次数: observeHits,
    逐币行数_页面: usdtLines,
    console_errors: consoleErrors,
  };

  fs.writeFileSync(path.join(OUT, `20_shot_${tag}.json`), JSON.stringify(res, null, 1), 'utf8');

  console.log(`信号区出现: ${found}（${firstSeenMs} ms）`);
  console.log(`诊断已渲染: ${diagText !== null}`);
  console.log('--- 空态面板原文 ---');
  console.log(emptyText);
  console.log('--- 统计 ---');
  console.log(`「观察」出现 ${observeHits} 次；逐币行 ${usdtLines} 行；控制台错误 ${consoleErrors.length} 条`);
  console.log(`→ ${path.join(SHOTS, `signal_panel_${tag}.png`)}`);

  await browser.close();
})().catch((e) => {
  console.error('ERR', e);
  process.exit(1);
});
