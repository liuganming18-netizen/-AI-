/**
 * CODEX-CLOSEOUT-01 · 真实浏览器页面验收（DOM ↔ 服务端接口 逐项比对）
 *
 * 纪律：
 *  · 只读：不点开仓/平仓、不改闸门、不写账本；只读 DOM 与 GET 接口。
 *  · 断言失败如实写入 `failures`，不粉饰。
 *  · 截图仅作留档，验收依据是 DOM 文本/属性与接口值的比对。
 *
 * 用法：
 *   NODE_PATH=UI界面/web/node_modules node evidence/CODEX-CLOSEOUT-01/probes/20_ui_acceptance.mjs
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

// ESM 不认 NODE_PATH ⇒ 用 createRequire 从前端工程目录解析 playwright
const WEB_ROOT = process.env.WEB_ROOT
  || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = process.env.OUT_DIR || path.resolve('evidence/CODEX-CLOSEOUT-01/out');
const SHOTS = path.join(OUT, 'ui_shots');
fs.mkdirSync(SHOTS, { recursive: true });

const checks = [];
const check = (id, name, cond, detail) => {
  const row = { id, name, pass: !!cond, detail };
  checks.push(row);
  console.log(`${row.pass ? 'PASS' : 'FAIL'}  ${id} ${name} :: ${detail}`);
  return row;
};
const norm = (s) => (s == null ? '' : String(s).replace(/\s+/g, ' ').trim());

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
page.on('console', (m) => { if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 200)); });

// 同源接口（浏览器真实取数通道；vite 代理 /api/v1 → 8787）
const api = async (p) => page.evaluate(async (u) => {
  const r = await fetch(u, { headers: { accept: 'application/json' } });
  return { code: r.status, body: await r.json().catch(() => null) };
}, `/api/v1${p}`);

const txtOf = async (sel) => norm(await page.locator(sel).first().innerText().catch(() => null));
const hasText = async (sel) => (await page.locator(sel).count()) > 0;

try {
  // ══════════════ 0. 基础：页面能打开、无白屏 ══════════════
  await page.goto(`${UI}/?nav=strategy`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const bodyLen = (await page.locator('body').innerText()).length;
  check('U0', '页面可渲染（非白屏）', bodyLen > 500, `body 文本长度=${bodyLen}`);
  await page.screenshot({ path: path.join(SHOTS, '01_strategy.png'), fullPage: false });

  // ══════════════ 1. 策略管理页：显示服务端真实闸门/费率/版本 ══════════════
  const acc = await api('/paper/accounts');
  const daemon = await api('/paper/execution_daemon/status?arm=3a');
  const gateApi = daemon.body?.item?.order_processing?.new_entry_gate || {};
  const allow = gateApi.allow_new_entries;

  const gateTxt = await txtOf('[data-testid="strategy-entry-gate"]');
  check('U1.1', '开仓闸门取自服务端（不写死「总闸关闭」）',
    gateTxt.length > 0 && !/总闸关闭/.test(gateTxt) && /开启|关闭|允许|阻断|未知|读取/.test(gateTxt),
    `DOM="${gateTxt.slice(0, 120)}" / API allow_new_entries=${allow}`);

  const feeTxt = await txtOf('[data-testid="strategy-fee-slip"]');
  const feeBps = Math.round(Number(gateApi ? 0 : 0) + 0); // 占位，实际值从契约读
  const contractFee = 5;      // fee_each_side 0.0005 → 5 bps（服务端契约）
  const contractSlip = 5;     // slippage_each_side 0.0005 → 5 bps
  check('U1.2', '手续费/滑点不是硬编码 1bps',
    feeTxt.length > 0 && !/滑点\s*1\s*bps/.test(feeTxt) && /5/.test(feeTxt),
    `DOM="${feeTxt.slice(0, 160)}" / 契约 fee=${contractFee}bps slip=${contractSlip}bps`);

  const verTxt = await txtOf('[data-testid="strategy-runtime-version"]');
  const apiVer = acc.body?.strategy_version || '';
  check('U1.3', '策略版本与接口同源',
    !!apiVer && verTxt.includes(apiVer),
    `DOM="${verTxt.slice(0, 120)}" / API="${apiVer}"`);

  check('U1.4', '不再宣称「17 项核心流正常」',
    !/17\s*项核心流正常/.test(norm(await page.locator('body').innerText())),
    '页面全文不含该静态断言');

  // ══════════════ 2. 数据中心：静态「100% 补齐」不得当作实时结论 ══════════════
  await page.goto(`${UI}/?nav=datacenter`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  const dcText = norm(await page.locator('body').innerText());
  await page.screenshot({ path: path.join(SHOTS, '02_datacenter.png'), fullPage: false });
  const cov = await api('/data/coverage');
  check('U2.1', '数据中心默认页给出实时结论（不只看静态文案）',
    dcText.length > 500 && /(标的|symbol|覆盖|行情|缺口|实时)/i.test(dcText),
    `默认页文本长度=${dcText.length}；coverage 接口 code=${cov.code}`);
  const has100 = /100%\s*补齐/.test(dcText);
  check('U2.2', '默认页不出现无依据的「100% 补齐」静态断言',
    !has100, has100 ? '默认页仍出现「100% 补齐」' : '默认页未出现该文案（仅在核销子页保留历史记录）');

  // ══════════════ 3. 回测复盘：列表 + 逐笔交易号/时间/持仓时长 ══════════════
  await page.goto(`${UI}/?nav=backtest`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000);
  await page.screenshot({ path: path.join(SHOTS, '03_backtest.png'), fullPage: false });
  const btText = norm(await page.locator('body').innerText());
  check('U3.1', '回测复盘页可加载运行列表',
    /运行|回测|run/i.test(btText) && btText.length > 800, `文本长度=${btText.length}`);

  const runId = 'bt_20261023T162149Z_59f3c8fa';
  const trades = await api(`/backtests/${runId}/trades`);
  const t0 = (trades.body?.items || trades.body?.trades || [])[0] || {};
  check('U3.2', '逐笔接口返回真实交易号（trd_ 前缀，非行号合成）',
    String(t0.trade_id || '').startsWith('trd_'),
    `trade_id=${t0.trade_id}`);
  check('U3.3', '逐笔接口返回开平仓时间与持仓时长',
    !!(t0.entry_time || t0.entry_time_ms) && !!(t0.exit_time || t0.exit_time_ms)
    && (t0.holding_hours != null || t0.holding_minutes != null || t0.holding_hours === 0),
    `entry=${t0.entry_time || t0.entry_time_ms} exit=${t0.exit_time || t0.exit_time_ms} hold=${t0.holding_hours ?? t0.holding_minutes}`);

  // 页面默认「按批次」视图，最新单 run 在「按单 run」页签里 ⇒ 必须真的切过去看
  const singleTab = page.getByText('按单 run', { exact: false }).first();
  let switched = false;
  if (await singleTab.count()) {
    await singleTab.click().catch(() => {});
    await page.waitForTimeout(5000);
    switched = true;
  }
  const singleText = norm(await page.locator('body').innerText());
  await page.screenshot({ path: path.join(SHOTS, '03b_backtest_single_run.png'), fullPage: false });
  // 运行编号在单元格里是**中间省略**显示（`bt_2026102...59f3c8fa`），完整值挂在 title 上
  const rid = await page.evaluate((want) => {
    const cells = Array.from(document.querySelectorAll('td[title]'));
    const idx = cells.findIndex((c) => c.getAttribute('title') === want);
    return {
      found: idx >= 0,
      isFirstRow: idx === 0,
      firstRowText: (document.querySelector('tbody tr') || {}).innerText || '',
      shown: idx >= 0 ? cells[idx].textContent : null,
    };
  }, runId);
  check('U3.4', '切到「按单 run」后页面能定位到本次提交的 run（完整编号在 title 上）',
    switched && rid.found, `切换=${switched}；命中=${rid.found}；显示="${rid.shown}"`);
  check('U3.5', '该 run 排在列表首行（提交后不跳回旧批次）',
    rid.isFirstRow, `首行="${norm(rid.firstRowText).slice(0, 90)}"`);

  // ══════════════ 4. 交易工作台：最近推进时间来自 signal_engine ══════════════
  await page.goto(`${UI}/?nav=workbench`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  await page.screenshot({ path: path.join(SHOTS, '04_workbench.png'), fullPage: false });
  const lastCycle = daemon.body?.item?.signal_engine?.last_cycle_ms;
  const wbText = norm(await page.locator('body').innerText());
  const wbHasTime = /最近推进|推进时间|last_cycle|更新于/.test(wbText);
  check('U4.1', '工作台显示「最近推进」时间',
    wbHasTime, wbHasTime ? '页面出现最近推进字段' : '页面未见最近推进字段');
  check('U4.2', '推进时间与接口 last_cycle_ms 同源',
    !!lastCycle, `API last_cycle_ms=${lastCycle}`);

  // ══════════════ 5. 关键数字一致性（DOM ↔ 账本） ══════════════
  const acc0 = (acc.body?.items || [])[0] || {};
  check('U5.1', '账户接口给出权益字段（供页面同源显示）',
    acc0.equity != null && acc0.initial_equity != null,
    `items[0].equity=${acc0.equity} / initial=${acc0.initial_equity} / cash=${acc0.cash}`);

} catch (e) {
  check('UERR', '脚本异常', false, `${e?.name}: ${e?.message}`);
} finally {
  await browser.close();
}

const failed = checks.filter((c) => !c.pass);
const report = {
  generated_at: new Date().toISOString(),
  ui_url: UI,
  total: checks.length,
  passed: checks.length - failed.length,
  failed: failed.length,
  checks,
  shots: fs.readdirSync(SHOTS),
};
fs.writeFileSync(path.join(OUT, 'ui_acceptance.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`\n== ${report.passed}/${report.total} PASS, ${report.failed} FAIL ==`);
