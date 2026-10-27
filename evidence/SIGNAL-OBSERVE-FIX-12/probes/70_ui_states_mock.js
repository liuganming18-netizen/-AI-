/**
 * 70_ui_states_mock.js — 观察区**四态渲染契约**测试（浏览器侧拦截只读接口）。
 *
 * 为什么需要它：`no_candidate`（真的没有符合观察条件的标的）和 `fault`（接口/扫描异常）
 * 在自然行情里无法按需触发，但它们必须显示成**不同的话**——工单第 5 条明确禁止
 * 「统一显示为交易信号 0」。所以这里只拦截**浏览器自己发出的 GET**，
 * 用构造的只读响应验证页面文案与判定属性。
 *
 * 边界（必须说清）：
 *   · 只 mock 浏览器侧的 `/api/v1/signals/observe`（只读、无副作用）；
 *   · **不写生产库、不下单、不注入信号**；后端进程完全不受影响；
 *   · 这是**前端渲染契约**证据，不能替代真实数据证据（后者见 40/50/60 号探针）。
 */
import { createRequire } from 'module';
import path from 'path';
import { fileURLToPath } from 'url';

const require = createRequire('/Users/boos/.workbuddy-ai/binaries/node/workspace/node_modules/');
const { chromium } = require('playwright');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(__dirname, '..', 'shots');
const PAGE = 'http://127.0.0.1:3000/';

const baseEnv = (over) => ({
  mode: 'observe_read_only',
  quality: { status: 'ok', age_seconds: 3, note: null },
  missing_reason: null,
  items: [],
  counts: { BOX: 0, SWEEP: 0, RETEST: 0 },
  observing_total: 0,
  data_not_ready: 0,
  universe: 526,
  stale: false,
  age_seconds: 3,
  writer_pid_alive: true,
  generated_at: '2026-10-25T16:00:00Z',
  scan_errors_total: 0,
  scan_errors: [],
  shown: 0,
  truncated: false,
  definition: '冻结规则 §3：每个 symbol+venue 最多一个处于观察状态的事件⇒ 观察中 = BOX / SWEEP / RETEST',
  ...over,
});

const ITEM = (symbol, state, stateCn, unmet) => ({
  symbol,
  state,
  state_cn: stateCn,
  next_gate: state === 'BOX' ? 'SWEEP' : 'BREAKOUT',
  created_utc: '2026-10-20T00:00:00Z',
  updated_utc: '2026-10-24T17:00:00Z',
  age_hours: 113.0,
  last_bar_utc: '2026-10-25T16:00:00Z',
  data_ready: true,
  box: { H: 84875.2, L: 79178.6, A: 816.32, ER: 0.0615, width_atr: 6.978 },
  satisfied: ['4h 箱体建档（宽度 2.0~8.0A、ER<=0.3）'],
  unmet: [unmet],
  pending_eval: [],
  deadline_utc: '2026-10-31T19:59:59Z',
  remaining_hours: 140.0,
  expired: false,
});

const CASES = [
  {
    key: 'not_connected',
    title: '数据未就绪（运行时未起 / 明细未写出）',
    env: baseEnv({
      quality: { status: 'not_connected', age_seconds: null, note: null },
      missing_reason: '观察明细文件不存在：paper_runtime_3a_observe.json（统一运行时未启动，或启动后尚未完成第一轮扫描）',
      universe: null,
      observing_total: null,
      data_not_ready: null,
      scan_errors_total: null,
    }),
    expectReason: 'data_not_ready',
    expectText: '数据未就绪',
  },
  {
    key: 'no_candidate',
    title: '没有符合条件的标的（扫描正常、数据正常）',
    env: baseEnv({ universe: 526, data_not_ready: 0, scan_errors_total: 0 }),
    expectReason: 'no_candidate',
    expectText: '没有符合观察条件的标的',
  },
  {
    key: 'fault_scan',
    title: '扫描异常（接口正常但扫描有错）',
    env: baseEnv({ scan_errors_total: 7 }),
    expectReason: 'fault',
    expectText: '观察区读取异常',
  },
  {
    key: 'stale',
    title: '过期残留（读到旧明细，写者已退出）',
    env: baseEnv({
      quality: { status: 'stale_record', age_seconds: 5400, note: null },
      stale: true,
      age_seconds: 5400,
      writer_pid_alive: false,
      missing_reason: '观察明细已过期：生成于 2026-10-25T14:30:00Z，距今 5400 秒；写入进程 已退出。⇒ 这批明细不代表当前观察状态，不得当作现状。',
    }),
    expectReason: 'fault',
    expectText: '观察区读取异常',
  },
  {
    key: 'has_items',
    title: '有观察标的（正常渲染逐条）',
    env: baseEnv({
      items: [
        ITEM('AGTUSDT', 'RETEST', '待突破', '首次上穿 0.017387（前一根 close <= 门槛、当前 close > 门槛）（当前 prev=0.017100 cur=0.017200）'),
        ITEM('AIGENSYNUSDT', 'BOX', '箱体观察', '1h low 落入 [0.018070, 0.018830)（当前 0.019100）'),
      ],
      counts: { BOX: 1, SWEEP: 0, RETEST: 1 },
      observing_total: 2,
      shown: 2,
    }),
    expectReason: 'has_items',
    expectText: '观察中 2',
  },
];

(async () => {
  const browser = await chromium.launch({
    executablePath:
      '/Users/boos/Library/Caches/ms-playwright/chromium_headless_shell-1248/' +
      'chrome-headless-shell-mac-arm64/chrome-headless-shell',
    args: ['--no-sandbox'],
  });
  const results = [];
  console.log('='.repeat(78));
  console.log('观察区四态渲染契约测试（浏览器侧拦截只读接口，不触碰生产数据）');
  console.log('='.repeat(78));

  for (const c of CASES) {
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
    const page = await ctx.newPage();
    await page.route('**/api/v1/signals/observe*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(c.env),
      }),
    );
    await page.goto(PAGE, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const panel = page.locator('[data-testid="observe-panel"]');
    for (let i = 0; i < 60; i++) {
      if ((await panel.count()) > 0) break;
      await page.waitForTimeout(500);
    }
    await page.waitForTimeout(800);
    const reason = await panel.getAttribute('data-observe-reason');
    const kind = await panel.getAttribute('data-observe-kind');
    const text = (await panel.innerText()).replace(/\s+/g, ' ');
    const ok = reason === c.expectReason && text.includes(c.expectText);
    results.push({ case: c.key, reason, kind, ok, text: text.slice(0, 220) });
    console.log(
      `\n[${ok ? 'PASS' : 'FAIL'}] ${c.key} — ${c.title}\n` +
        `  data-observe-reason=${reason}（期望 ${c.expectReason}）kind=${kind}\n` +
        `  文案片段: ${text.slice(0, 150)}`,
    );
    await panel.screenshot({ path: path.join(SHOTS, `observe_state_${c.key}.png`) });
    await ctx.close();
  }

  const bad = results.filter((r) => !r.ok);
  console.log('\n' + '='.repeat(78));
  console.log(`合计 ${results.length} 项：通过 ${results.length - bad.length}，失败 ${bad.length}`);
  console.log('='.repeat(78));
  await browser.close();
  process.exit(bad.length ? 1 : 0);
})().catch((e) => {
  console.error('ERR', e);
  process.exit(1);
});
