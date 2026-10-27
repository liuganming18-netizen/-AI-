import { createRequire } from 'node:module';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const getRanges = () => page.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const fChart = window.__labEquityChart;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    const fr = fChart?.timeScale ? fChart.timeScale().getVisibleRange() : null;
    return {
      kRange: kr ? { fromMs: kr.fromMs, toMs: kr.toMs } : null,
      fRange: fr ? { from: fr.from, to: fr.to } : null,
    };
  });

  console.log('--- Initial Ranges ---');
  const init = await getRanges();
  console.log('Init:', JSON.stringify(init));

  // 1. 尝试主图拖拽
  const kCanvas = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvas.scrollIntoViewIfNeeded();
  let kBox = await kCanvas.boundingBox();
  console.log('kBox:', kBox);

  console.log('\n--- Test Main Drag ---');
  const preMainDrag = await getRanges();
  await page.mouse.move(kBox.x + kBox.width * 0.6, kBox.y + kBox.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(kBox.x + kBox.width * 0.35, kBox.y + kBox.height * 0.5, { steps: 15 });
  await page.mouse.up();
  await page.waitForTimeout(1000);
  const postMainDrag = await getRanges();
  console.log('Pre Main Drag :', JSON.stringify(preMainDrag));
  console.log('Post Main Drag:', JSON.stringify(postMainDrag));
  console.log('Main K changed?', preMainDrag.kRange?.fromMs !== postMainDrag.kRange?.fromMs);
  console.log('Main F changed?', preMainDrag.fRange?.from !== postMainDrag.fRange?.from);

  // 2. 尝试资金图拖拽
  const fSection = page.locator('[data-lab-section="lab-funding"]');
  await fSection.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const fCanvas = page.locator('[data-lab-section="lab-funding"] canvas').first();
  let fBox = await fCanvas.boundingBox();
  console.log('\nfBox:', fBox);

  console.log('\n--- Test Funding Drag ---');
  const preFundDrag = await getRanges();
  // 观察 funding canvas 的中心位置
  await page.mouse.move(fBox.x + fBox.width * 0.5, fBox.y + fBox.height * 0.5);
  await page.mouse.down();
  // 拖动 200px
  await page.mouse.move(fBox.x + fBox.width * 0.5 + 200, fBox.y + fBox.height * 0.5, { steps: 15 });
  await page.mouse.up();
  await page.waitForTimeout(1000);
  const postFundDrag = await getRanges();
  console.log('Pre Fund Drag :', JSON.stringify(preFundDrag));
  console.log('Post Fund Drag:', JSON.stringify(postFundDrag));
  console.log('Fund F changed?', preFundDrag.fRange?.from !== postFundDrag.fRange?.from);
  console.log('Fund K changed?', preFundDrag.kRange?.fromMs !== postFundDrag.kRange?.fromMs);

  // 3. 尝试资金图滚轮缩放
  console.log('\n--- Test Funding Wheel Zoom ---');
  const preFundZoom = await getRanges();
  await page.keyboard.down('Control');
  await page.mouse.move(fBox.x + fBox.width * 0.5, fBox.y + fBox.height * 0.5);
  await page.mouse.wheel(0, -600);
  await page.keyboard.up('Control');
  await page.waitForTimeout(1000);
  const postFundZoom = await getRanges();
  console.log('Pre Fund Zoom :', JSON.stringify(preFundZoom));
  console.log('Post Fund Zoom:', JSON.stringify(postFundZoom));
  console.log('Zoom F changed?', preFundZoom.fRange?.from !== postFundZoom.fRange?.from);
  console.log('Zoom K changed?', preFundZoom.kRange?.fromMs !== postFundZoom.kRange?.fromMs);

  // 4. 尝试资金图时间轴拖动缩放
  console.log('\n--- Test Funding TimeScale Drag ---');
  const preFundTimeScale = await getRanges();
  const timeScaleY = fBox.y + fBox.height - 10;
  await page.mouse.move(fBox.x + fBox.width * 0.5, timeScaleY);
  await page.mouse.down();
  await page.mouse.move(fBox.x + fBox.width * 0.35, timeScaleY, { steps: 15 });
  await page.mouse.up();
  await page.waitForTimeout(1000);
  const postFundTimeScale = await getRanges();
  console.log('Pre Fund TimeScale :', JSON.stringify(preFundTimeScale));
  console.log('Post Fund TimeScale:', JSON.stringify(postFundTimeScale));
  console.log('TimeScale F changed?', preFundTimeScale.fRange?.from !== postFundTimeScale.fRange?.from);
  console.log('TimeScale K changed?', preFundTimeScale.kRange?.fromMs !== postFundTimeScale.kRange?.fromMs);

  await browser.close();
})();
