/**
 * 异常路径验证（只读，用 route 拦截伪造后端，不碰真实数据）。
 * 用法：node scripts/verify_fallbacks.mjs <输出目录>
 *
 * 覆盖四条：
 *   1. 列表为空（items: []）        → 空态要写清「为什么空 + 怎么办」
 *   2. 列表 500                     → 失败态 + 重试按钮，页面不崩
 *   3. 列表正常但 detail 全 404      → 头部/指标/图表降级为「—」，不白屏
 *   4. 断网（route.abort）           → 失败态，不抛未捕获异常
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || '/tmp/bt_fallback';
fs.mkdirSync(OUT, { recursive: true });
const BASE = 'http://127.0.0.1:3000/?theme=dark';

const browser = await chromium.launch();
const problems = [];

async function open({ list, detail }) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource|net::ERR/.test(m.text())) {
      pageErrors.push(m.text().slice(0, 200));
    }
  });

  await page.route('**/api/v1/backtests**', async (route) => {
    const url = route.request().url();
    const isList = /\/backtests(\?|$)/.test(url);
    if (isList) return list(route);
    return detail(route, url);
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: '回测复盘' }).click();
  await page.waitForTimeout(2500);
  return { page, pageErrors };
}

const okJson = (route, body) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

// ── 1. 空列表 ────────────────────────────────────────────────────────────
{
  const { page, pageErrors } = await open({
    list: (r) =>
      okJson(r, {
        mode: 'research_backtest',
        contract_version: 'bt_api_v1',
        items: [],
        total: 0,
      }),
    detail: (r) => r.fulfill({ status: 404, body: '{}' }),
  });
  const text = await page.locator('.ds-bt-side').innerText();
  console.log('[empty] 左栏文案:', JSON.stringify(text.split('\n').slice(-4).join(' / ')));
  await page.screenshot({ path: path.join(OUT, 'fb_empty.png') });
  if (!/还没有任何回测记录|没有匹配的回测/.test(text)) problems.push('空列表没有专门空态');
  if (pageErrors.length) problems.push('空列表出现页面异常: ' + pageErrors[0]);
  await page.close();
}

// ── 2. 列表 500 ──────────────────────────────────────────────────────────
{
  const { page, pageErrors } = await open({
    list: (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }),
    detail: (r) => r.fulfill({ status: 500, body: '{}' }),
  });
  const text = await page.locator('.ds-bt-side').innerText();
  console.log('[500] 左栏文案:', JSON.stringify(text.split('\n').slice(-4).join(' / ')));
  await page.screenshot({ path: path.join(OUT, 'fb_list_500.png') });
  if (!/失败|重试/.test(text)) problems.push('列表 500 没有失败态');
  const retry = page.getByRole('button', { name: '重试' });
  if (!(await retry.count())) problems.push('列表 500 没有重试按钮');
  if (pageErrors.length) problems.push('列表 500 出现页面异常: ' + pageErrors[0]);
  await page.close();
}

// ── 3. 列表正常，detail 全 404 ───────────────────────────────────────────
{
  const { page, pageErrors } = await open({
    list: (r) =>
      okJson(r, {
        mode: 'research_backtest',
        contract_version: 'bt_api_v1',
        total: 1,
        items: [
          {
            run_id: 'run_WX_EVENT_TREND_V0_1_BTCUSDT_1700000000',
            strategy_id: 'WX_EVENT_TREND_V0_1',
            status: 'completed',
            symbols: ['BTCUSDT'],
            net_return_pct: 12.3,
            trades_count: 3,
            window: { signal_timeframe: '15m' },
          },
        ],
      }),
    detail: (r) => r.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' }),
  });
  await page.waitForTimeout(1500);
  const main = await page.locator('.ds-bt-main').innerText();
  console.log('[detail 404] 主区首行:', JSON.stringify(main.split('\n').slice(0, 6).join(' / ')));
  await page.screenshot({ path: path.join(OUT, 'fb_detail_404.png') });
  // 允许出现「取数失败」提示，但不允许整块空白 / 未捕获异常
  if (main.trim().length < 20) problems.push('detail 404 时主区几乎空白');
  if (pageErrors.length) problems.push('detail 404 出现页面异常: ' + pageErrors[0]);
  await page.close();
}

// ── 4. 断网 ──────────────────────────────────────────────────────────────
{
  const { page, pageErrors } = await open({
    list: (r) => r.abort('failed'),
    detail: (r) => r.abort('failed'),
  });
  const text = await page.locator('.ds-bt-side').innerText();
  console.log('[offline] 左栏文案:', JSON.stringify(text.split('\n').slice(-4).join(' / ')));
  await page.screenshot({ path: path.join(OUT, 'fb_offline.png') });
  if (!/失败|重试/.test(text)) problems.push('断网没有失败态');
  if (pageErrors.length) problems.push('断网出现页面异常: ' + pageErrors[0]);
  await page.close();
}

await browser.close();
if (problems.length) {
  console.error('❌ ' + problems.join('；'));
  process.exitCode = 1;
} else {
  console.log('✅ 异常路径全部通过');
}
