/**
 * 51_shot_observe_panel.js — 只截「观察区」面板本身（默认态，不做任何交互）。
 *
 * 用途：让验收方一眼看到「币种 / 形态阶段 / 最近更新时间 / 已满足 / 未满足」，
 * 不被整页布局分散注意力。只读，不点按钮。
 */
import { createRequire } from 'module';
import path from 'path';
import { fileURLToPath } from 'url';

const require = createRequire('/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/');
const { chromium } = require('playwright');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(__dirname, '..', 'shots');
const PAGE = 'http://127.0.0.1:3000/';

(async () => {
  const browser = await chromium.launch({
    executablePath:
      '/Users/boos/Library/Caches/ms-playwright/chromium_headless_shell-1248/' +
      'chrome-headless-shell-mac-arm64/chrome-headless-shell',
    args: ['--no-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  const page = await ctx.newPage();
  await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });

  const panel = page.locator('[data-testid="observe-panel"]');
  for (let i = 0; i < 60; i++) {
    if ((await panel.count()) > 0) break;
    await page.waitForTimeout(500);
  }
  // 等明细真正渲染出来（接口首轮可能返回 not_connected）
  for (let i = 0; i < 40; i++) {
    if ((await page.locator('[data-testid="observe-row"]').count()) > 0) break;
    await page.waitForTimeout(500);
  }
  await page.waitForTimeout(600);

  await panel.screenshot({ path: path.join(SHOTS, 'observe_panel_only.png') });
  // 同时截「展开某一行条件」后的样子（已满足 / 待评估 / 箱体参数）
  await page.locator('[data-testid="observe-row-toggle"]').first().click();
  await page.waitForTimeout(300);
  await panel.screenshot({ path: path.join(SHOTS, 'observe_panel_expanded.png') });

  const rows = await page.locator('[data-testid="observe-row"]').count();
  console.log('观察行数:', rows);
  console.log('→', path.join(SHOTS, 'observe_panel_only.png'));
  console.log('→', path.join(SHOTS, 'observe_panel_expanded.png'));
  await browser.close();
})().catch((e) => {
  console.error('ERR', e);
  process.exit(1);
});
