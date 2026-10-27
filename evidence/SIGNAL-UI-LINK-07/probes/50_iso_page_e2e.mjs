/**
 * 50_iso_page_e2e.mjs —— 隔离环境「候选 → 页面卡片」整条链的**页面级**验收
 *                        （SIGNAL-UI-LINK-07 要求 2）
 *
 * 为什么要这样做，而不是改生产：
 *   生产当前**没有**自然候选（规则决定的：最近一个候选是 2026-10-11T20:00Z，
 *   比启用时点 2026-10-14T01:00Z 还早 2.2 天，此后 13.6 天零候选）。
 *   要证明「有效新候选能一路显示到页面」，只能让一条**真实产生**的候选存在于
 *   一份**隔离**的实时库里，再用**隔离端口**的后端把它渲染出来。
 *
 * 三个隔离点（全部只在本进程/本页面内生效）：
 *   1. 数据：`iso_e2e/live_signals.sqlite3` —— 由 `probes/30_iso_e2e.py` 用真实历史行情、
 *      实际策略引擎 `step_once(now=2026-10-02T15:00:00Z)` 产生、走**正式持久化**写入；
 *   2. 后端：`probes/45_iso_api_launcher.py` 起在 8799，`LIVE_DB` 指向上面那份库，
 *      `PAPER_EPOCH_MS` 仅在**该进程内存**改成模拟启用时点 2026-10-01T16:00Z；
 *      生产 8787 的进程、生产库、生产常量、门禁文件全程不动；
 *   3. 前端：用**生产正在跑的同一份页面**（3000 端口），只在浏览器网络层把
 *      `/api/v1/signals*` 与 `/api/v1/paper/*` 的出口改指向 8799
 *      （`route.fetch` 服务端转发，绕开跨域；页面代码一行没改）。
 *
 * 本脚本**不写任何数据库**。
 */
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync, createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const argOf = (n, d) => { const i = argv.indexOf(n); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };
/** 隔离库目录（默认 iso_e2e）；`--iso iso_recovered` 用于验收 origin=recovered 分支 */
const ISO_DIR_NAME = argOf('--iso', 'iso_e2e');
/** 期望的来源分支：live（默认）/ recovered */
const EXPECT_ORIGIN = argOf('--expect-origin', 'live');
const OUT_SUFFIX = argOf('--out-suffix', '');
const ROOT = resolve(HERE, '..', '..', '..');
const EVID = resolve(HERE, '..');
const OUT = resolve(EVID, 'out');
const WEB = resolve(ROOT, 'UI界面', 'web');
const BE = resolve(ROOT, '框架', 'backend');
const ISO = resolve(EVID, ISO_DIR_NAME);
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PY = '/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python';
const API_PORT = 8799;
const PROD_WEB = 'http://127.0.0.1:3000';
const CAND = 'cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000';
const ISO_EPOCH = 1790870400000; // 2026-10-01T16:00:00Z（模拟启用时点，早于候选）

mkdirSync(OUT, { recursive: true });

