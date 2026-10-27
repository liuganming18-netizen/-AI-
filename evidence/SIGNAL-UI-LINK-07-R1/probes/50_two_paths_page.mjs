/**
 * 50_two_paths_page.mjs —— 两条真实候选路径的**页面级**验收（SIGNAL-UI-LINK-07-R1 要求 3）
 *
 * 用户要求原话：
 *   「用现有真实历史行情，分别验证「新产生候选」与「重启恢复候选」，经正式引擎→
 *     数据库→API→页面。按**同一候选 ID** 核对币种、方向、确认时间、可执行状态。
 *     刷新、轮询、重复处理后不丢失、不重复。」
 *
 * 两条路径的数据分别来自（都是本单探针 `40_two_paths.py` 用**正式引擎**跑的）：
 *   --mode live       → evidence/SIGNAL-UI-LINK-07-R1/iso_live/live_signals.sqlite3
 *                       origin=live，由 `step_once(now=15:00, origin='live')` 现场产生
 *   --mode recovered  → evidence/SIGNAL-UI-LINK-07-R1/iso_recovered/live_signals.sqlite3
 *                       origin=recovered，由 bootstrap 尾部补齐 + `_route_recovered_candidates()`
 *
 * 三个隔离点（只在**本进程内存**与**浏览器网络层**生效）：
 *   1. 数据：上面那份隔离库；
 *   2. 后端：`probes/15_iso_api_launcher.py` 起在 8799(live) / 8798(recovered)，
 *      `LIVE_DB` 指向隔离库，`PAPER_EPOCH_MS` 只在该进程内存改成模拟启用时点；
 *   3. 前端：用**生产正在跑的同一份页面**（3000），只在网络层把 `/api/v1/signals*`
 *      与 `/api/v1/paper/*` 的出口改指向隔离端口（页面代码一行没改）。
 *
 * 本脚本**不写任何数据库**（隔离库以只读方式打开核验；前后 sha 必须一致）。
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
const argOf = (n, d) => {
  const i = argv.indexOf(n);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const MODE = argOf('--mode', 'live');
if (!['live', 'recovered'].includes(MODE)) {
  throw new Error(`--mode 只能是 live / recovered，收到 ${MODE}`);
}
const ISO_DIR = MODE === 'live' ? 'iso_live' : 'iso_recovered';
const API_PORT = MODE === 'live' ? 8799 : 8798;
const OUT_SUFFIX = MODE;

const ROOT = resolve(HERE, '..', '..', '..');
const EVID = resolve(HERE, '..');
const OUT = resolve(EVID, 'out');
const WEB = resolve(ROOT, 'UI界面', 'web');
const BE = resolve(ROOT, '框架', 'backend');
const ISO = resolve(EVID, ISO_DIR);
const ISO_DB = resolve(ISO, 'live_signals.sqlite3');
const require = createRequire(resolve(WEB, 'noop.js'));
const { chromium } = require('playwright');

const PY = '/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python';
const PROD_WEB = 'http://127.0.0.1:3000';
const PROD_API = 'http://127.0.0.1:8787';
const CAND = 'cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000';
const ISO_EPOCH = 1790870400000;            // 2026-10-01T16:00:00Z
const PROD_EPOCH = 1791939600000;           // 2026-10-14T01:00:00Z
const EXPECT = {
  symbol: 'VELVETUSDT',
  direction: 'long',
  confirmed_at: 1790953200000,             // 2026-10-02T15:00:00Z
  executable_state: 'enterable',
};

mkdirSync(OUT, { recursive: true });

const result = {
  probe: `50_two_paths_page[${MODE}]`,
  mode: MODE,
  generated_real_utc: new Date().toISOString(),
  isolation: {
    api_port: API_PORT,
    frontend: PROD_WEB + '（生产同一份页面代码；仅网络层换数据源）',
    LIVE_DB: ISO_DB,
    PAPER_LEDGER_DIR: resolve(ISO, 'paper'),
    PAPER_LOCK_DIR: resolve(ISO, 'locks'),
    iso_paper_epoch_ms: ISO_EPOCH,
    iso_paper_epoch_utc: '2026-10-01T16:00:00Z',
    prod_paper_epoch_ms: PROD_EPOCH,
    note: '生产 8787 进程 / 生产实时库 / 生产启用时点常量 / 门禁文件全程未改',
  },
  expected: { candidate_id: CAND, ...EXPECT },
  steps: [],
  ok: false,
};
const step = (name, ok, detail, soft = false) => {
  result.steps.push({ name, ok, detail, soft });
  console.log(`${ok ? '[OK]' : soft ? '[INFO]' : '[FAIL]'} ${name}${detail ? ' — ' + detail : ''}`);
};

/**
 * 确认时间归一化。
 * 三层的**表示**不同但**时刻**必须相同：
 *   · 数据库 `confirmed_at`：毫秒整数（1790953200000）
 *   · 接口 `confirmed_at`：ISO 字符串（2026-10-02T15:00:00Z）
 *   · 页面 `data-confirmed-at`：ISO 字符串（由接口值直接透传）
 * 直接 `Number()` 比会得到 NaN ⇒ 必须按「同一时刻」比，不是放宽标准。
 */
