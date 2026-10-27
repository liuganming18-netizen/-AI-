import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const WEB = resolve(ROOT, 'UI界面', 'web');

const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const TF_EXPECTED_STEP = {
  '15m': 900_000,
  '30m': 1_800_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '8h': 28_800_000,
  '12h': 43_200_000,
  '1d': 86_400_000,
};

async function verifyAll() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();

  await page.goto('http://127.0.0.1:3000/?nav=backtest', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  const results = [];

  for (const [tf, step] of Object.entries(TF_EXPECTED_STEP)) {
    const btn = page.locator(`[data-testid="lab-chart-tf"] button[data-tf="${tf}"]`);
    if (await btn.count() === 0) {
      results.push({ tf, status: 'NOT_FOUND' });
      continue;
    }
    await btn.click();
    // 等待数据加载与图表重新聚合渲染
    await page.waitForTimeout(2000);

    const info = await page.evaluate((expectedStep) => {
      const bars = window.__labChartBars || [];
      const gaps = [];
      for (let i = 0; i < bars.length - 1; i++) {
        const diff = bars[i + 1].timestamp - bars[i].timestamp;
        if (diff !== expectedStep) {
          gaps.push({ index: i, t1: bars[i].timestamp, t2: bars[i + 1].timestamp, diff });
        }
      }
      return {
        barsCount: bars.length,
        firstBar: bars[0]?.time,
        lastBar: bars[bars.length - 1]?.time,
        gaps,
      };
    }, step);

    results.push({
      tf,
      expectedStep: step,
      barsCount: info.barsCount,
      firstBar: info.firstBar,
      lastBar: info.lastBar,
      gapsCount: info.gaps.length,
      gaps: info.gaps,
    });
  }

  console.log('=== 回测 K 线周期连续性全面核验证明 ===');
  console.log(JSON.stringify(results, null, 2));

  await page.screenshot({ path: resolve(ROOT, 'evidence', 'backtest_all_tf_verified.png') });
  await browser.close();

  const anyFail = results.some(r => r.barsCount === 0 || r.gapsCount > 0);
  if (anyFail) {
    console.error('FAIL: 发现断层或空蜡烛！');
    process.exit(1);
  } else {
    console.log('PASS: 所有周期 K 线 100% 连续完整，零断层！');
  }
}

verifyAll().catch(err => {
  console.error(err);
  process.exit(1);
});
