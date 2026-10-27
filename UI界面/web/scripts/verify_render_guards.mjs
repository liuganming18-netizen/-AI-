/**
 * 渲染兜底验证（只读，用 route 拦截伪造畸形响应，不碰真实数据）
 * 用法：node scripts/verify_render_guards.mjs
 *
 * 背景（2026-10-17 白屏事故）：
 *   `chart.candles` 线上有**两种形态** —— 数组元组 `[t,o,h,l,c,v]` 与对象 `{t,o,h,l,c,v}`，
 *   本地 644 份 chart.json 实测 375 : 269。旧代码只按元组解构，点到对象形态的 run 就抛
 *   `TypeError: xxx is not iterable`，React 卸载整棵树 ⇒ 整页白屏。
 *
 * 本脚本锁死三条：
 *   1. 畸形 candles（null / 字符串 / 缺 OHLC）⇒ 丢弃并显示跳过根数，不崩；
 *   2. 某一块渲染抛错（用 /trades 返回 [null] 触发）⇒ 只有那块显示「渲染失败」，
 *      K 线 canvas 与左栏仍在，root 不被卸载；
 *   3. 全程不允许有未捕获 pageerror。
 */
import { chromium } from 'playwright';

const browser = await chromium.launch();
const problems = [];
const okJson = (route, body) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function open(intercept) {
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push((e.message || '').slice(0, 200)));
  await page.route('**/api/v1/backtests/**', intercept);
  await page.goto('http://127.0.0.1:3000/?theme=dark', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  await page.getByRole('button', { name: '回测复盘' }).click();
  for (let i = 0; i < 30; i++) {
    if (await page.locator('[data-run-id]').count()) break;
    await page.waitForTimeout(800);
  }
  await page.locator('[data-run-id]').first().click();
  await page.waitForTimeout(3000);
  const state = await page.evaluate(() => ({
    root: document.querySelector('#root')?.children.length ?? 0,
    canvas: document.querySelectorAll('canvas').length,
    alerts: [...document.querySelectorAll('[role="alert"]')].map((e) => e.innerText.split('\n')[0]),
    meta: document.querySelector('.ds-card__meta')?.innerText ?? '',
  }));
  return { page, errors, state };
}

// ── 1. 畸形 candles ──────────────────────────────────────────────────────
{
  const { page, errors, state } = await open(async (route) => {
    const url = route.request().url();
    if (!url.includes('/chart')) return route.continue();
    const resp = await route.fetch();
    const j = await resp.json();
    const ch = j.chart || j;
    // null / 字符串 / 只有 t / 正常对象 / 正常元组
    ch.candles = [
      null,
      'oops',
      { t: 1758758400000 },
      { t: 1758759300000, o: 1, h: 2, l: 0.5, c: 1.5, v: 10 },
      [1758760200000, 1, 2, 0.5, 1.5, 5],
    ];
    return okJson(route, j);
  });
  console.log('[畸形 candles]', JSON.stringify(state), errors[0] ?? '');
  if (state.root === 0) problems.push('畸形 candles 把整页搞没了');
  if (state.canvas === 0) problems.push('畸形 candles 后 K 线没画出来（合法那根应能画）');
  if (!/跳过/.test(state.meta)) problems.push('畸形 candles 没有显示跳过根数');
  if (errors.length) problems.push('畸形 candles 出现未捕获异常: ' + errors[0]);
  await page.screenshot({ path: '/tmp/guard_bad_candles.png' });
  await page.close();
}

// ── 2. 单块渲染抛错 → 错误边界接住 ───────────────────────────────────────
{
  const { page, errors, state } = await open(async (route) => {
    const url = route.request().url();
    if (url.includes('/trades')) return okJson(route, { items: [null], total: 1 });
    return route.continue();
  });
  console.log('[边界触发]', JSON.stringify(state), errors[0] ?? '');
  if (state.root === 0) problems.push('边界没接住，整页被卸载');
  if (state.canvas === 0) problems.push('一块崩了把 K 线也带走了（边界粒度过粗）');
  if (!state.alerts.length) problems.push('没有出现「渲染失败」提示，边界未生效');
  if (errors.length) problems.push('边界触发时仍有未捕获异常: ' + errors[0]);
  await page.screenshot({ path: '/tmp/guard_boundary.png' });
  await page.close();
}

await browser.close();
if (problems.length) {
  console.error('❌ ' + problems.join('；'));
  process.exitCode = 1;
} else {
  console.log('✅ 渲染兜底全部通过');
}