const toMs = (v) => {
  if (v === null || v === undefined || v === '') return NaN;
  if (typeof v === 'number') return v;
  const n = Number(v);
  if (Number.isFinite(n)) return n;
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : NaN;
};

const sha256 = (p) =>
  new Promise((res, rej) => {
    const h = createHash('sha256');
    const s = createReadStream(p);
    s.on('data', (d) => h.update(d));
    s.on('end', () => res(h.digest('hex')));
    s.on('error', rej);
  });

/** 只读查隔离库（子进程，避免本进程持有连接影响 sha） */
const readDb = () =>
  new Promise((res) => {
    const cp = spawn(PY, ['-c', `
import sqlite3, json
con = sqlite3.connect("file:${ISO_DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "select signal_uid, symbol, direction, origin, display_state, executable_state, "
    "executable_reason, confirmed_at, available_at, event_time from live_signal "
    "order by confirmed_at")]
print(json.dumps({"n": len(rows), "rows": rows}, ensure_ascii=False))
`], { stdio: ['ignore', 'pipe', 'pipe'] });
    let o = '';
    cp.stdout.on('data', (d) => (o += d));
    cp.on('close', () => {
      try { res(JSON.parse(o.trim())); } catch { res({ n: -1, rows: [], raw: o.slice(0, 400) }); }
    });
  });

const PROD_LIVE_DB = resolve(BE, 'live', 'live_signals.sqlite3');
const PROD_STORE = resolve(BE, 'live_store.py');
const before = {
  prod_live_db_sha256: await sha256(PROD_LIVE_DB),
  live_store_py_sha256: await sha256(PROD_STORE),
  iso_live_db_sha256: await sha256(ISO_DB),
};
result.fingerprint_before = before;

async function waitHttp(url, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (r.ok) return Date.now() - t0;
    } catch { /* 还没起来 */ }
    await sleep(600);
  }
  throw new Error(`${label} 未在 ${timeoutMs / 1000}s 内就绪：${url}`);
}

const children = [];
let browser = null;
let apiLog = '';

