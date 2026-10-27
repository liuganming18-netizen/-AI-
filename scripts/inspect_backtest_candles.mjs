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

  let chartData = null;
  page.on('response', async (res) => {
    const url = res.url();
    if (url.includes('/chart')) {
      try {
        const json = await res.json();
        chartData = json;
        console.log(`[CHART INTERCEPTED] URL: ${url}`);
      } catch (e) {}
    }
  });

  await page.goto('http://127.0.0.1:3000/?nav=backtest', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(3000);

  if (!chartData) {
    console.log('未拦截到 /chart 数据！尝试检查选中的 run...');
  } else {
    const chart = chartData.chart || chartData;
    const candles = chart.candles || [];
    console.log(`\n=== 原始 chart.candles 概况 ===`);
    console.log(`总根数: ${candles.length}`);
    if (candles.length > 0) {
      const getT = (c) => Array.isArray(c) ? c[0] : (c.t || c.timestamp || c.time);
      const firstT = getT(candles[0]);
      const lastT = getT(candles[candles.length - 1]);
      console.log(`首根: ${firstT} (${new Date(firstT).toISOString()})`);
      console.log(`末根: ${lastT} (${new Date(lastT).toISOString()})`);

      // 检查时间差分布和断层/Gap
      const diffs = [];
      let gaps = [];
      for (let i = 1; i < candles.length; i++) {
        const prevT = getT(candles[i - 1]);
        const currT = getT(candles[i]);
        const diff = currT - prevT;
        if (diff !== 900000) { // 15m = 900,000ms
          gaps.push({
            index: i,
            prevT,
            prevIso: new Date(prevT).toISOString(),
            currT,
            currIso: new Date(currT).toISOString(),
            diffMin: (diff / 60000).toFixed(1),
            diffHours: (diff / 3600000).toFixed(2),
            diffDays: (diff / 86400000).toFixed(2)
          });
        }
      }
      console.log(`\n异常时间间隔 (diff != 15m) 数量: ${gaps.length}`);
      if (gaps.length > 0) {
        console.log(`前 10 个异常间隔 (Gaps/断层):`);
        console.log(JSON.stringify(gaps.slice(0, 10), null, 2));
      }
    }
  }

  // 同时也检查前端 LabChartPanel 内部渲染的蜡烛
  const panelInfo = await page.evaluate(() => {
    const tfBtns = Array.from(document.querySelectorAll('button')).filter(b => ['日线', '4h', '1h', '15m'].includes(b.textContent?.trim() || ''));
    const activeTf = tfBtns.find(b => b.classList.contains('ds-seg__item--active') || b.getAttribute('aria-selected') === 'true')?.textContent?.trim();
    return {
      activeTf,
      chartText: document.querySelector('[data-lab-section="lab-chart"]')?.textContent?.slice(0, 300)
    };
  });
  console.log('\n=== 前端面板状态 ===', panelInfo);

  await browser.close();
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
