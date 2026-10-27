/**
 * 08｜在**真实前端页面**上点「导出」按钮，把浏览器实际下载到的 CSV 落盘核对。
 *
 * 为什么必须有这一步：07 只证明了「导出所依赖的端点能取到全列数据」，
 * 但工单要的是「通过**实际导出入口**下载文件」——前端的 CSV 是浏览器内
 * `new Blob` 生成的（`BacktestWorkbench.tsx:1863-1873`），只有真的点一次按钮、
 * 真的拿到 `download` 事件，才算走了那个入口。
 *
 * 隔离方式（都用既有开关，不改被测代码）：
 *   · 后端：PORT=8799 + EVENT_TREND_RUNS_DIR/MARKET_DB/PAPER_* 全指向本单 out/
 *   · 前端：`API_PROXY_TARGET`（vite.config.ts 已支持）+ `VITE_CACHE_DIR` 独立缓存
 *           ⇒ 第二实例 3001，不与生产 dev 实例（3000）争缓存、不碰 8787
 *
 * 深链：`/?run=<run_id>` 是 App.tsx:57-76 已有的确定性入口。
 */
import { createRequire } from 'module';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

const BASE = '/Users/boos/Desktop/威科夫因子共振趋势';
const ME = path.join(BASE, 'evidence/SIGNAL-ORDER-TRACE-04');
const OUT = path.join(ME, 'out');
const DL = path.join(OUT, '08_ui_download');
const WEB = path.join(BASE, 'UI界面/web');
const PY = '/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python';
const NODE = '/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node';

const BACKEND_PORT = 8799;
const FRONT_PORT = 3001;