try {
  // ── 1. 隔离后端 ────────────────────────────────────────────────────────────
  const api = spawn(PY, [resolve(HERE, '15_iso_api_launcher.py')], {
    cwd: BE,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      PORT: String(API_PORT),
      ISO_PAPER_EPOCH_MS: String(ISO_EPOCH),
      LIVE_DB: ISO_DB,
      PAPER_LEDGER_DIR: resolve(ISO, 'paper'),
      PAPER_LOCK_DIR: resolve(ISO, 'locks'),
      MARKET_DB: resolve(ROOT, '数据', '市场数据', 'db', 'market_v2.sqlite3'),
      HTTP_PROXY: '', HTTPS_PROXY: '', http_proxy: '', https_proxy: '', no_proxy: '*',
    },
  });
  const buf = [];
  api.stdout.on('data', (d) => buf.push(String(d)));
  api.stderr.on('data', (d) => buf.push(String(d)));
  children.push(api);
  api.on('exit', () => { apiLog = buf.join('').slice(-1500); });

  const apiReadyMs = await waitHttp(
    `http://127.0.0.1:${API_PORT}/api/v1/health`, 180000, '隔离后端');
  step('隔离后端就绪', true, `端口 ${API_PORT}，${(apiReadyMs / 1000).toFixed(1)}s`);

  // ── 2. 接口层：四项字段 ────────────────────────────────────────────────────
  const sigUrl = `http://127.0.0.1:${API_PORT}/api/v1/signals?scope=current&limit=500`;
  const sig = await (await fetch(sigUrl)).json();
  const items = sig.items || [];
  const it = items[0] || {};
  result.iso_api = {
    total: sig.total,
    as_of: sig.as_of,
    missing_reason: sig.missing_reason,
    quality_status: sig.quality?.status,
    quality_note_len: (sig.quality?.note || '').length,
    effective_floor_ms: sig.quality?.effective_floor_ms,
    item: {
      signal_id: it.signal_id,
      symbol: it.symbol,
      direction: it.direction,
      confirmed_at: it.confirmed_at,
      executable_state: it.executable_state,
      origin: it.origin,
      executable_reason: it.executable_reason,
    },
  };
  step('接口返回 1 条', sig.total === 1 && items.length === 1,
    `total=${sig.total} n=${items.length}`);
  step('接口 quality.status = ok', sig.quality?.status === 'ok',
    `status=${sig.quality?.status} note长度=${result.iso_api.quality_note_len}`);
  step('接口 missing_reason 为空（正常态不写故障字段）',
    !sig.missing_reason, `missing_reason=${JSON.stringify(sig.missing_reason)}`);
  step('接口生效下限 = 模拟启用时点', sig.quality?.effective_floor_ms === ISO_EPOCH,
    `${sig.quality?.effective_floor_ms}（期望 ${ISO_EPOCH}）`);
  step('接口四项字段与期望一致',
    it.signal_id === CAND && it.symbol === EXPECT.symbol &&
    it.direction === EXPECT.direction &&
    toMs(it.confirmed_at) === EXPECT.confirmed_at &&
    it.executable_state === EXPECT.executable_state,
    `id=${it.signal_id} symbol=${it.symbol} dir=${it.direction} ` +
    `confirmed=${it.confirmed_at}(${toMs(it.confirmed_at)}) exec=${it.executable_state}`);
  step('接口 origin 与路径一致', it.origin === MODE, `origin=${it.origin}`);

  // 重复请求：接口必须幂等（不新增行、不重复返回）
  const repeatIds = [];
  for (let i = 0; i < 5; i += 1) {
    const r = await (await fetch(sigUrl)).json();
    repeatIds.push((r.items || []).map((x) => x.signal_id).join(','));
  }
  result.repeat_api = { n: repeatIds.length, distinct: [...new Set(repeatIds)] };
  step('接口重复请求 5 次结果稳定（不重复）',
    new Set(repeatIds).size === 1 && repeatIds[0] === CAND,
    `distinct=${new Set(repeatIds).size}`);

  // 生产口径未被影响
  const prodSig = await (await fetch(`${PROD_API}/api/v1/signals?scope=current&limit=5`)).json();
  result.prod_api = {
    total: prodSig.total,
    paper_epoch_ms: prodSig.quality?.paper_epoch_ms,
    quality_status: prodSig.quality?.status,
    missing_reason: prodSig.missing_reason,
  };
  step('生产口径未变（启用时点仍是 2026-10-14）',
    prodSig.quality?.paper_epoch_ms === PROD_EPOCH,
    `prod paper_epoch_ms=${prodSig.quality?.paper_epoch_ms} total=${prodSig.total}`);

  // ── 3. 页面层 ─────────────────────────────────────────────────────────────
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
    } catch { /* 页面关闭/请求取消：忽略，不让 route 回调把进程带崩 */ }
  };
  await page.route('**/api/v1/signals**', proxyTo);
  await page.route('**/api/v1/paper/**', proxyTo);

  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  await page.goto(PROD_WEB + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 60000 });
  await page.waitForSelector('[data-testid="signal-row"]', { timeout: 90000 });

  const readCards = () =>
    page.$$eval('[data-testid="signal-row"]', (nodes) =>
      nodes.map((n) => ({
        uid: n.getAttribute('data-signal-uid'),
        symbol: n.getAttribute('data-symbol'),
        direction: n.getAttribute('data-direction'),
        confirmed_at: n.getAttribute('data-confirmed-at'),
        exec: n.getAttribute('data-executable-state'),
        origin: n.getAttribute('data-origin'),
        filled: n.getAttribute('data-filled'),
        text: (n.textContent || '').replace(/\s+/g, ' ').slice(0, 200),
      })),
    );
  const panelState = () =>
    page.$eval('[data-testid="signal-panel"]', (n) => n.getAttribute('data-signals-state'));

  const cards = await readCards();
  result.page_cards_initial = cards;
  result.panel_state_initial = await panelState();

  step('页面状态 = ok（不是 loading / fault）',
    result.panel_state_initial === 'ok', `data-signals-state=${result.panel_state_initial}`);
  step('页面上 1 张卡', cards.length === 1, `${cards.length} 张`);
  step('卡片 ID == 接口 ID == 期望候选 ID',
    cards[0]?.uid === CAND && it.signal_id === CAND, cards[0]?.uid || '(无)');
  step('卡片四项字段与接口一致（币种/方向/确认时间/可执行状态）',
    cards[0]?.symbol === it.symbol &&
    cards[0]?.direction === it.direction &&
    toMs(cards[0]?.confirmed_at) === toMs(it.confirmed_at) &&
    cards[0]?.exec === it.executable_state,
    `页面 {${cards[0]?.symbol}, ${cards[0]?.direction}, ${cards[0]?.confirmed_at}, ` +
    `${cards[0]?.exec}} vs 接口 {${it.symbol}, ${it.direction}, ${it.confirmed_at}, ` +
    `${it.executable_state}}`);
  step('卡片四项字段与期望一致',
    cards[0]?.symbol === EXPECT.symbol && cards[0]?.direction === EXPECT.direction &&
    toMs(cards[0]?.confirmed_at) === EXPECT.confirmed_at &&
    cards[0]?.exec === EXPECT.executable_state,
    `${EXPECT.symbol}/${EXPECT.direction}/${EXPECT.confirmed_at}/` +
    `${EXPECT.executable_state}，页面确认时间=${toMs(cards[0]?.confirmed_at)}`);
  step('卡片上「做多」标签可见', /做多/.test(cards[0]?.text || ''),
    (cards[0]?.text || '').slice(0, 60));
  step('未落回空态（没有 signal-empty）',
    !(await page.$('[data-testid="signal-empty"]')), '无空态块');
  step('未显示错误框（没有 signal-error）',
    !(await page.$('[data-testid="signal-error"]')), '无错误框');
  step('页面确实走隔离出口',
    routed.some((u) => u.includes(`:${API_PORT}/api/v1/signals`)),
    routed.filter((u) => u.includes('/signals')).slice(0, 1).join('') || '未捕获');

  const shot = `50_page_${OUT_SUFFIX}.png`;
  await page.screenshot({ path: resolve(OUT, shot) });
  step('截图已保存', true, 'out/' + shot);

  // ── 4. 刷新：不丢不重 ─────────────────────────────────────────────────────
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="signal-row"]', { timeout: 90000 });
  const cardsReload = await readCards();
  result.page_cards_after_reload = cardsReload;
  step('刷新后仍是 1 张卡、同一 ID、字段不变（不丢不重）',
    cardsReload.length === 1 && cardsReload[0]?.uid === cards[0]?.uid &&
    cardsReload[0]?.direction === cards[0]?.direction &&
    cardsReload[0]?.confirmed_at === cards[0]?.confirmed_at &&
    cardsReload[0]?.exec === cards[0]?.exec,
    `${cardsReload.length} 张，uid=${cardsReload[0]?.uid}`);

  // ── 5. 轮询：等 2 个 5s 周期，靠页面自己的定时器刷新（不 reload）──────────
  const routedBeforePoll = routed.length;
  await sleep(12000);
  const cardsPoll = await readCards();
  result.page_cards_after_poll = cardsPoll;
  result.poll_requests = routed.length - routedBeforePoll;
  step('轮询期间确实重新请求了接口',
    routed.length - routedBeforePoll >= 1, `新增 ${routed.length - routedBeforePoll} 次请求`);
  step('两轮轮询后仍是 1 张卡、同一 ID、字段不变（不丢不重）',
    cardsPoll.length === 1 && cardsPoll[0]?.uid === cards[0]?.uid &&
    cardsPoll[0]?.direction === cards[0]?.direction &&
    cardsPoll[0]?.confirmed_at === cards[0]?.confirmed_at &&
    cardsPoll[0]?.exec === cards[0]?.exec,
    `${cardsPoll.length} 张，uid=${cardsPoll[0]?.uid}`);
  step('页面无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | ') || '无');
} catch (e) {
  step('异常中止', false, String(e && e.message ? e.message : e));
} finally {
  if (browser) {
    try {
      for (const p of browser.contexts()) {
        for (const pg of p.pages()) await pg.unrouteAll({ behavior: 'ignoreErrors' });
      }
    } catch { /* ignore */ }
    await browser.close().catch(() => {});
  }
  for (const cp of children) { try { cp.kill('SIGTERM'); } catch { /* ignore */ } }
  await sleep(1200);
  for (const cp of children) {
    try { if (cp.exitCode === null) cp.kill('SIGKILL'); } catch { /* ignore */ }
  }
}

