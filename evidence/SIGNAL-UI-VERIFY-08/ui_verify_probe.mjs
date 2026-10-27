import pw from '/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'node:fs';
import path from 'node:path';

const OUT_DIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-VERIFY-08';
const BASE_URL = 'http://127.0.0.1:3000';

async function main() {
  if (!fs.existsSync(OUT_DIR)) {
    fs.mkdirSync(OUT_DIR, { recursive: true });
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1680, height: 1050 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  const networkLogs = [];
  page.on('response', async (res) => {
    const url = res.url();
    if (url.includes('/api/v1/signals') || url.includes('/paper/')) {
      try {
        const text = await res.text();
        networkLogs.push({
          url,
          status: res.status(),
          response: text.slice(0, 1000),
        });
      } catch (e) {
        networkLogs.push({ url, status: res.status(), error: e.message });
      }
    }
  });

  const consoleLogs = [];
  page.on('console', (msg) => {
    consoleLogs.push({ type: msg.type(), text: msg.text() });
  });

  console.log('1. 正在访问交易工作台页面...');
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(4000);

  // 截图整个页面
  await page.screenshot({ path: path.join(OUT_DIR, '01_workbench_initial.png') });

  // 检查信号区 DOM
  const signalPanel = await page.$('[data-testid="signal-panel"]');
  const panelExists = !!signalPanel;

  let signalCountAttr = null;
  let signalScopeAttr = null;
  let emptyStateExists = false;
  let emptyStateText = '';
  let errorStateExists = false;
  let errorStateText = '';
  let signalCardsCount = 0;

  if (panelExists) {
    signalCountAttr = await signalPanel.getAttribute('data-signal-count');
    signalScopeAttr = await signalPanel.getAttribute('data-signal-scope');

    const emptyEl = await page.$('[data-testid="signal-empty"]');
    emptyStateExists = !!emptyEl;
    if (emptyEl) {
      emptyStateText = (await emptyEl.innerText()).trim();
    }

    const errorEl = await page.$('[data-testid="signal-error"]');
    errorStateExists = !!errorEl;
    if (errorEl) {
      errorStateText = (await errorEl.innerText()).trim();
    }

    const cards = await page.$$('[data-testid="signal-row"]');
    signalCardsCount = cards.length;

    // 截取信号面板局部图
    await signalPanel.screenshot({ path: path.join(OUT_DIR, '02_signal_panel_snapshot.png') });
  }

  // 检查是否具备历史候选入口或明细入口
  const historicalEntry = await page.$('text=历史候选');
  const rejectedEntry = await page.$('text=被拒绝');

  // 测试筛选器与搜索框存在性
  const searchInput = await page.$('input[placeholder="搜索代币..."]');
  const filterSelect = await page.$('select');

  const report = {
    timestamp: new Date().toISOString(),
    panelExists,
    signalCountAttr,
    signalScopeAttr,
    signalCardsCount,
    emptyStateExists,
    emptyStateText,
    errorStateExists,
    errorStateText,
    hasHistoricalEntry: !!historicalEntry,
    hasRejectedEntry: !!rejectedEntry,
    hasSearchInput: !!searchInput,
    hasFilterSelect: !!filterSelect,
    networkLogs,
    consoleLogs: consoleLogs.slice(-20),
  };

  fs.writeFileSync(
    path.join(OUT_DIR, 'browser_probe_result.json'),
    JSON.stringify(report, null, 2),
    'utf-8'
  );

  console.log('测试完成，结果已写入 evidence/SIGNAL-UI-VERIFY-08/browser_probe_result.json');
  await browser.close();
}

main().catch((err) => {
  console.error('Probe failed:', err);
  process.exit(1);
});
