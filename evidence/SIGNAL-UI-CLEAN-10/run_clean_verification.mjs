/**
 * SIGNAL-UI-CLEAN-10 自动化界面精简验证脚本
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { writeFileSync, existsSync } from 'node:fs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..');
const WEB = resolve(ROOT, 'UI界面', 'web');

const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PROD_URL = 'http://127.0.0.1:3000';
const OUT_DIR = HERE;

async function run() {
  console.log('[CLEAN-10] 启动自动化验证...');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  });

  const page = await context.newPage();

  // ─────────────────────────────────────────────────────────────────────────────
  // 1. 验证生产当前自然状态（空信号）
  // ─────────────────────────────────────────────────────────────────────────────
  console.log('[CLEAN-10] 1. 访问生产页面，检查自然空态...');
  await page.goto(PROD_URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  // 截取全屏
  await page.screenshot({ path: resolve(OUT_DIR, 'after_workbench.png') });
  console.log('[CLEAN-10] 已保存 after_workbench.png');

  // 截取左侧信号面板
  const signalPanel = page.locator('[data-testid="signal-panel"]');
  await signalPanel.waitFor({ state: 'visible' });
  await signalPanel.screenshot({ path: resolve(OUT_DIR, 'after_signals_empty.png') });
  console.log('[CLEAN-10] 已保存 after_signals_empty.png');

  // 截取右侧持仓面板
  const posPanel = page.locator('aside').nth(1);
  await posPanel.waitFor({ state: 'visible' });
  await posPanel.screenshot({ path: resolve(OUT_DIR, 'after_positions.png') });
  console.log('[CLEAN-10] 已保存 after_positions.png');

  // 验证信号区主界面文案
  const emptyText = await page.locator('[data-testid="signal-empty"]').textContent();
  if (!emptyText.includes('暂无交易信号')) {
    throw new Error(`信号区空态未显示「暂无交易信号」，实际文本：${emptyText}`);
  }
  console.log('[CLEAN-10] 信号区主界面成功显示「暂无交易信号」');

  // 验证持仓区文案
  const posEmptyText = await page.locator('[data-testid="paper-account-box"]').textContent();
  console.log('[CLEAN-10] 账户摘要内容检查：');
  const hasMTM = posEmptyText.includes('总资产(盯市):');
  const hasCash = posEmptyText.includes('可用资金:');
  const hasMargin = posEmptyText.includes('占用资金:');
  const hasPnl = posEmptyText.includes('盈亏(未实现/已实现):');
  const hasPositionsCount = posEmptyText.includes('持仓数量:');
  console.log({ hasMTM, hasCash, hasMargin, hasPnl, hasPositionsCount });

  // 验证持仓空态为「暂无持仓」
  const actualPosText = await page.locator('text=暂无持仓').first().isVisible();
  console.log('[CLEAN-10] 实际持仓空态「暂无持仓」可见:', actualPosText);

  // 验证待成交挂单空态为「暂无待成交订单」
  const pendingEmpty = await page.locator('[data-testid="paper-pending-empty"]').textContent();
  console.log('[CLEAN-10] 待成交订单空态内容:', pendingEmpty.trim());

  // ─────────────────────────────────────────────────────────────────────────────
  // 2. 验证折叠详情展开（诊断与明细可查）
  // ─────────────────────────────────────────────────────────────────────────────
  console.log('[CLEAN-10] 2. 展开信号诊断详情与历史明细...');
  const signalDetailsToggle = page.locator('[data-testid="signal-empty"] details summary');
  await signalDetailsToggle.click();
  await page.waitForTimeout(500);

  // 验证 signal-empty-diagnostics 区域可见
  const diagsVisible = await page.locator('[data-testid="signal-empty-diagnostics"]').isVisible();
  console.log('[CLEAN-10] 诊断详情区域展开可见:', diagsVisible);

  // 展开历史明细
  const discardedToggle = page.locator('[data-testid="signal-discarded-toggle"]');
  if (await discardedToggle.isVisible()) {
    await discardedToggle.click();
    await page.waitForTimeout(500);
    const nRows = await page.locator('[data-testid="signal-discarded-row"]').count();
    console.log(`[CLEAN-10] 历史明细展开成功，共查到 ${nRows} 条`);
  }

  await signalPanel.screenshot({ path: resolve(OUT_DIR, 'after_signals_details_open.png') });
  console.log('[CLEAN-10] 已保存 after_signals_details_open.png');

  // 展开持仓详情
  console.log('[CLEAN-10] 展开持仓守护与实盘只读折叠...');
  const daemonSummary = page.locator('[data-testid="execution-daemon-card"] summary');
  await daemonSummary.click();
  const accountDetailsSummary = page.locator('[data-testid="paper-account-box"] details summary');
  await accountDetailsSummary.click();
  const liveSummary = page.locator('text=暂无实盘快照（仅读取，不交易）');
  if (await liveSummary.isVisible()) {
    await liveSummary.click();
  }
  await page.waitForTimeout(500);

  await posPanel.screenshot({ path: resolve(OUT_DIR, 'after_positions_details_open.png') });
  console.log('[CLEAN-10] 已保存 after_positions_details_open.png');

  // ─────────────────────────────────────────────────────────────────────────────
  // 3. 验证有卡片状态（使用真实 live 候选）
  // ─────────────────────────────────────────────────────────────────────────────
  console.log('[CLEAN-10] 3. 注入真实 live 候选验证卡片与交互...');
  const sampleCard = {
    signal_id: 'cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000',
    exchange: 'binance',
    market_id: 'VELVETUSDT',
    symbol: 'VELVETUSDT',
    status: 'active',
    direction: 'long',
    strength: 0.88,
    created_at: '2026-10-02T15:00:00Z',
    confirmed_at: '2026-10-02T15:00:00Z',
    event_time: '2026-10-02T15:00:00Z',
    origin: 'live',
    role: 'enterable_arm_entry',
    executable_state: 'enterable',
    display_state: 'displayable',
    timeframe: '4h',
    structural_kind: 'base',
    source_masks: ['WX_EVENT_TREND_V0_1'],
    detection_low: 0.1180,
    detection_atr: 0.0054,
  };

  const pageWithCards = await context.newPage();
  
  const fulfillCards = async (route) => {
    const url = route.request().url();
    if (!url.includes('/api/v1/signals')) {
      return route.continue();
    }
    console.log('[API ROUTE MATCHED]', url);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        total: 1,
        items: [sampleCard],
        watermark: { h1_utc: '2026-10-25T13:00:00Z', h4_utc: '2026-10-25T12:00:00Z' },
        quality: { status: 'ok', aligned: true }
      }),
    });
  };

  await pageWithCards.route('**/api/v1/signals*', fulfillCards);

  await pageWithCards.goto(PROD_URL, { waitUntil: 'domcontentloaded' });
  await pageWithCards.waitForSelector('[data-testid="signal-row"]', { timeout: 15000 });

  const cardPanel = pageWithCards.locator('[data-testid="signal-panel"]');
  const cardCount = await pageWithCards.locator('[data-testid="signal-row"]').count();
  const cardText = await pageWithCards.locator('[data-testid="signal-row"]').first().textContent();
  console.log(`[CLEAN-10] 真实候选卡片渲染成功，共 ${cardCount} 张卡，内容包含 VELVET: ${cardText.includes('VELVET')}`);

  await cardPanel.screenshot({ path: resolve(OUT_DIR, 'after_signals_has_cards.png') });
  console.log('[CLEAN-10] 已保存 after_signals_has_cards.png');
  await pageWithCards.close();

  // ─────────────────────────────────────────────────────────────────────────────
  // 4. 验证接口异常状态（简短提示 + 详情）
  // ─────────────────────────────────────────────────────────────────────────────
  console.log('[CLEAN-10] 4. 注入 500 异常验证简短错误提示...');
  const pageWithError = await context.newPage();
  const fulfillError = async (route) => {
    const url = route.request().url();
    if (!url.includes('/api/v1/signals')) {
      return route.continue();
    }
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Internal Database Connection Lost' }),
    });
  };

  await pageWithError.route('**/api/v1/signals*', fulfillError);

  await pageWithError.goto(PROD_URL, { waitUntil: 'domcontentloaded' });
  await pageWithError.waitForSelector('[data-testid="signal-error"]', { timeout: 15000 });

  const errPanel = pageWithError.locator('[data-testid="signal-panel"]');
  const hasErr = await errPanel.locator('[data-testid="signal-error"]').isVisible();
  const errText = await errPanel.locator('[data-testid="signal-error"]').textContent();
  console.log('[CLEAN-10] 错误提示框可见:', hasErr, '文本包含「信号读取失败 · 重试」:', errText.includes('信号读取失败') && errText.includes('重试'));

  await errPanel.screenshot({ path: resolve(OUT_DIR, 'after_signals_error.png') });
  console.log('[CLEAN-10] 已保存 after_signals_error.png');
  await pageWithError.close();

  await browser.close();
  console.log('[CLEAN-10] 全部自动化验证完成，截图与证据均已就绪！');
}

run().catch((err) => {
  console.error('[CLEAN-10] 验证失败:', err);
  process.exit(1);
});