// ── 6. 库层 + 三方对撞 + 指纹 ───────────────────────────────────────────────
const db = await readDb();
result.db = db;
step('库里 1 行、同一候选 ID', db.n === 1 && db.rows[0]?.signal_uid === CAND, `${db.n} 行`);
step('库四项字段与页面一致',
  db.rows[0]?.symbol === result.page_cards_initial?.[0]?.symbol &&
  db.rows[0]?.direction === result.page_cards_initial?.[0]?.direction &&
  toMs(db.rows[0]?.confirmed_at) === toMs(result.page_cards_initial?.[0]?.confirmed_at) &&
  db.rows[0]?.executable_state === result.page_cards_initial?.[0]?.exec,
  `库 {${db.rows[0]?.symbol}, ${db.rows[0]?.direction}, ${db.rows[0]?.confirmed_at}, ` +
  `${db.rows[0]?.executable_state}} = ` +
  `${toMs(db.rows[0]?.confirmed_at)}（页面 ${toMs(result.page_cards_initial?.[0]?.confirmed_at)}）`);
step('库 origin 与路径一致', db.rows[0]?.origin === MODE, `origin=${db.rows[0]?.origin}`);
step('三方（库 / 接口 / 页面）按同一 ID 对齐',
  db.rows[0]?.signal_uid === result.iso_api?.item?.signal_id &&
  result.iso_api?.item?.signal_id === result.page_cards_initial?.[0]?.uid,
  '三处同一 ID');

