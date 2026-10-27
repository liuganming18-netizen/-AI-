/**
 * 30_fault_recovery.mjs —— 隔离浏览器会话下的「五态 + 恢复」验证
 *                          （SIGNAL-UI-LINK-07-R1 要求 1、2）
 *
 * 验什么：
 *   ① 加载中（首帧还没拿到响应）—— 不得显示「暂无信号」，也不得显示错误；
 *   ② 正常有卡（真实候选，来自隔离后端）；
 *   ③ 业务异常 200（`quality.status=no_watermark`）—— 必须显示「有效下限缺失」，**不是空列表**；
 *   ④ HTTP 503 —— 必须显示「接口返回错误」，带状态码；
 *   ⑤ 网络失败 —— 必须显示「无法连接后端」；
 *   ⑥ 正常 0 条（200 + `status=ok` + 非空 note）—— 必须落空态诊断，**不得**因为 note 非空就报错；
 *   ⑦ 恢复 —— 回到真实后端后错误消失、卡片重新出现、同一候选 ID。
 *
 * 为什么在**生产页面**上做：页面代码就是线上那一份，只把 `/api/v1/signals` 的网络出口
 * 在本浏览器会话里换掉（`page.route`）。这样测的是真实渲染路径，不是另写一个测试页。
 * 全程不 reload（除首帧），靠页面自己的 5 秒轮询完成状态迁移 —— 这才叫「恢复」。
 */
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..', '..');
const EVID = resolve(HERE, '..');
const OUT = resolve(EVID, 'out');
const WEB = resolve(ROOT, 'UI界面', 'web');
const BE = resolve(ROOT, '框架', 'backend');
const ISO = resolve(ROOT, 'evidence', 'SIGNAL-UI-LINK-07', 'iso_e2e');
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PY = '/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python';
const API_PORT = 8799;
const PROD_WEB = 'http://127.0.0.1:3000';
const CAND = 'cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000';
const ISO_EPOCH = 1790870400000;

mkdirSync(OUT, { recursive: true });

