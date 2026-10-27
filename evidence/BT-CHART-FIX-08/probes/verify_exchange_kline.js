const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';
const UI = 'http://127.0.0.1:3000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  console.log(`1. 打开回测工作台页面: ${UI}/?nav=backtest&run=${TARGET_RUN}...`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  
  // 等待图表 handle 就绪
  await page.waitForFunction(() => !!window.__labChartHandle && !!window.__labChartBars && window.__labChartBars.length > 0, { timeout: 15000 });
  await page.waitForTimeout(1500);

  // 展开订单明细面板（如果有折叠）
  const ordersToggle = page.locator('[data-testid="lab-panel-orders"]');
  if (await ordersToggle.count() > 0) {
    const isExpanded = await ordersToggle.getAttribute('aria-expanded');
    if (isExpanded !== 'true') {
      await ordersToggle.click();
      await page.waitForTimeout(800);
    }
  }

  // 读取图表首屏落位指标
  const initialMetrics = await page.evaluate(() => {
    const handle = window.__labChartHandle;
    const bars = window.__labChartBars;
    const tf = window.__labChartTf;
    if (!handle || !bars) return { ok: false, error: 'no handle or bars' };
    const range = handle.visibleRange();
    if (!range) return { ok: false, error: 'no range' };

    // 统计在 range.fromMs ~ range.toMs 范围内的 bar 根数
    const visibleBars = bars.filter(b => b.timestamp >= range.fromMs && b.timestamp <= range.toMs).length;
    const canvas = document.querySelector('[data-lab-section="lab-chart"] canvas');
    const width = canvas ? canvas.getBoundingClientRect().width : 800;
    const estimatedBarSpacing = visibleBars > 0 ? (width / visibleBars) : 0;

    return {
      ok: true,
      timeframe: tf,
      totalBars: bars.length,
      visibleBars,
      rangeFrom: new Date(range.fromMs).toISOString(),
      rangeTo: new Date(range.toMs).toISOString(),
      canvasWidth: Math.round(width),
      estimatedBarSpacing: Number(estimatedBarSpacing.toFixed(2)),
    };
  });

  console.log('首屏交易所级 K 线落位指标:', JSON.stringify(initialMetrics, null, 2));

  const screenshotDir = path.resolve('evidence/BT-CHART-FIX-08/screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });

  // 截取首屏主图及资金图区域
  const chartSection = page.locator('[data-lab-section="lab-chart"]');
  if (await chartSection.count() > 0) {
    await chartSection.screenshot({ path: path.join(screenshotDir, 'kline_exchange_style_full.png') });
    console.log('首屏 K 线截图已保存至 kline_exchange_style_full.png');
  }

  // 点击订单聚焦
  console.log('2. 点击具体订单行进行聚焦验证...');
  const firstOrderRow = page.locator('[data-order-id]').first();
  let focusMetrics = null;
  if (await firstOrderRow.count() > 0) {
    await firstOrderRow.click();
    await page.waitForTimeout(1200);

    focusMetrics = await page.evaluate(() => {
      const handle = window.__labChartHandle;
      const bars = window.__labChartBars;
      if (!handle || !bars) return null;
      const range = handle.visibleRange();
      if (!range) return null;
      const visibleBars = bars.filter(b => b.timestamp >= range.fromMs && b.timestamp <= range.toMs).length;
      const canvas = document.querySelector('[data-lab-section="lab-chart"] canvas');
      const width = canvas ? canvas.getBoundingClientRect().width : 800;
      const estimatedBarSpacing = visibleBars > 0 ? (width / visibleBars) : 0;
      return {
        visibleBars,
        rangeFrom: new Date(range.fromMs).toISOString(),
        rangeTo: new Date(range.toMs).toISOString(),
        canvasWidth: Math.round(width),
        estimatedBarSpacing: Number(estimatedBarSpacing.toFixed(2)),
      };
    });
    console.log('订单聚焦指标:', JSON.stringify(focusMetrics, null, 2));

    if (await chartSection.count() > 0) {
      await chartSection.screenshot({ path: path.join(screenshotDir, 'kline_exchange_style_focused.png') });
      console.log('订单聚焦截图已保存至 kline_exchange_style_focused.png');
    }
  }

  // 检查设计令牌入场与平仓颜色
  const paletteCheck = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return {
      entryToken: style.getPropertyValue('--ds-chart-entry').trim(),
      exitToken: style.getPropertyValue('--ds-chart-exit').trim(),
    };
  });
  console.log('入场与出场设计令牌值:', paletteCheck);

  // 保存落盘 JSON 数据
  const outPath = path.resolve('evidence/BT-CHART-FIX-08/out/kline_exchange_metrics.json');
  fs.writeFileSync(outPath, JSON.stringify({
    timestamp: new Date().toISOString(),
    runId: TARGET_RUN,
    initialMetrics,
    focusMetrics,
    paletteCheck,
  }, null, 2));

  console.log('验证脚本全部完成！产物已落盘。');
  await browser.close();
})();
