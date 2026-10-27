import pw from '/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/playwright/index.js';
const { chromium } = pw;
import path from 'node:path';

async function shot() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  const outDir = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-CLEAN-10';
  await page.screenshot({ path: path.join(outDir, 'before_workbench.png') });

  const sigPanel = await page.$('[data-testid="signal-panel"]');
  if (sigPanel) await sigPanel.screenshot({ path: path.join(outDir, 'before_signals.png') });

  const posPanel = await page.$('.workbench-position-panel, [data-testid="position-panel"]');
  if (posPanel) await posPanel.screenshot({ path: path.join(outDir, 'before_positions.png') });

  await browser.close();
  console.log('改造前截图完成！');
}
shot().catch(console.error);
