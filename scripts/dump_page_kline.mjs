import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const WEB = resolve(ROOT, 'UI界面', 'web');

const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();

  console.log('Navigating to backtest...');
  await page.goto('http://127.0.0.1:3000/?nav=backtest', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(3000);

  // 获取 window 上的图表数据
  const info = await page.evaluate(() => {
    const bars = window.__labChartBars || [];
    const chart = window.__labChartData || {};
    const tf = window.__labChartTf;
    const run = window.__labCurrentRun;

    return {
      run,
      tf,
      symbol: chart.symbol,
      barsCount: bars.length,
      firstBar: bars[0],
      lastBar: bars[bars.length - 1],
      barsSample: bars.slice(0, 5),
      rawCandlesCount: chart.candles ? chart.candles.length : 0,
      contextNote: chart.candles_context_note,
      candlesSource: chart.candles_source,
      candlesWindowShown: chart.candles_window_shown,
      candlesRunRange: chart.candles_run_range
    };
  });

  console.log('Page Chart Info:', JSON.stringify(info, null, 2));

  // 检查 bars 里的时间差
  const gapReport = await page.evaluate(() => {
    const bars = window.__labChartBars || [];
    const gaps = [];
    for (let i = 1; i < bars.length; i++) {
      const prev = bars[i - 1].timestamp;
      const curr = bars[i].timestamp;
      const diff = curr - prev;
      gaps.push({ index: i, prev, curr, diff, prevTime: bars[i - 1].time, currTime: bars[i].time });
    }
    // 找出不是典型周期的间隔
    const stepCounts = {};
    for (const g of gaps) {
      stepCounts[g.diff] = (stepCounts[g.diff] || 0) + 1;
    }
    const abnormal = gaps.filter(g => g.diff !== 3600000 && g.diff !== 900000 && g.diff !== 86400000);
    return { stepCounts, abnormalCount: abnormal.length, abnormalSample: abnormal.slice(0, 10) };
  });

  console.log('Gap Report:', JSON.stringify(gapReport, null, 2));

  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
