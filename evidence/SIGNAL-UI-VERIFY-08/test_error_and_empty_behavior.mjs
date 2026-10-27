import pw from '/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'node:fs';
import path from 'node:path';

const OUT_DIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08';
const BASE_URL = 'http://127.0.0.1:3000';

async function runTests() {
  const browser = await chromium.launch({ headless: true });
  const results = {};

  // 测试用例 1: 接口返回 200 带有 missing_reason（如有效下限缺失或信号库未就绪）
  {
    console.log('--- Case 1: 接口返回 200 带 missing_reason ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    await page.route('**/api/v1/signals*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mode: null,
          missing_reason: '有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定',
          quality: { status: 'no_watermark', note: '下限缺失时如实空态' },
          items: [],
          total: 0,
        }),
      });
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const emptyEl = await page.$('[data-testid="signal-empty"]');
    const errorEl = await page.$('[data-testid="signal-error"]');
    const emptyText = emptyEl ? (await emptyEl.innerText()).trim() : null;
    const errorText = errorEl ? (await errorEl.innerText()).trim() : null;

    await page.screenshot({ path: path.join(OUT_DIR, 'case1_missing_reason_response.png') });

    results.case1 = {
      description: '接口 200 返回 missing_reason 时，页面是否能展示错误还是误报暂无信号',
      hasEmptyState: !!emptyEl,
      emptyText,
      hasErrorState: !!errorEl,
      errorText,
      verdict: emptyEl && !errorEl ? 'FAIL: missing_reason 被前端忽略，错误地显示为“暂无实时信号”' : 'PASS',
    };
  }

  // 测试用例 2: 接口返回 500 服务端内部错误
  {
    console.log('--- Case 2: 接口 500 异常 ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    await page.route('**/api/v1/signals*', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          missing_reason: 'Internal Server Error: database disk image is malformed',
          quality: { status: 'error' },
        }),
      });
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const emptyEl = await page.$('[data-testid="signal-empty"]');
    const errorEl = await page.$('[data-testid="signal-error"]');
    const emptyText = emptyEl ? (await emptyEl.innerText()).trim() : null;
    const errorText = errorEl ? (await errorEl.innerText()).trim() : null;

    await page.screenshot({ path: path.join(OUT_DIR, 'case2_server_500_error.png') });

    results.case2 = {
      description: '接口 500 时，页面是否正确渲染错误提示',
      hasEmptyState: !!emptyEl,
      emptyText,
      hasErrorState: !!errorEl,
      errorText,
      verdict: errorEl ? 'PASS' : 'FAIL',
    };
  }

  // 测试用例 3: 接口返回不可执行候选（如被风控拦截 blocked_gate 或未就绪）
  {
    console.log('--- Case 3: 接口返回不可执行候选 ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    const candidateSignal = {
      signal_id: 'sig_cand_001',
      exchange: 'binance',
      market_id: 'BTCUSDT',
      symbol: 'BTCUSDT',
      status: 'waiting_retest',
      direction: 'long',
      current_direction: null,
      actionable: false,
      actionable_reason: 'gate_paused: 新开仓闸门关闭',
      strength: 0.85,
      confirmed_at: '2026-10-25T09:00:00Z',
      event_time: '2026-10-25T09:00:00Z',
      timeframe: '15m',
      origin: 'live',
      executable_state: 'blocked_gate',
      structural_kind: 'spring',
      role: 'candidate',
    };

    await page.route('**/api/v1/signals*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          quality: { status: 'ok', scope: 'current' },
          items: [candidateSignal],
          total: 1,
        }),
      });
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const emptyEl = await page.$('[data-testid="signal-empty"]');
    const cards = await page.$$('[data-testid="signal-row"]');
    const emptyText = emptyEl ? (await emptyEl.innerText()).trim() : null;

    await page.screenshot({ path: path.join(OUT_DIR, 'case3_blocked_candidate_hidden.png') });

    results.case3 = {
      description: '接口返回不可执行/被拒绝候选时，前端是否错误隐藏并展示“暂无实时信号”',
      cardsCount: cards.length,
      hasEmptyState: !!emptyEl,
      emptyText,
      verdict: cards.length === 0 && emptyEl ? 'FAIL: 前端 isCurrentSignal 强制过滤 executableState!==enterable，导致候选/被拒绝信号完全被隐藏，页面误显示为暂无实时信号' : 'PASS',
    };
  }

  // 测试用例 4: 接口返回真实有效候选信号（核对卡片各字段、价格、状态、刷新）
  {
    console.log('--- Case 4: 接口返回有效信号卡片字段核对 ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    const validSignal = {
      signal_id: 'sig_valid_btc_1025',
      exchange: 'binance',
      market_id: 'BTCUSDT',
      symbol: 'BTCUSDT',
      status: 'confirmed',
      direction: 'long',
      current_direction: 'long',
      actionable: true,
      actionable_reason: 'wyckoff_spring_confirmed',
      strength: 0.95,
      confirmed_at: '2026-10-25T09:15:00Z',
      event_time: '2026-10-25T09:15:00Z',
      timeframe: '15m',
      origin: 'live',
      executable_state: 'enterable',
      structural_kind: 'spring',
      role: 'enterable_arm_entry',
      detection_low: 67200.5,
      arm: '3a',
    };

    await page.route('**/api/v1/signals*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          quality: { status: 'ok', scope: 'current' },
          items: [validSignal],
          total: 1,
        }),
      });
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const cards = await page.$$('[data-testid="signal-row"]');
    const cardEl = cards[0];
    let cardText = '';
    let hasSignalId = false;
    let stopLossText = '';
    let targetPriceText = '';
    let triggerPriceText = '';

    if (cardEl) {
      cardText = await cardEl.innerText();
      hasSignalId = cardText.includes('sig_valid_btc_1025');
      // 检查价格行文本
      triggerPriceText = cardText.includes('67,200.5') ? '67,200.5' : '未匹配';
    }

    await page.screenshot({ path: path.join(OUT_DIR, 'case4_valid_signal_card.png') });

    // 验证刷新
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    const cardsAfterReload = await page.$$('[data-testid="signal-row"]');

    await page.screenshot({ path: path.join(OUT_DIR, 'case4_after_reload.png') });

    results.case4 = {
      description: '有效信号卡片字段核验与页面刷新保持',
      cardRendered: cards.length === 1,
      hasSignalId,
      triggerPriceRecognized: triggerPriceText,
      stopLossTargetIssue: '后端未下发 stop_loss_price 与 target_price，前端 adaptSignal 写死 null，导致止损与目标价显示为破折号 —',
      persistsAfterReload: cardsAfterReload.length === 1,
      verdict: cards.length === 1 && cardsAfterReload.length === 1 ? 'PASS（卡片能渲染且刷新保持，但止损/目标价字段映射缺失）' : 'FAIL',
    };
  }

  fs.writeFileSync(
    path.join(OUT_DIR, 'test_cases_summary.json'),
    JSON.stringify(results, null, 2),
    'utf-8'
  );
  console.log('全部测试用例执行完成，详细总结已写入 evidence/SIGNAL-UI-VERIFY-08/test_cases_summary.json');

  await browser.close();
}

runTests().catch((err) => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
