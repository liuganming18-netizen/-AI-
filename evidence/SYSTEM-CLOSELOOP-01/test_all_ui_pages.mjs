/**
 * SYSTEM-CLOSELOOP-01: 全 UI 页面与链路真实浏览器自动化巡检脚本
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE_URL = 'http://127.0.0.1:3000';
const OUT_DIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-01';
const SHOT_DIR = path.join(OUT_DIR, 'screenshots');
fs.mkdirSync(SHOT_DIR, { recursive: true });

async function run() {
  console.log('🚀 启动 Playwright 无头浏览器进行全 UI 页面巡检...');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1680, height: 1050 },
    deviceScaleFactor: 1
  });
  const page = await context.newPage();

  const consoleErrors = [];
  const networkRequests = [];
  const failedRequests = [];

  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text();
      // 忽略部分第三方已知警告
      consoleErrors.push({ text, location: msg.location() });
      console.log('  ❌ [Console Error]:', text.slice(0, 200));
    }
  });

  page.on('pageerror', error => {
    consoleErrors.push({ text: error.message, stack: error.stack });
    console.log('  💥 [Page Crash/Error]:', error.message);
  });

  page.on('requestfinished', req => {
    const res = req.response();
    const url = req.url();
    if (url.includes('/api/')) {
      const status = res ? res.status() : 0;
      networkRequests.push({ url, method: req.method(), status });
      if (status >= 400) {
        failedRequests.push({ url, method: req.method(), status });
        console.log(`  ⚠️ [HTTP ${status}] ${req.method()} ${url}`);
      }
    }
  });

  page.on('requestfailed', req => {
    const url = req.url();
    if (url.includes('/api/')) {
      failedRequests.push({ url, method: req.method(), error: req.failure()?.errorText });
      console.log(`  ⛔ [Request Failed] ${req.method()} ${url}: ${req.failure()?.errorText}`);
    }
  });

  const pageReports = [];

  // ==========================================
  // 1. 交易工作台 (workbench)
  // ==========================================
  console.log('\n--- 1. 检查交易工作台 (workbench) ---');
  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  await page.screenshot({ path: path.join(SHOT_DIR, '01_workbench_overview.png') });

  // 检查顶栏状态
  const topBarText = await page.locator('.topbar, header, [data-topbar]').innerText().catch(() => '');
  console.log('  顶栏状态文本预览:', topBarText.replace(/\n+/g, ' | ').slice(0, 150));

  // 检查 TokenTable 标的列表渲染
  const tokenRows = page.locator('table tbody tr, [data-token-row]');
  const tokenRowCount = await tokenRows.count();
  console.log(`  TokenTable 行数: ${tokenRowCount}`);

  // 尝试展开第一行图表
  let chartExpanded = false;
  if (tokenRowCount > 0) {
    const firstRow = tokenRows.first();
    await firstRow.click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(SHOT_DIR, '02_workbench_chart_expanded.png') });
    chartExpanded = true;
    console.log('  已展开第一行行情 K 线图表');
  }

  // 检查持仓面板 PositionPanel
  const posPanel = page.locator('[data-position-panel], .position-panel');
  const posCount = await posPanel.count();
  console.log(`  持仓面板元素数: ${posCount}`);
  await page.screenshot({ path: path.join(SHOT_DIR, '03_workbench_position_panel.png') });

  pageReports.push({
    nav: 'workbench',
    title: '交易工作台',
    tokenRowCount,
    chartExpanded,
    screenshot: '01_workbench_overview.png'
  });

  // ==========================================
  // 2. 数据中心 (datacenter)
  // ==========================================
  console.log('\n--- 2. 检查数据中心 (datacenter) ---');
  await page.getByRole('button', { name: '数据中心' }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(SHOT_DIR, '04_datacenter_overview.png') });

  // 尝试点击数据中心内部的各个 Tab
  const dcTabs = ['覆盖矩阵', '补数计划', 'OI明细', '资金费', '聪明钱', '合并表'];
  for (const tname of dcTabs) {
    const tabBtn = page.getByRole('button', { name: tname }).or(page.getByRole('tab', { name: tname }));
    if (await tabBtn.count()) {
      await tabBtn.first().click();
      await page.waitForTimeout(1500);
      const safeName = tname.replace('/', '_');
      await page.screenshot({ path: path.join(SHOT_DIR, `05_datacenter_tab_${safeName}.png`) });
      console.log(`  已测试数据中心 Tab: ${tname}`);
    }
  }

  pageReports.push({
    nav: 'datacenter',
    title: '数据中心',
    screenshot: '04_datacenter_overview.png'
  });

  // ==========================================
  // 3. 策略管理 (strategy)
  // ==========================================
  console.log('\n--- 3. 检查策略管理 (strategy) ---');
  await page.getByRole('button', { name: '策略管理' }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(SHOT_DIR, '06_strategy_management.png') });

  // 检查策略卡片或表格
  const stratCards = page.locator('[data-strategy-card], .strategy-card, table tr');
  const stratCount = await stratCards.count();
  console.log(`  策略元素数: ${stratCount}`);

  pageReports.push({
    nav: 'strategy',
    title: '策略管理',
    stratCount,
    screenshot: '06_strategy_management.png'
  });

  // ==========================================
  // 4. 回测复盘 (backtest)
  // ==========================================
  console.log('\n--- 4. 检查回测复盘 (backtest) ---');
  await page.getByRole('button', { name: '回测复盘' }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(SHOT_DIR, '07_backtest_overview.png') });

  // 检查批次选择
  const batchSelect = page.locator('select, [data-batch-select]');
  let currentBatch = '';
  if (await batchSelect.count()) {
    currentBatch = await batchSelect.first().inputValue().catch(() => '');
    console.log(`  当前选中批次: ${currentBatch}`);
  }

  // 检查 Run 列表
  const runItems = page.locator('[data-run-id]');
  const runCount = await runItems.count();
  console.log(`  Run 列表条目目数: ${runCount}`);

  let runSelected = false;
  if (runCount > 0) {
    const firstRun = runItems.first();
    const runId = await firstRun.getAttribute('data-run-id') || 'unknown';
    console.log(`  点击第一个 Run: ${runId}`);
    await firstRun.click();
    await page.waitForTimeout(3000);
    await page.screenshot({ path: path.join(SHOT_DIR, '08_backtest_run_selected.png') });
    runSelected = true;

    // 检查逐笔交易表 TradeTable
    const tradeRows = page.locator('[data-trade-row], table tbody tr');
    const tradeRowCount = await tradeRows.count();
    console.log(`  TradeTable 交易条目数: ${tradeRowCount}`);

    // 点击第一笔交易查看 K 线标记
    if (tradeRowCount > 0) {
      await tradeRows.first().click();
      await page.waitForTimeout(2500);
      await page.screenshot({ path: path.join(SHOT_DIR, '09_backtest_kline_marked.png') });
      console.log('  已点击首笔交易并截取 K 线标记图');
    }
  }

  pageReports.push({
    nav: 'backtest',
    title: '回测复盘',
    runCount,
    runSelected,
    screenshot: '07_backtest_overview.png'
  });

  await browser.close();

  const auditResult = {
    timestamp: new Date().toISOString(),
    pages: pageReports,
    consoleErrors,
    networkRequestsCount: networkRequests.length,
    failedRequests
  };

  fs.writeFileSync(
    path.join(OUT_DIR, '01_ui_scan_result.json'),
    JSON.stringify(auditResult, null, 2),
    'utf-8'
  );

  console.log('\n✅ 全 UI 自动化巡检完成！诊断报告落盘: 01_ui_scan_result.json');
}

run().catch(err => {
  console.error('巡检脚本异常失败:', err);
  process.exit(1);
});