const res = {
  probe: '30_fault_recovery',
  generated_real_utc: new Date().toISOString(),
  page: PROD_WEB,
  iso_api: `http://127.0.0.1:${API_PORT}`,
  expected_candidate_id: CAND,
  steps: [],
  scenarios: [],
  ok: false,
};
const step = (name, ok, detail) => {
  res.steps.push({ name, ok, detail });
  console.log(`${ok ? '[OK]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

let mode = 'pass'; // pass | delay | biz200 | http503 | netfail | empty200
let browser = null;
let api = null;

async function waitHttp(url, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (r.ok) return Date.now() - t0;
    } catch { /* not ready */ }
    await sleep(600);
  }
  throw new Error(`${label} 未就绪：${url}`);
}

try {
  // ── 隔离后端（提供一张真实候选卡，供「有卡 → 故障 → 恢复」链条使用）──────
  api = spawn(PY, [resolve(HERE, '15_iso_api_launcher.py')], {
    cwd: BE,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      PORT: String(API_PORT),
      ISO_PAPER_EPOCH_MS: String(ISO_EPOCH),
      LIVE_DB: resolve(ISO, 'live_signals.sqlite3'),
      PAPER_LEDGER_DIR: resolve(ISO, 'paper'),
      PAPER_LOCK_DIR: resolve(ISO, 'locks'),
      MARKET_DB: resolve(ROOT, '数据', '市场数据', 'db', 'market_v2.sqlite3'),
      HTTP_PROXY: '', HTTPS_PROXY: '', http_proxy: '', https_proxy: '', no_proxy: '*',
    },
  });
  api.stdout.on('data', () => {});
  api.stderr.on('data', () => {});
  await waitHttp(`http://127.0.0.1:${API_PORT}/api/v1/health`, 150000, '隔离后端');
  step('隔离后端就绪', true, `端口 ${API_PORT}`);

  browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  const bizBody = {
    items: [],
    total: 0,
    limit: 500,
    has_more: false,
    as_of: null,
    quality: {
      status: 'no_watermark',
      age_seconds: null,
      scope: 'current',
      live_start_ms: null,
      paper_epoch_ms: null,
      effective_floor_ms: null,
      note: '下限缺失时如实空态：不用当前时间重置水位、不放行旧信号',
    },
    missing_reason: '有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定',
  };
  const emptyOkBody = {
    items: [],
    total: 0,
    limit: 500,
    has_more: false,
    as_of: null,
    quality: {
      status: 'ok',
      age_seconds: null,
      scope: 'current',
      live_start_ms: 1792748844240,
      paper_epoch_ms: 1791939600000,
      effective_floor_ms: 1792748844240,
      note: '当前交易信号区（scope=current）：origin∈{live,recovered} + enterable + 真实 bar 右边界不早于有效下限 + 真实臂入场 + 排除已终态拒单；合并后 0 张卡（原始 0 条臂行）',
    },
    missing_reason: null,
  };

  const proxyTo = async (route) => {
    const u = new URL(route.request().url());
    const target = `http://127.0.0.1:${API_PORT}${u.pathname}${u.search}`;
    const resp = await route.fetch({ url: target });
    await route.fulfill({ response: resp });
  };

  await page.route('**/api/v1/signals**', async (route) => {
    try {
      if (mode === 'pass') return await proxyTo(route);
      if (mode === 'delay') {
        await sleep(4000);
        return await proxyTo(route);
      }
      if (mode === 'biz200') {
        return await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(bizBody),
        });
      }
      if (mode === 'empty200') {
        return await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(emptyOkBody),
        });
      }
      if (mode === 'http503') {
        return await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ 错误: '模拟 503：上游不可用（故障恢复验证）' }),
        });
      }
      if (mode === 'netfail') {
        return await route.abort('connectionrefused');
      }
    } catch {
      /* 页面关闭时忽略 */
    }
  });

  const snap = async () =>
    page.evaluate(() => {
      const panel = document.querySelector('[data-testid="signal-panel"]');
      const err = document.querySelector('[data-testid="signal-error"]');
      const row = document.querySelector('[data-testid="signal-row"]');
      return {
        state: panel?.getAttribute('data-signals-state') ?? null,
        count: Number(panel?.getAttribute('data-signal-count') ?? -1),
        rows: document.querySelectorAll('[data-testid="signal-row"]').length,
        errKind: err?.getAttribute('data-error-kind') ?? null,
        errStatus: err?.getAttribute('data-error-status') ?? null,
        errText: (err?.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 220) || null,
        empty: !!document.querySelector('[data-testid="signal-empty"]'),
        loading: !!document.querySelector('[data-testid="signal-loading"]'),
        uid: row?.getAttribute('data-signal-uid') ?? null,
      };
    });

  async function waitFor(pred, timeoutMs, label) {
    const t0 = Date.now();
    let last = null;
    while (Date.now() - t0 < timeoutMs) {
      last = await snap();
      if (pred(last)) return { ok: true, snap: last, ms: Date.now() - t0 };
      await sleep(300);
    }
    return { ok: false, snap: last, ms: Date.now() - t0, label };
  }

  async function scenario(name, modeSet, pred, expect, timeout = 20000) {
    mode = modeSet;
    const r = await waitFor(pred, timeout, name);
    const s = r.snap || {};
    res.scenarios.push({ name, mode: modeSet, expect, waited_ms: r.ms, snap: s, ok: r.ok });
    step(`${name}：${expect}`, r.ok, `state=${s.state} rows=${s.rows} kind=${s.errKind} status=${s.errStatus}`);
    await page.screenshot({ path: resolve(OUT, `30_${name}.png`) });
    return r;
  }

  // ── ① 首帧：延迟 4 秒，必须先显示「正在读取」 ────────────────────────────
  mode = 'delay';
  await page.goto(PROD_WEB + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
  const loadingSeen = await waitFor((s) => s.loading === true, 8000, 'loading');
  step('① 加载中：显示「正在读取」而不是「暂无信号」', loadingSeen.ok,
       `loading=${loadingSeen.snap?.loading} empty=${loadingSeen.snap?.empty} state=${loadingSeen.snap?.state}`);
  step('① 加载中：不得同时显示错误', !loadingSeen.snap?.errKind,
       `errKind=${loadingSeen.snap?.errKind}`);

  // ── ② 正常有卡 ──────────────────────────────────────────────────────────
  await scenario('02_有卡', 'pass',
    (s) => s.rows === 1 && s.uid === CAND && s.errKind === null,
    '1 张卡、ID 一致、无错误');

  // ── ③ 业务异常 200（有效下限缺失）────────────────────────────────────────
  const biz = await scenario('03_业务异常200', 'biz200',
    (s) => s.errKind === 'no_watermark' && s.rows === 0,
    '显示「有效下限缺失」，卡片清零');
  step('③ 文案含真实原因（不是空列表）',
       !!biz.snap?.errText && biz.snap.errText.includes('有效下限'),
       (biz.snap?.errText || '').slice(0, 80));
  step('③ 不得同时显示空态诊断', !biz.snap?.empty, `empty=${biz.snap?.empty}`);

  // ── ④ HTTP 503 ──────────────────────────────────────────────────────────
  const http = await scenario('04_HTTP错误', 'http503',
    (s) => s.errKind === 'http' && s.errStatus === '503' && s.rows === 0,
    '显示「接口返回错误」且带 503，卡片清零');
  step('④ 文案含 HTTP 503',
       !!http.snap?.errText && http.snap.errText.includes('503'),
       (http.snap?.errText || '').slice(0, 80));

  // ── ⑤ 网络失败 ──────────────────────────────────────────────────────────
  await scenario('05_网络失败', 'netfail',
    (s) => s.errKind === 'network' && s.rows === 0,
    '显示「无法连接后端」，卡片清零');

  // ── ⑥ 正常 0 条（note 非空也不算故障）───────────────────────────────────
  const empty = await scenario('06_正常空列表', 'empty200',
    (s) => s.errKind === null && s.empty === true && s.rows === 0,
    '落空态诊断、不报错（note 非空不构成故障）');
  step('⑥ 空态时 state=ok', empty.snap?.state === 'ok', empty.snap?.state);

  // ── ⑦ 恢复：回到真实后端，错误消失、卡片回来 ────────────────────────────
  await scenario('07_恢复', 'pass',
    (s) => s.errKind === null && s.rows === 1 && s.uid === CAND && !s.empty,
    '错误消失、卡片重新出现、同一候选 ID');

  // ── ⑧ 轮询稳定性：连等两轮不得抖动 ──────────────────────────────────────
  await sleep(11000);
  const stable = await snap();
  step('⑧ 两轮轮询后仍稳定（1 张卡、同一 ID、无错误）',
       stable.rows === 1 && stable.uid === CAND && !stable.errKind,
       `rows=${stable.rows} uid=${stable.uid} kind=${stable.errKind}`);

  step('页面无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | ') || '无');
  res.page_errors = errs;
} catch (e) {
  step('异常中止', false, String(e && e.message ? e.message : e));
} finally {
  if (browser) {
    try {
      for (const c of browser.contexts()) for (const p of c.pages()) await p.unrouteAll({ behavior: 'ignoreErrors' });
    } catch { /* ignore */ }
    await browser.close().catch(() => {});
  }
  if (api) {
    try { api.kill('SIGTERM'); } catch { /* ignore */ }
    await sleep(800);
    try { if (api.exitCode === null) api.kill('SIGKILL'); } catch { /* ignore */ }
  }
}

res.ok = res.steps.every((s) => s.ok);
writeFileSync(resolve(OUT, '30_fault_recovery.json'), JSON.stringify(res, null, 2), 'utf8');
console.log('\n== 结果：' + (res.ok ? '全部通过' : '存在失败项') + ' ==');
console.log('写出：evidence/SIGNAL-UI-LINK-07-R1/out/30_fault_recovery.json');
process.exit(res.ok ? 0 : 1);
