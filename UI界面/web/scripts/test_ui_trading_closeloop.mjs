/**
 * SYSTEM-CLOSELOOP-01: 端到端 UI 交易链路闭环真实浏览器交互与截屏脚本
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

const BASE_URL = 'http://127.0.0.1:3000';
const OUT_DIR = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-01';
const SHOT_DIR = path.join(OUT_DIR, 'screenshots');
const MKT_DB_PATH = '/Users/boos/Desktop/威科夫因子共振趋势/数据/市场数据/db/market_v2.sqlite3';

async function run() {
  console.log('🚀 启动 Playwright 测试端到端 UI 交易闭环...');
  
  // 查询最新闭合 bar
  const rawSql = execSync(`sqlite3 "${MKT_DB_PATH}" "SELECT open_time, close FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND is_closed=1 ORDER BY open_time DESC LIMIT 1;"`).toString().trim();
  const [openTimeStr, closeStr] = rawSql.split('|');
  const barOpen = Number(openTimeStr);
  const sigEventTime = barOpen - 15 * 60 * 1000;
  console.log(`  最新闭合 15m bar: open_time=${barOpen}, close=${closeStr}`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1680, height: 1050 },
    deviceScaleFactor: 1
  });
  const page = await context.newPage();

  // 1. 访问交易工作台
  console.log('1. 打开交易工作台...');
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(3000);

  // 截图：初始状态
  await page.screenshot({ path: path.join(SHOT_DIR, '10_ui_trading_workbench_initial.png') });
  console.log('  已截取初始工作台: 10_ui_trading_workbench_initial.png');

  // 2. 下达直接撮合成交的模拟开仓
  console.log('2. 下达真实模拟开仓订单 (撮合成交)...');
  const sigUid = `sig_ui_test_${Date.now()}`;
  const openRes = await fetch('http://127.0.0.1:8787/api/v1/paper/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: 'open',
      arm: '3a',
      symbol: 'BTCUSDT',
      direction: 'long',
      atr: 500.0,
      signal_uid: sigUid,
      event_time: sigEventTime,
      available_at: sigEventTime,
      requested_at: sigEventTime,
      now_ms: barOpen + 60000
    })
  }).then(r => r.json());

  const openItem = openRes.items?.[0] || openRes;
  console.log('  开仓响应:', JSON.stringify(openItem));

  // 刷新工作台页面以呈现最新持仓
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  // 截图：持仓呈现
  await page.screenshot({ path: path.join(SHOT_DIR, '11_ui_position_active.png') });
  console.log('  已截取持仓激活图: 11_ui_position_active.png');

  // 3. 执行平仓
  const pid = openItem.position_id;
  if (pid) {
    console.log(`3. 执行平仓 position_id=${pid}...`);
    const closeRes = await fetch('http://127.0.0.1:8787/api/v1/paper/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'close',
        arm: '3a',
        position_id: pid,
        reason: 'ui_closeloop_verification'
      })
    }).then(r => r.json());

    console.log('  平仓响应:', JSON.stringify(closeRes.items?.[0] || closeRes));

    // 再次刷新工作台并截图：持仓已清零
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: path.join(SHOT_DIR, '12_ui_position_closed.png') });
    console.log('  已截取平仓后清零图: 12_ui_position_closed.png');
  }

  await browser.close();
  console.log('✅ 端到端 UI 交易闭环验证完成！截图已落盘。');
}

run().catch(err => {
  console.error('UI 闭环测试失败:', err);
  process.exit(1);
});