const after = {
  prod_live_db_sha256: await sha256(PROD_LIVE_DB),
  live_store_py_sha256: await sha256(PROD_STORE),
  iso_live_db_sha256: await sha256(ISO_DB),
};
result.fingerprint_after = after;
step('生产实时库未被写入',
  before.prod_live_db_sha256 === after.prod_live_db_sha256,
  `${before.prod_live_db_sha256.slice(0, 12)}… → ${after.prod_live_db_sha256.slice(0, 12)}…`);
step('启用时点常量所在文件未被改写',
  before.live_store_py_sha256 === after.live_store_py_sha256,
  `live_store.py sha ${before.live_store_py_sha256.slice(0, 12)}… 一致`);
step('隔离库未被本探针改写（只读打开）',
  before.iso_live_db_sha256 === after.iso_live_db_sha256,
  before.iso_live_db_sha256 === after.iso_live_db_sha256 ? 'sha 一致' : 'sha 变化');

result.api_log_tail = apiLog;
result.ok = result.steps.filter((s) => !s.soft).every((s) => s.ok);
const outJson = `50_two_paths_page_${OUT_SUFFIX}.json`;
writeFileSync(resolve(OUT, outJson), JSON.stringify(result, null, 2), 'utf8');
console.log('\n== 结果：' + (result.ok ? '全部通过' : '存在失败项') + ' ==');
console.log(`写出：evidence/SIGNAL-UI-LINK-07-R1/out/${outJson}`);
process.exit(result.ok ? 0 : 1);