const result = {
  probe: '50_iso_page_e2e' + (OUT_SUFFIX ? ':' + OUT_SUFFIX : ''),
  generated_real_utc: new Date().toISOString(),
  isolation: {
    api_port: API_PORT,
    frontend: PROD_WEB + '（生产同一份页面代码；仅网络层换数据源）',
    LIVE_DB: resolve(ISO, 'live_signals.sqlite3'),
    PAPER_LEDGER_DIR: resolve(ISO, 'paper'),
    PAPER_LOCK_DIR: resolve(ISO, 'locks'),
    iso_dir: ISO_DIR_NAME,
    expect_origin: EXPECT_ORIGIN,
    iso_paper_epoch_ms: ISO_EPOCH,
    iso_paper_epoch_utc: '2026-10-01T16:00:00Z',
    prod_paper_epoch_ms: 1791939600000,
    note: '生产 8787 进程 / 生产实时库 / 生产启用时点常量 / 门禁文件全程未改；隔离仅在本进程内存与浏览器网络层生效',
  },
  expected_candidate_id: CAND,
  steps: [],
  ok: false,
};
const step = (name, ok, detail, soft = false) => {
  result.steps.push({ name, ok, detail, soft });
  console.log(`${ok ? '[OK]' : soft ? '[INFO]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

const sha256 = (p) =>
  new Promise((res, rej) => {
    const h = createHash('sha256');
    const s = createReadStream(p);
    s.on('data', (d) => h.update(d));
    s.on('end', () => res(h.digest('hex')));
    s.on('error', rej);
  });

const PROD_LIVE_DB = resolve(BE, 'live', 'live_signals.sqlite3');
const PROD_STORE = resolve(BE, 'live_store.py');
const before = {
  prod_live_db_sha256: await sha256(PROD_LIVE_DB),
  live_store_py_sha256: await sha256(PROD_STORE),
  iso_live_db_sha256: await sha256(resolve(ISO, 'live_signals.sqlite3')),
};
result.fingerprint_before = before;

async function waitHttp(url, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (r.ok) return Date.now() - t0;
    } catch {
      /* 还没起来 */
    }
    await sleep(600);
  }
  throw new Error(`${label} 未在 ${timeoutMs / 1000}s 内就绪：${url}`);
}

const children = [];
let browser = null;
let apiLog = '';
try {
  // ── 1. 隔离后端 ──────────────────────────────────────────────────────────
  const api = spawn(
    PY,
    [resolve(HERE, '45_iso_api_launcher.py')],
    {
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
    },
  );
  const buf = [];
  api.stdout.on('data', (d) => buf.push(String(d)));
  api.stderr.on('data', (d) => buf.push(String(d)));
  children.push(api);

  const apiReadyMs = await waitHttp(`http://127.0.0.1:${API_PORT}/api/v1/health`, 150000, '隔离后端');
  step('隔离后端就绪', true, `端口 ${API_PORT}，${(apiReadyMs / 1000).toFixed(1)}s`);

  // ── 2. 隔离接口：必须返回该候选 ──────────────────────────────────────────
  const sig = await (
    await fetch(`http://127.0.0.1:${API_PORT}/api/v1/signals?scope=current&limit=500`)
  ).json();
  const ids = (sig.items || []).map((x) => x.signal_id);
  result.iso_api = {
    total: sig.total,
    as_of: sig.as_of,
    ids,
    live_start_ms: sig.quality?.live_start_ms,
    paper_epoch_ms: sig.quality?.paper_epoch_ms,
    effective_floor_ms: sig.quality?.effective_floor_ms,
    excluded_breakdown: sig.quality?.excluded_breakdown,
  };
  step('隔离接口返回该候选', sig.total === 1 && ids[0] === CAND, `total=${sig.total}，id=${ids[0]}`);
  step(
    '隔离下限自洽（模拟启用时点生效）',
    sig.quality?.effective_floor_ms === ISO_EPOCH,
    `effective_floor=${sig.quality?.effective_floor_ms}（期望 ${ISO_EPOCH}）`,
  );
  step(
    '隔离未影响生产口径（生产仍是 2026-10-14）',
    (await (await fetch('http://127.0.0.1:8787/api/v1/signals?scope=current&limit=5')).json()).quality
      ?.paper_epoch_ms === 1791939600000,
    '生产 8787 的 paper_epoch_ms 仍为 1791939600000',
  );

  // ── 3. 生产页面 + 网络层换源 ─────────────────────────────────────────────
  browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const routed = [];
  const proxyTo = async (route) => {
    try {
      const u = new URL(route.request().url());
      const target = `http://127.0.0.1:${API_PORT}${u.pathname}${u.search}`;
      routed.push(target);
      const resp = await route.fetch({ url: target });
      await route.fulfill({ response: resp });
    } catch {
      /* 页面关闭/请求取消：忽略，不让 route 回调把进程带崩 */
    }
  };
  await page.route('**/api/v1/signals**', proxyTo);
  await page.route('**/api/v1/paper/**', proxyTo);
  result.routed_requests = routed;

  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  await page.goto(PROD_WEB + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-row"]', { timeout: 90000 });

  const cards = await page.$$eval('[data-testid="signal-row"]', (nodes) =>
    nodes.map((n) => ({
      uid: n.getAttribute('data-signal-uid'),
      origin: n.getAttribute('data-origin'),
      exec: n.getAttribute('data-executable-state'),
      symbol: n.getAttribute('data-symbol'),
      text: (n.textContent || '').replace(/\s+/g, ' ').slice(0, 160),
    })),
  );
  result.page_cards = cards;
  step('页面上出现信号卡片', cards.length === 1, `${cards.length} 张`);
  step('卡片 ID == 期望候选 ID', cards[0]?.uid === CAND, cards[0]?.uid || '(无)');
  step(
    '卡片属性（来源 / 可执行 / 币种）',
    cards[0]?.origin === EXPECT_ORIGIN &&
      cards[0]?.exec === 'enterable' &&
      cards[0]?.symbol === 'VELVETUSDT',
    `origin=${cards[0]?.origin} exec=${cards[0]?.exec} symbol=${cards[0]?.symbol}`,
  );
  const hasEmpty = await page.$('[data-testid="signal-empty"]');
  step('未落回空态', !hasEmpty, hasEmpty ? '同时出现空态块' : '无空态块');
  step(
    '页面确实走的是隔离出口',
    routed.some((u) => u.includes(`:${API_PORT}/api/v1/signals`)),
    routed.filter((u) => u.includes('/signals')).slice(0, 1).join('') || '未捕获',
  );

  const shot = `09_iso_page_candidate${OUT_SUFFIX ? '_' + OUT_SUFFIX : ''}.png`;
  await page.screenshot({ path: resolve(OUT, shot) });
  step('截图已保存', true, 'out/' + shot);

  // ── 4. 刷新幂等 ──────────────────────────────────────────────────────────
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="signal-row"]', { timeout: 90000 });
  const cards2 = await page.$$eval('[data-testid="signal-row"]', (n) => n.length);
  step('刷新后不重复', cards2 === 1, `刷新后 ${cards2} 张卡`);

  step('页面无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | ') || '无');
} catch (e) {
  step('异常中止', false, String(e && e.message ? e.message : e));
} finally {
  if (browser) {
    try {
      for (const p of browser.contexts()) {
        for (const pg of p.pages()) {
          await pg.unrouteAll({ behavior: 'ignoreErrors' });
        }
      }
    } catch { /* ignore */ }
    await browser.close().catch(() => {});
  }
  for (const cp of children) {
    try { cp.kill('SIGTERM'); } catch { /* ignore */ }
  }
  await sleep(1200);
  for (const cp of children) {
    try { if (cp.exitCode === null) cp.kill('SIGKILL'); } catch { /* ignore */ }
  }
  apiLog = (result.__apiBuf || '').slice(-1200);
}

