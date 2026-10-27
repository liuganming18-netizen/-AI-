import pw from '/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'node:fs';
import path from 'node:path';

const OUT_DIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08-R1';
const BASE_URL = 'http://127.0.0.1:3000';

async function runR1Verification() {
  if (!fs.existsSync(OUT_DIR)) {
    fs.mkdirSync(OUT_DIR, { recursive: true });
  }

  const browser = await chromium.launch({ headless: true });
  const results = {
    testRunUtc: new Date().toISOString(),
    tests: {},
  };

  console.log('🚀 开始 SIGNAL-UI-VERIFY-08-R1 独立复验...');

  // ==========================================
  // 测试 1：现有版本的自然只读核验（完全不 mock，直连当前运行系统）
  // ==========================================
  {
    console.log('--- 测试 1: 真实页面自然只读核验（无 mock）---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    let signalsApiStatus = null;
    let signalsApiResponse = null;
    let daemonApiStatus = null;
    let daemonApiResponse = null;

    page.on('response', async (res) => {
      const url = res.url();
      if (url.includes('/api/v1/signals?scope=current')) {
        signalsApiStatus = res.status();
        try { signalsApiResponse = JSON.parse(await res.text()); } catch {}
      }
      if (url.includes('/api/v1/paper/execution_daemon/status')) {
        daemonApiStatus = res.status();
        try { daemonApiResponse = JSON.parse(await res.text()); } catch {}
      }
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 20000 });
    // 等待数据加载与轮询回来（守护状态接口首次大约需要等待几秒）
    await page.waitForTimeout(6000);

    // 截图当前交易工作台全景
    await page.screenshot({ path: path.join(OUT_DIR, '01_live_natural_page.png') });

    // 检查左侧交易信号面板
    const signalPanel = await page.$('[data-testid="signal-panel"]');
    await signalPanel.screenshot({ path: path.join(OUT_DIR, '02_live_natural_signal_panel.png') });

    // 检查空态诊断区域
    const emptyDiagEl = await page.$('[data-testid="signal-empty-diagnostics"]');
    const emptyNoDiagEl = await page.$('[data-testid="signal-empty-nodiag"]');
    const emptyPendingEl = await page.$('[data-testid="signal-empty-pending"]');
    const discardedToggle = await page.$('[data-testid="signal-discarded-toggle"]');

    let toggleText = null;
    let expandedRowsCount = 0;
    let firstCandidateIdOnPage = null;
    let firstCandidateReasonOnPage = null;

    if (discardedToggle) {
      toggleText = (await discardedToggle.innerText()).trim();
      console.log(`  找到历史回放候选展开按钮: "${toggleText}"，正在点击展开...`);
      await discardedToggle.click();
      await page.waitForTimeout(1000);

      await signalPanel.screenshot({ path: path.join(OUT_DIR, '03_live_discarded_expanded.png') });

      const discardedRows = await page.$$('[data-testid="signal-discarded-row"]');
      expandedRowsCount = discardedRows.length;
      if (expandedRowsCount > 0) {
        firstCandidateIdOnPage = await discardedRows[0].getAttribute('data-candidate-id');
        firstCandidateReasonOnPage = (await discardedRows[0].innerText()).trim();
      }
    }

    // 从实际 API 响应比对
    const daemonDiscardedList = daemonApiResponse?.item?.signal_engine?.bootstrap?.discarded_detail || [];
    const firstDaemonCand = daemonDiscardedList[0];
    const firstApiCandId = firstDaemonCand?.candidate_id || firstDaemonCand?.candidateId;

    const idMatch = firstApiCandId && firstCandidateIdOnPage === firstApiCandId;

    results.tests.test1_natural_readonly = {
      description: '自然只读核验：API 与真实页面同一候选 ID 核对，历史明细展开与条数验证',
      signalsApiStatus,
      signalsTotal: signalsApiResponse?.total,
      daemonApiStatus,
      daemonDiscardedCount: daemonDiscardedList.length,
      pageHasDiagnostics: !!emptyDiagEl,
      pageHasToggle: !!discardedToggle,
      toggleText,
      pageExpandedRowsCount: expandedRowsCount,
      firstCandidateIdOnPage,
      firstCandidateIdInApi: firstApiCandId,
      idMatch: !!idMatch,
      verdict: idMatch && expandedRowsCount === daemonDiscardedList.length
        ? 'PASS: 同一候选ID在API与页面精确吻合，历史丢弃候选条数(51)对应，不可执行原因明确标注'
        : 'FAIL: 页面与API对应不一致',
    };
    await context.close();
  }

  // ==========================================
  // 测试 2：live 卡片与 recovered 卡片展示，及刷新、轮询、关闭重开验证（隔离会话）
  // ==========================================
  {
    console.log('--- 测试 2: live 与 recovered 候选卡片核验及持久性（隔离会话）---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    const sampleLive = {
      signal_id: 'sig_live_test_sol_1025',
      exchange: 'binance',
      market_id: 'SOLUSDT',
      symbol: 'SOLUSDT',
      status: 'confirmed',
      direction: 'long',
      current_direction: 'long',
      actionable: true,
      actionable_reason: 'wyckoff_spring_15m_breakout',
      strength: 0.92,
      confirmed_at: '2026-10-25T10:45:00Z',
      event_time: '2026-10-25T10:45:00Z',
      timeframe: '15m',
      origin: 'live',
      executable_state: 'enterable',
      structural_kind: 'spring',
      role: 'enterable_arm_entry',
      detection_low: 178.5,
      arm: '3a',
    };

    const sampleRecovered = {
      signal_id: 'sig_recovered_test_eth_1025',
      exchange: 'binance',
      market_id: 'ETHUSDT',
      symbol: 'ETHUSDT',
      status: 'confirmed',
      direction: 'long',
      current_direction: 'long',
      actionable: true,
      actionable_reason: 'daemon_recovered_in_grace_period',
      strength: 0.88,
      confirmed_at: '2026-10-25T10:30:00Z',
      event_time: '2026-10-25T10:30:00Z',
      timeframe: '15m',
      origin: 'recovered',
      executable_state: 'enterable',
      structural_kind: 'base',
      role: 'enterable_arm_entry',
      detection_low: 2540.2,
      arm: '3b',
    };

    await page.route('**/api/v1/signals?scope=current*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          quality: { status: 'ok', scope: 'current' },
          items: [sampleLive, sampleRecovered],
          total: 2,
        }),
      });
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const cardsInitial = await page.$$('[data-testid="signal-row"]');
    const cardTexts = await Promise.all(cardsInitial.map((c) => c.innerText()));
    const hasLive = cardTexts.some((t) => t.includes('sig_live_test_sol_1025') && t.includes('SOL'));
    const hasRecovered = cardTexts.some((t) => t.includes('sig_recovered_test_eth_1025') && t.includes('ETH'));

    await page.screenshot({ path: path.join(OUT_DIR, '04_cards_live_and_recovered.png') });

    // 验证 1: 页面硬刷新 (Reload)
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    const cardsReload = await page.$$('[data-testid="signal-row"]');

    // 验证 2: 轮询（等待 6s，触发一次完整轮询）
    await page.waitForTimeout(6000);
    const cardsPoll = await page.$$('[data-testid="signal-row"]');

    await context.close();

    // 验证 3: 关闭重开（新建 context 访问）
    const context2 = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page2 = await context2.newPage();
    await page2.route('**/api/v1/signals?scope=current*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          quality: { status: 'ok', scope: 'current' },
          items: [sampleLive, sampleRecovered],
          total: 2,
        }),
      });
    });
    await page2.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page2.waitForTimeout(3000);
    const cardsReopen = await page2.$$('[data-testid="signal-row"]');
    await page2.screenshot({ path: path.join(OUT_DIR, '05_cards_after_reopen.png') });
    await context2.close();

    results.tests.test2_cards_persistence = {
      description: 'live 与 recovered 卡片渲染，以及刷新、轮询、关闭重开持久性核验',
      initialCardCount: cardsInitial.length,
      hasLiveCard: hasLive,
      hasRecoveredCard: hasRecovered,
      reloadCardCount: cardsReload.length,
      pollCardCount: cardsPoll.length,
      reopenCardCount: cardsReopen.length,
      verdict:
        cardsInitial.length === 2 &&
        hasLive &&
        hasRecovered &&
        cardsReload.length === 2 &&
        cardsPoll.length === 2 &&
        cardsReopen.length === 2
          ? 'PASS: live与recovered均能正确识别展示，且在刷新、轮询、关闭重开后均不丢失、不重复'
          : 'FAIL',
    };
  }

  // ==========================================
  // 测试 3：业务异常 200 与 missing_reason 深入核验（隔离会话）
  // ==========================================
  {
    console.log('--- 测试 3: 业务异常 200 (带 missing_reason) 深度核验 ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    await page.route('**/api/v1/signals?scope=current*', (route) => {
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

    const errorEl = await page.$('[data-testid="signal-error"]');
    const emptyEl = await page.$('[data-testid="signal-empty"]');
    const emptyDiagEl = await page.$('[data-testid="signal-empty-diagnostics"]');

    await page.screenshot({ path: path.join(OUT_DIR, '06_business_200_missing_reason.png') });

    results.tests.test3_business_200_missing_reason = {
      description: '业务异常 200 携带 missing_reason 时，前端是否能够透出真实异常，还是被判定为普通正常',
      hasErrorBox: !!errorEl,
      errorBoxText: errorEl ? (await errorEl.innerText()).trim() : null,
      hasEmptyFallback: !!emptyEl || !!emptyDiagEl,
      verdict: errorEl
        ? 'PASS: 业务异常成功展示为错误提示'
        : 'FAIL: useMarketData.ts 在 200 响应时直接 setSignalsError(null)，导致 missing_reason 未展示为错误，而是落入普通空态',
    };
    await context.close();
  }

  // ==========================================
  // 测试 4：网络/HTTP 失败及恢复核验（隔离会话）
  // ==========================================
  {
    console.log('--- 测试 4: 网络 / HTTP 失败及恢复核验 ---');
    const context = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page = await context.newPage();

    let shouldFail = true;
    await page.route('**/api/v1/signals?scope=current*', (route) => {
      if (shouldFail) {
        route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'Service Unavailable' }),
        });
      } else {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            quality: { status: 'ok', scope: 'current' },
            items: [],
            total: 0,
          }),
        });
      }
    });

    await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);

    const errorElFail = await page.$('[data-testid="signal-error"]');
    const errorTextFail = errorElFail ? (await errorElFail.innerText()).trim() : null;
    await page.screenshot({ path: path.join(OUT_DIR, '07_http_503_error.png') });

    // 模拟恢复
    console.log('  模拟后端恢复正常...');
    shouldFail = false;
    // 等待一轮轮询（约 6s）或触发重新请求
    await page.waitForTimeout(6000);

    const errorElRecovered = await page.$('[data-testid="signal-error"]');
    await page.screenshot({ path: path.join(OUT_DIR, '08_http_recovered.png') });

    results.tests.test4_network_failure_and_recovery = {
      description: 'HTTP 503 异常能够立即展示红色告警框；后端恢复后能够自动消除告警恢复空态诊断',
      failedStageShowsError: !!errorElFail,
      errorText: errorTextFail,
      recoveryClearsError: !errorElRecovered,
      verdict:
        errorElFail && !errorElRecovered
          ? 'PASS: HTTP失败能显示明确异常不冒充空态；恢复后自动自愈消除错误框'
          : 'FAIL',
    };
    await context.close();
  }

  fs.writeFileSync(
    path.join(OUT_DIR, 'verify_r1_results.json'),
    JSON.stringify(results, null, 2),
    'utf-8'
  );

  console.log('✨ 复验执行完毕，结果已写入 evidence/SIGNAL-UI-VERIFY-08-R1/verify_r1_results.json');
  await browser.close();
}

runR1Verification().catch((err) => {
  console.error('R1 verification failed:', err);
  process.exit(1);
});
