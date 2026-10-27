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

  page.on('console', msg => console.log('[PAGE LOG]', msg.text()));
  page.on('pageerror', err => console.log('[PAGE ERR]', err));

  await page.goto('http://127.0.0.1:3000/?nav=backtest', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  console.log('Clicking 日线 button...');
  const dayBtn = page.locator('button:has-text("日线")').first();
  await dayBtn.click();
  await page.waitForTimeout(2000);

  const dayInfo = await page.evaluate(() => {
    const bars = window.__labChartBars || [];
    const chart = window.__labChartData || {};
    const tf = window.__labChartTf;
    const errorEl = document.querySelector('[data-lab-section="lab-chart"] .ds-alert--danger, [data-lab-section="lab-chart"] .text-rose-500, [data-testid="lab-chart-empty"]');
    return {
      tf,
      barsCount: bars.length,
      firstBar: bars[0],
      lastBar: bars[bars.length - 1],
      errorText: errorEl ? errorEl.textContent : null,
      chartSectionText: document.querySelector('[data-lab-section="lab-chart"]')?.textContent?.slice(0, 300)
    };
  });

  console.log('Day Info after click:', JSON.stringify(dayInfo, null, 2));

  await page.screenshot({ path: resolve(ROOT, 'evidence', 'backtest_day_click.png') });

  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