const require = createRequire(path.join(WEB, 'package.json'));
const { chromium } = require('playwright');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function httpOk(url, timeoutMs = 3000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    return r.status;
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function waitUp(url, label, tries = 240, gap = 500) {
  for (let i = 0; i < tries; i++) {
    const s = await httpOk(url);
    if (s === 200) return true;
    await sleep(gap);
  }
  throw new Error(`${label} 未在 ${(tries * gap) / 1000}s 内就绪：${url}`);
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function csvRows(p) {
  const txt = fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, '');
  const lines = txt.split(/\r?\n/).filter((l) => l.trim().length);
  if (!lines.length) return { header: [], rows: 0, sample: null };
  const header = lines[0].split(',');
  return { header, rows: lines.length - 1, sample: lines[1] || null };
}

async function main() {
  fs.mkdirSync(DL, { recursive: true });
  const r07 = readJson(path.join(OUT, '07_导出核验.json'));
  const env07 = r07.backend.env_subset;
  // 抽样：优先挑多笔 run（订单/成交行数 > 2，才能验证「多行」也能导出）
  const r06 = readJson(path.join(OUT, '06_工作台注册.json'));
  const multi = r06.runs.filter((r) => r.n_trades > 1).map((r) => r.run_id);
  const single = r06.runs.filter((r) => r.n_trades === 1).map((r) => r.run_id);
  const runId = multi[0] || single[0];
  if (!runId) throw new Error('06 报告里没有可用的 run_id');

  const children = [];
  const report = {
    generated_at_utc: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    run_id: runId,
    ports: { backend: BACKEND_PORT, frontend: FRONT_PORT },
    isolation: { api_proxy_target: `http://127.0.0.1:${BACKEND_PORT}`,
                 vite_cache_dir: 'node_modules/.vite-sot04' },
    steps: [], downloads: [], errors: [],
  };

  try {
    // ── 1. 后端（隔离）────────────────────────────────────────────────
    const benv = { ...process.env, ...env07, PORT: String(BACKEND_PORT),
                   NO_PROXY: '127.0.0.1,localhost,::1',
                   no_proxy: '127.0.0.1,localhost,::1' };
    for (const k of ['HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy']) delete benv[k];
    const blog = fs.openSync(path.join(DL, 'backend_8799.log'), 'w');
    const be = spawn(PY, [path.join(BASE, '框架/backend/adapter.py')],
                     { cwd: BASE, env: benv, stdio: ['ignore', blog, blog] });
    children.push(be);
    report.steps.push({ step: 'backend_spawn', pid: be.pid });

    // ── 2. 前端第二实例（独立缓存 + 代理到 8799）──────────────────────
    const fenv = { ...process.env, API_PROXY_TARGET: `http://127.0.0.1:${BACKEND_PORT}`,
                   VITE_CACHE_DIR: 'node_modules/.vite-sot04', DISABLE_HMR: 'true' };
    const flog = fs.openSync(path.join(DL, 'vite_3001.log'), 'w');
    const fe = spawn(path.join(WEB, 'node_modules/.bin/vite'),
                     ['--port', String(FRONT_PORT), '--strictPort'],
                     { cwd: WEB, env: fenv, stdio: ['ignore', flog, flog] });
    children.push(fe);
    report.steps.push({ step: 'frontend_spawn', pid: fe.pid });

    await waitUp(`http://127.0.0.1:${BACKEND_PORT}/api/health`, 'backend');
    report.steps.push({ step: 'backend_ready', at: new Date().toISOString() });
    await waitUp(`http://127.0.0.1:${FRONT_PORT}/`, 'frontend');
    report.steps.push({ step: 'frontend_ready', at: new Date().toISOString() });

    // ── 3. 浏览器：深链 → 点导出 ───────────────────────────────────────
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ acceptDownloads: true,
                                           viewport: { width: 1600, height: 1100 } });
    const page = await ctx.newPage();
    const consoleErrors = [];
    const badResponses = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
    // 4xx/5xx 要记到**具体 URL**：只记「有个 500」在证据里等于没记
    page.on('response', (r) => {
      if (r.status() >= 400) badResponses.push({ status: r.status(), url: r.url() });
    });

    const url = `http://127.0.0.1:${FRONT_PORT}/?run=${encodeURIComponent(runId)}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
    report.steps.push({ step: 'navigate', url });

    await page.waitForSelector('[data-testid="lab-trades"]', { timeout: 120000 });
    report.steps.push({ step: 'trades_panel_visible', at: new Date().toISOString() });

    // 页面自报的 run_id 与逐笔行数（证明「打开的是这个 run」）
    // 注意：`lab-trades` 卡片出现 ≠ 数据已到 —— 首帧 chip 会是「本 run 共 0 笔」。
    // 必须等 chip 变成非 0 再取页面事实，否则抓到的是空态。
    const waitChip = async (ms) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        const txt = await page.innerText('body');
        const m = txt.match(/本 run 共\s*(\d+)\s*笔/);
        if (m && Number(m[1]) > 0) return Number(m[1]);
        await sleep(400);
      }
      return null;
    };
    const chipCount = await waitChip(60000);
    report.steps.push({ step: 'trades_chip_ready', count: chipCount });

    // K 线：等 canvas 出现（`_generate_fallback_chart` 现读 parquet，可能要十几秒）
    let canvasCount = 0;
    try {
      await page.waitForSelector('canvas', { timeout: 120000 });
      canvasCount = await page.locator('canvas').count();
    } catch { /* 记录为 0，不伪造 */ }
    report.steps.push({ step: 'kline_canvas', count: canvasCount });

    const bodyText = await page.innerText('body');
    report.page_facts = {
      run_id_in_page: bodyText.includes(runId),
      symbol_in_page: bodyText.includes('NEAR') || /USDT/.test(bodyText),
      has_kline_canvas: canvasCount > 0,
      canvas_count: canvasCount,
      trades_chip: (bodyText.match(/本 run 共\s*\d+\s*笔/) || [null])[0],
      trades_chip_count: chipCount,
      text_len: bodyText.length,
      text_head: bodyText.slice(0, 600),
    };
    await page.screenshot({ path: path.join(DL, '00_overview.png'), fullPage: true });

    // ── 页面数字 vs 本 run 的 summary.json（独立对账，不靠肉眼）──────────
    // 页面显示的是标的 + 运行时间，不打印 raw run_id ⇒ 用「数字对得上」来证明
    // 打开的就是这个 run，而不是靠字符串包含。
    const runDir = path.join(OUT, 'iso_runs', runId);
    const sum = readJson(path.join(runDir, 'summary.json'));
    const eq0 = fs.readFileSync(path.join(runDir, 'equity_curve.jsonl'), 'utf8')
      .split('\n').filter((l) => l.trim())[0];
    const base = Number(JSON.parse(eq0).cash);
    const grab1 = (re) => { const m = bodyText.match(re); return m ? Number(m[1]) : null; };
    const pageNetPct = grab1(/净收益\s*([+-]?[\d.]+)\s*%/);
    const pageTrades = grab1(/成交笔数\s*(\d+)/);
    const pageFees = grab1(/手续费\s*\/\s*资金费：([\d.]+)\s*\//);
    const pageFunding = grab1(/手续费\s*\/\s*资金费：[\d.]+\s*\/\s*([\d.]+)/);
    const expNetPct = Math.round((sum.net_pnl / base) * 10000) / 100;
    report.page_vs_summary = {
      base_equity_from_equity_curve: base,
      net_pct: { page: pageNetPct, expected: expNetPct,
                 match: pageNetPct !== null && Math.abs(pageNetPct - expNetPct) <= 0.01 },
      n_trades: { page: pageTrades, expected: sum.n_trades, match: pageTrades === sum.n_trades },
      fees: { page: pageFees, expected: Math.round(sum.fees * 1e4) / 1e4,
              match: pageFees !== null && Math.abs(pageFees - sum.fees) <= 1e-4 },
      funding: { page: pageFunding, expected: Math.round(sum.funding_fee * 1e4) / 1e4,
                 match: pageFunding !== null
                        && Math.abs(pageFunding - sum.funding_fee) <= 1e-4 },
    };
    report.page_vs_summary.all_match = Object.values(report.page_vs_summary)
      .filter((v) => v && typeof v === 'object' && 'match' in v)
      .every((v) => v.match === true);

    const grab = async (selector, filename, label) => {
      const btn = page.locator(selector);
      await btn.waitFor({ state: 'visible', timeout: 60000 });
      const [dl] = await Promise.all([
        page.waitForEvent('download', { timeout: 60000 }),
        btn.click(),
      ]);
      const p = path.join(DL, filename);
      await dl.saveAs(p);
      const meta = csvRows(p);
      report.downloads.push({
        label, selector,
        suggested_filename: dl.suggestedFilename(),
        saved_as: p, bytes: fs.statSync(p).size,
        header: meta.header, rows: meta.rows, sample: meta.sample,
      });
      return meta;
    };

    // ① 逐笔 CSV（在「单条记录回放」卡片右上）
    await grab('[data-testid="lab-trades"] button:has-text("导出逐笔 CSV")',
               `trades_${runId}.csv`, '逐笔交易');
    await page.screenshot({ path: path.join(DL, '01_trades_panel.png'), fullPage: false });

    // ② 订单 CSV（先展开「更多视图 → 委托订单」）
    await page.click('[data-testid="lab-panel-orders"]');
    await page.waitForSelector('[data-testid="lab-orders"]', { timeout: 60000 });
    await grab('[data-testid="lab-orders"] button:has-text("导出订单 CSV")',
               `orders_${runId}.csv`, '委托订单');
    await page.screenshot({ path: path.join(DL, '02_orders_panel.png'), fullPage: false });

    // ③ 成交 CSV（展开「更多视图 → 逐次成交」）
    await page.click('[data-testid="lab-panel-fills"]');
    await page.waitForSelector('[data-testid="lab-fills"]', { timeout: 60000 });
    await grab('[data-testid="lab-fills"] button:has-text("导出成交 CSV")',
               `fills_${runId}.csv`, '逐次成交');
    await page.screenshot({ path: path.join(DL, '03_fills_panel.png'), fullPage: false });

    // ④ 订单定位：点第一行逐笔 ⇒ 执行视图应定位该笔
    const firstRow = page.locator('[data-testid="lab-trades"] tbody tr').first();
    if (await firstRow.count()) {
      await firstRow.click();
      await sleep(1200);
      const after = await page.innerText('body');
      report.order_locate = {
        clicked: true,
        focused_trade_id_shown: /trd_paper_pos_\d+/.test(after),
        matched_trade_ids: Array.from(new Set(after.match(/trd_paper_pos_\d+/g) || [])),
      };
      await page.screenshot({ path: path.join(DL, '04_trade_located.png') });
    } else {
      report.order_locate = { clicked: false, reason: '逐笔表无行' };
    }

    report.console_errors = consoleErrors.slice(0, 20);
    report.bad_responses = badResponses.slice(0, 40);
    await browser.close();
    report.steps.push({ step: 'done', at: new Date().toISOString() });
  } catch (e) {
    report.errors.push(`${e && e.stack ? e.stack : e}`);
  } finally {
    for (const c of children) {
      try { c.kill('SIGTERM'); } catch { /* ignore */ }
    }
    await sleep(1200);
    for (const c of children) {
      try { if (c.exitCode === null) c.kill('SIGKILL'); } catch { /* ignore */ }
    }
    fs.writeFileSync(path.join(OUT, '08_UI导出核验.json'),
                     JSON.stringify(report, null, 2), 'utf8');
    console.log(JSON.stringify({
      run_id: report.run_id,
      downloads: report.downloads.map((d) => ({ label: d.label, file: d.saved_as,
                                                rows: d.rows, bytes: d.bytes })),
      page_facts: report.page_facts,
      page_vs_summary: report.page_vs_summary,
      order_locate: report.order_locate,
      bad_responses: report.bad_responses,
      errors: report.errors,
    }, null, 2));
  }
}

main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