// ── 5. 三方对账（页面 / 接口 / 库）+ 指纹 ───────────────────────────────────
const dbRow = await new Promise((res) => {
  const cp = spawn(PY, ['-c', `
import sqlite3, json
con = sqlite3.connect("file:${resolve(ISO, 'live_signals.sqlite3')}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select signal_uid, origin, executable_state, symbol from live_signal")]
print(json.dumps({"n": len(rows), "rows": rows}, ensure_ascii=False))
`], { stdio: ['ignore', 'pipe', 'pipe'] });
  let o = '';
  cp.stdout.on('data', (d) => (o += d));
  cp.on('close', () => res(o.trim()));
});
const db = JSON.parse(dbRow || '{"n":0,"rows":[]}');
result.db = db;
step('数据库只读核验', db.n === 1 && db.rows[0]?.signal_uid === CAND, `${db.n} 行`);

const after = {
  prod_live_db_sha256: await sha256(PROD_LIVE_DB),
  live_store_py_sha256: await sha256(PROD_STORE),
  iso_live_db_sha256: await sha256(resolve(ISO, 'live_signals.sqlite3')),
};
result.fingerprint_after = after;
step(
  '生产实时库未被写入',
  before.prod_live_db_sha256 === after.prod_live_db_sha256,
  `${before.prod_live_db_sha256.slice(0, 12)}… → ${after.prod_live_db_sha256.slice(0, 12)}…`,
);
step(
  '启用时点常量所在文件未被改写',
  before.live_store_py_sha256 === after.live_store_py_sha256,
  `live_store.py sha ${before.live_store_py_sha256.slice(0, 12)}… 一致`,
);
step(
  '隔离库也未被本探针改写（只读打开）',
  before.iso_live_db_sha256 === after.iso_live_db_sha256,
  before.iso_live_db_sha256 === after.iso_live_db_sha256 ? 'sha 一致' : 'sha 变化',
);
step(
  '三方按候选 ID 对齐（页面 / 接口 / 库）',
  result.page_cards?.[0]?.uid === CAND && result.iso_api?.ids?.[0] === CAND && db.rows[0]?.signal_uid === CAND,
  '页面 / 接口 / 库 三处同一 ID',
);

result.api_log_tail = apiLog;
result.ok = result.steps.filter((s) => !s.soft).every((s) => s.ok);
const outJson = `09_iso_page_e2e${OUT_SUFFIX ? '_' + OUT_SUFFIX : ''}.json`;
writeFileSync(resolve(OUT, outJson), JSON.stringify(result, null, 2), 'utf8');
console.log('\n== 结果：' + (result.ok ? '全部通过' : '存在失败项') + ' ==');
console.log('写出：evidence/SIGNAL-UI-LINK-07/out/' + outJson);
process.exit(result.ok ? 0 : 1);
