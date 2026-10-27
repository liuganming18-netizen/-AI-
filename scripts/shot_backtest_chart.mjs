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

  console.log('Navigating to backtest workbench...');
  await page.goto('http://127.0.0.1:3000/?nav=backtest', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(3000);

  await page.screenshot({ path: resolve(ROOT, 'evidence', 'backtest_full.png') });
  console.log('Screenshot saved to evidence/backtest_full.png');

  const chartEl = page.locator('[data-lab-section="lab-chart"]');
  if (await chartEl.isVisible()) {
    await chartEl.screenshot({ path: resolve(ROOT, 'evidence', 'backtest_chart_section.png') });
    console.log('Screenshot saved to evidence/backtest_chart_section.png');
  } else {
    console.log('chartEl not visible!');
  }

  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
