import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..');
const WEB = resolve(ROOT, 'UI界面', 'web');
const EVID_R1 = resolve(ROOT, 'evidence', 'SIGNAL-UI-LINK-07-R1');
const ISO_LIVE_DB = resolve(EVID_R1, 'iso_live', 'live_signals.sqlite3');
const ISO_DIR = resolve(EVID_R1, 'iso_live');
const LAUNCHER = resolve(EVID_R1, 'probes', '15_iso_api_launcher.py');
const PY = '/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python';

const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PROD_WEB = 'http://127.0.0.1:3000';
const ISO_PORT = 8795;
const EXPECTED_CANDIDATE_ID = 'cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000';

async function run() {
  console.log('🚀 启动隔离后端服务 (Port 8799) 基于真实 live 数据库...');
  const BE = resolve(ROOT, '框架', 'backend');
  const env = {
    ...process.env,
    PORT: String(ISO_PORT),
    LIVE_DB: ISO_LIVE_DB,
    PAPER_LEDGER_DIR: resolve(ISO_DIR, 'paper'),
    PAPER_LOCK_DIR: resolve(ISO_DIR, 'locks'),
    ISO_PAPER_EPOCH_MS: '1790870400000',
    MARKET_DB: resolve(ROOT, '数据', '市场数据', 'db', 'market_v2.sqlite3'),
    HTTP_PROXY: '',
    HTTPS_PROXY: '',
    http_proxy: '',
    https_proxy: '',
    no_proxy: '*',
  };

  const be = spawn(PY, [LAUNCHER], { cwd: BE, env, stdio: ['ignore', 'pipe', 'pipe'] });
  be.stderr.on('data', (d) => process.stderr.write(`[iso-be] ${d}`));

  let ready = false;
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${ISO_PORT}/api/v1/health`);
      if (res.ok) {
        ready = true;
        break;
      }
    } catch {}
    await sleep(400);
  }

  if (!ready) {
    be.kill();
    throw new Error('隔离后端启动超时');
  }
  console.log('  隔离后端就绪！');

  const browser = await chromium.launch({ headless: true });
  const results = {
    testedAt: new Date().toISOString(),
    expectedCandidateId: EXPECTED_CANDIDATE_ID,
    steps: {},
  };

  try {
    // ----------------------------------------------------
    // 第一阶段：打开会话 Context 1，验证真实候选卡片首次显示
    // ----------------------------------------------------
    console.log('1. 打开会话 Context 1 访问交易工作台...');
    const ctx1 = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page1 = await ctx1.newPage();

    const proxyTo = async (route) => {
      try {
        const u = new URL(route.request().url());
        const target = `http://127.0.0.1:${ISO_PORT}${u.pathname}${u.search}`;
        const resp = await route.fetch({ url: target });
        await route.fulfill({ response: resp });
      } catch {}
    };

    await page1.route('**/api/v1/signals**', proxyTo);
    await page1.route('**/api/v1/paper/**', proxyTo);

    await page1.goto(`${PROD_WEB}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page1.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
    await page1.waitForSelector('[data-testid="signal-row"]', { timeout: 30000 });

    const initialCards = await page1.$$('[data-testid="signal-row"]');
    const initialCount = initialCards.length;
    let initialUid = null;
    if (initialCount > 0) {
      initialUid = await initialCards[0].getAttribute('data-signal-uid');
    }
    await page1.screenshot({ path: resolve(HERE, '06_step1_initial_card.png') });

    // ----------------------------------------------------
    // 第二阶段：刷新 (Reload) 验证
    // ----------------------------------------------------
    console.log('2. 页面硬刷新 (Reload)...');
    await page1.reload({ waitUntil: 'domcontentloaded' });
    await page1.waitForSelector('[data-testid="signal-row"]', { timeout: 30000 });

    const reloadCards = await page1.$$('[data-testid="signal-row"]');
    const reloadCount = reloadCards.length;
    const reloadUid = reloadCount > 0 ? await reloadCards[0].getAttribute('data-signal-uid') : null;
    await page1.screenshot({ path: resolve(HERE, '07_step2_after_reload.png') });

    // ----------------------------------------------------
    // 第三阶段：两轮轮询 (连续等待 12 秒)
    // ----------------------------------------------------
    console.log('3. 等待两轮轮询 (12 秒)...');
    await page1.waitForTimeout(12000);

    const pollCards = await page1.$$('[data-testid="signal-row"]');
    const pollCount = pollCards.length;
    const pollUid = pollCount > 0 ? await pollCards[0].getAttribute('data-signal-uid') : null;
    await page1.screenshot({ path: resolve(HERE, '08_step3_after_two_polls.png') });

    // ----------------------------------------------------
    // 第四阶段：完全关闭 Context 1，重新开辟 Context 2
    // ----------------------------------------------------
    console.log('4. 关闭 Context 1，新建 Context 2 重新进入（不靠注入，靠后端自愈持存）...');
    await ctx1.close();

    const ctx2 = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
    const page2 = await ctx2.newPage();

    await page2.route('**/api/v1/signals**', proxyTo);
    await page2.route('**/api/v1/paper/**', proxyTo);

    await page2.goto(`${PROD_WEB}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page2.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
    await page2.waitForSelector('[data-testid="signal-row"]', { timeout: 30000 });

    const reopenCards = await page2.$$('[data-testid="signal-row"]');
    const reopenCount = reopenCards.length;
    const reopenUid = reopenCount > 0 ? await reopenCards[0].getAttribute('data-signal-uid') : null;
    await page2.screenshot({ path: resolve(HERE, '09_step4_after_reopen.png') });
    await ctx2.close();

    results.steps = {
      initial: { count: initialCount, uid: initialUid, ok: initialCount === 1 && initialUid === EXPECTED_CANDIDATE_ID },
      reload: { count: reloadCount, uid: reloadUid, ok: reloadCount === 1 && reloadUid === EXPECTED_CANDIDATE_ID },
      polling: { count: pollCount, uid: pollUid, ok: pollCount === 1 && pollUid === EXPECTED_CANDIDATE_ID },
      reopen: { count: reopenCount, uid: reopenUid, ok: reopenCount === 1 && reopenUid === EXPECTED_CANDIDATE_ID },
    };

    results.verdict =
      results.steps.initial.ok &&
      results.steps.reload.ok &&
      results.steps.polling.ok &&
      results.steps.reopen.ok
        ? 'PASS: 真实候选在初始、硬刷新、两轮轮询以及关闭重开后均保持精准显示，不丢失、不重复，且关闭重开直接通过后端真实服务恢复'
        : 'FAIL';

    writeFileSync(
      resolve(HERE, '06_reopen_and_persistence_summary.json'),
      JSON.stringify(results, null, 2),
      'utf-8'
    );
    console.log('✅ 独立关闭重开复验完成，结果写入 06_reopen_and_persistence_summary.json');
  } finally {
    await browser.close();
    be.kill();
  }
}

run().catch((err) => {
  console.error('复验失败:', err);
  process.exit(1);
});
