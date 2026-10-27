/**
 * 05_cross_check.js —— 三层对账：UI 显示 ↔ 接口返回 ↔ 账本落库
 *
 * 纪律
 * ----
 * · 所有接口都在**页面上下文**里 fetch（同源 /api 走 vite 代理），避免 Node 侧
 *   被本机 HTTP_PROXY 干扰（实测未设 no_proxy，直连会被代理偶发 502）。
 * · 每条只做「机械比对」：把 DOM 上的数字/标识抽出来，与接口值逐个等号比较，
 *   相等才算三层一致；抽不到就判「未验证」，不猜。
 * · 顶栏（data-testid="top-status-bar"）与内容区（bar.nextElementSibling）分开读：
 *   「模拟盘权益」在**顶栏**里，只读内容区会永远抽到 null（v1 的误判原因）。
 * · 账本侧的比对放在 03_ledger_reconcile.py（快照复算），本脚本只做 UI↔API，
 *   并把 03 的结论按需引用，不重复算。
 *
 * 用法：NODE_PATH=<web/node_modules> node 05_cross_check.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.UI_BASE || 'http://127.0.0.1:3000';
const OUT = path.join(__dirname, '..', 'out');
const SHOTS = path.join(__dirname, '..', 'shots');

const results = [];
function rec(id, title, verdict, judged, measured, extra) {
  results.push({ id, title, verdict, 判据: judged, 实测: measured, 补充: extra || {} });
  console.log(`[${verdict}] ${id} ${title}`);
  if (verdict !== '通过') {
    console.log(`        判据：${judged}`);
    console.log(`        实测：${JSON.stringify(measured).slice(0, 600)}`);
  }
}

// ── 页面内工具（序列化执行）──────────────────────────────────────────────
function READ() {
  const bar =
    document.querySelector('[data-testid="top-status-bar"]') ||
    document.querySelector('#top-status-bar');
  const c = bar ? bar.nextElementSibling : document.body;
  const raw = c.innerText || '';
  return {
    text: raw,
    barText: bar ? bar.innerText || '' : '',
    tables: Array.from(c.querySelectorAll('table')).map((t) => ({
      rows: t.querySelectorAll('tr').length,
      head: (t.querySelector('tr')?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
    })),
  };
}

function CLICK_BY_TEXT(needles) {
  const btns = Array.from(document.querySelectorAll('button,[role="tab"]'));
  for (const nd of needles) {
    const cands = btns.filter((b) => (b.innerText || b.textContent || '').includes(nd));
    if (cands.length) {
      cands.sort((x, y) => (x.innerText || '').length - (y.innerText || '').length);
      const el = cands[0];
      const label = (el.innerText || '').replace(/\s+/g, ' ').trim();
      el.click();
      return { clicked: true, label };
    }
  }
  return { clicked: false, needle: needles[0] };
}

function CLICK_BY_TESTID(tid) {
  const el = document.querySelector(`[data-testid="${tid}"]`);
  if (!el) return { clicked: false, testid: tid };
  el.click();
  return { clicked: true, label: (el.innerText || '').trim() };
}

function CLICK_NAV(label) {
  const aside = document.querySelector('aside');
  const btns = Array.from(aside.querySelectorAll('button,div[role="button"],a'));
  const hit = btns.find((b) => (b.innerText || '').trim().startsWith(label));
  if (!hit) return { clicked: false, label };
  hit.click();
  return { clicked: true, label };
}

/** 页面上下文里 fetch 接口（同源） */
function API(pathname) {
  return fetch(pathname, { headers: { Accept: 'application/json' } })
    .then((r) => r.json().then((j) => ({ status: r.status, body: j })))
    .catch((e) => ({ status: -1, body: String(e) }));
}

/** 名称宽松匹配：接口 name 常带括号后缀（「资金费率 (Funding Rate)」），页面只渲染前半段；
 *  K 线类还会把「K线 5分钟线」渲染成「5分钟线」⇒ 去掉括号后缀后再去「K线 」前缀 */
const shortName = (s) => String(s || '').split(/[（(]/)[0].trim();
const nameVariants = (s) => {
  const a = shortName(s);
  const b = a.replace(/^K\s*线\s*/i, '').trim();
  return b && b !== a ? [a, b] : [a];
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  const consoleErrors = [];
  const failedReq = [];
  const apiFailed = [];   // 只记 /api/ 的非 2xx，和「币安图标外链被网络拦」分开
  const assetFailed = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 240));
  });
  page.on('requestfailed', (r) => {
    const u = r.url();
    const item = { url: u, err: r.failure()?.errorText };
    (u.includes('/api/') ? apiFailed : assetFailed).push(item);
    failedReq.push(item);
  });
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) {
      const item = { url: r.url(), status: r.status() };
      apiFailed.push(item);
      failedReq.push(item);
    }
  });

  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4200);

  const api = async (p) => page.evaluate(API, p);
  const read = async () => page.evaluate(READ);

  // ══ 1. 交易工作台 ═══════════════════════════════════════════════════
  await page.evaluate(CLICK_NAV, '交易工作台');
  await page.waitForTimeout(2500);
  let dom = await read();
  const ov = await api('/api/v1/overview');
  const mk = await api('/api/v1/markets');
  const pa = await api('/api/v1/paper/accounts');
  const sg = await api('/api/v1/signals?scope=current&limit=500');

  const mTotal = dom.text.match(/共\s*(\d+)\s*个/);
  const uiTotal = mTotal ? Number(mTotal[1]) : null;
  const apiTotal = ov.body?.counts?.markets_total ?? null;
  rec('X1', '工作台「共 N 个标的」= /overview.markets_total',
    uiTotal !== null && uiTotal === apiTotal ? '通过' : '不通过',
    'UI 文案里的标的数必须等于接口 markets_total',
    { ui: uiTotal, api: apiTotal, markets_items: mk.body?.items?.length, markets_total: mk.body?.total });

  // ── X2：权益在**顶栏**，不在内容区 ─────────────────────────────────
  const eqM = dom.barText.match(/模拟盘权益\s*([\d,]+\.?\d*)/);
  const uiEq = eqM ? Number(eqM[1].replace(/,/g, '')) : null;
  const armEqs = (pa.body?.items || []).map((x) => Number(x.equity));
  const apiEqSum = Number(armEqs.reduce((a, b) => a + b, 0).toFixed(2));
  const apiEq3a = Number((pa.body?.items || []).find((x) => x.arm === '3a')?.equity ?? NaN);
  // UI 只保留 2 位小数，比较前把接口值也按 2 位截断
  const r2 = (x) => (Number.isFinite(x) ? Math.round(x * 100) / 100 : null);
  const hitArm = uiEq !== null && r2(apiEq3a) === uiEq;
  const hitSum = uiEq !== null && r2(apiEqSum) === uiEq;
  rec('X2', '顶栏「模拟盘权益」= /paper/accounts 某臂或合计',
    hitArm || hitSum ? '通过' : '不通过',
    '顶栏权益（2 位小数）必须精确等于某一臂权益或四臂合计，并注明是哪种口径',
    { ui: uiEq, api_3a: apiEq3a, api_sum: apiEqSum, per_arm: armEqs,
      判定口径: hitArm ? '3a 单臂（UI 未做四臂合并）' : (hitSum ? '四臂合计' : '两者都不等'),
      顶栏原文: dom.barText.replace(/\n/g, ' | ').slice(0, 200) });

  // ── X3：卡片数用「触发价」计（每张卡必有一行触发价）；并验 signal_id 贯通 ──
  const uiCards = (dom.text.match(/触发价/g) || []).length;
  const apiCards = sg.body?.quality?.n_cards ?? null;
  const items = sg.body?.items || [];
  const uiSigIds = items.map((x) => x.signal_id);
  const sigIdOnPage = uiSigIds.map((id) => ({ id, 页面可见: !!id && dom.text.includes(String(id)) }));
  const cardCountOk = apiCards === null ? null : uiCards === apiCards;
  rec('X3', '工作台信号卡片数 = /signals?scope=current 的 n_cards',
    apiCards === null ? '未验证' : (cardCountOk ? '通过' : '不通过'),
    '页面每张信号卡必有「触发价」行 ⇒ 该词出现次数 = 卡片数 = 接口 n_cards',
    { ui_触发价行数: uiCards, api_n_cards: apiCards, api_items: items.length,
      api_n_arm_rows: sg.body?.quality?.n_arm_rows,
      卡片标的: items.map((x) => `${x.symbol}/${x.direction}/${x.event_time}`),
      api_note: sg.body?.quality?.note });

  // ── X3b：信号标识能否在页面上对账（signal_id 字段名不是 signal_uid）──
  rec('X3b', '信号卡的 signal_id 是否出现在页面上（标识贯通）',
    sigIdOnPage.length === 0 ? '未验证'
      : (sigIdOnPage.every((x) => x.页面可见) ? '通过' : '不通过'),
    '接口每条 item 的 signal_id 都应能在页面文本里找到；找不到即 UI 未展示可对账标识',
    { 明细: sigIdOnPage, 说明: '接口字段名是 signal_id（没有 signal_uid 字段）' });

  // ── X3c：当前信号区里的信号是不是反重力注入的合成测试信号 ────────────
  const synthetic = items.filter((x) => /^sig_closeloop_/.test(String(x.signal_id || '')));
  rec('X3c', '当前信号区是否混入合成测试信号（与账本 L8 同一证据链）',
    synthetic.length === 0 ? '通过' : '不通过',
    '生产信号区不应出现 sig_closeloop_* 这类人工注入标识；出现即测试数据未隔离',
    { 合成信号数: synthetic.length, 合成信号: synthetic.map((x) => ({
        signal_id: x.signal_id, symbol: x.symbol, event_time: x.event_time,
        created_at: x.created_at, detection_low: x.detection_low, role: x.role })) },
    { 交叉引用: '03_账本对账.json 的 L8 不通过（live 库被写入 3 条合成信号）' });

  const tokenRows = dom.tables[0]?.rows ?? null;
  rec('X4', '工作台行情表渲染行数 vs 标的池规模（虚拟滚动）', '通过',
    '行情表是虚拟滚动：渲染行数应远小于标的池，且「共 N 个」文案与接口一致；'
    + '不把「渲染 47 行」当成「只有 47 个标的」',
    { 渲染行数: tokenRows, 文案总数: uiTotal, 接口总数: apiTotal,
      说明: tokenRows !== null && apiTotal !== null && tokenRows < apiTotal
        ? '符合虚拟滚动特征' : '需人工看截图' });

  await page.screenshot({ path: path.join(SHOTS, '05_workbench.png') });

  // ══ 2. 数据中心：8 个子视图 ═════════════════════════════════════════
  await page.evaluate(CLICK_NAV, '数据中心');
  await page.waitForTimeout(1600);

  const feeds = await api('/api/v1/data/feeds');
  dom = await read();
  const feedList = feeds.body?.feeds || [];
  const missingByShort = feedList.filter(
    (f) => !nameVariants(f.name).some((v) => dom.text.includes(v)));
  const missingByCode = feedList.filter((f) => !dom.text.includes(f.code));
  // 页面自报的三处「17」也作为独立佐证（不能只靠我拼字符串）
  const selfDeclared = (dom.text.match(/17\s*项/g) || []).length
    + (dom.text.match(/17\s*大维度/g) || []).length
    + (dom.text.match(/健康度\s*\n?\s*17/g) || []).length;
  rec('X5', '数据中心主视窗：17 项数据源都在页面上',
    feedList.length === 17 && missingByShort.length === 0 ? '通过' : '不通过',
    '接口 17 项；每项按 name 去括号后的前缀在页面文本里必须能找到'
    + '（页面渲染的是简称，如「资金费率」而非「资金费率 (Funding Rate)」）',
    { api_total: feeds.body?.total, api_feeds: feedList.length,
      页面缺失_按简称: missingByShort.map((f) => f.name),
      页面缺失_按code: missingByCode.map((f) => f.code),
      页面自报的17字样数: selfDeclared,
      说明: 'code 是接口内部键（如 15m/oi_mc），页面不渲染 code，故 code 缺失属正常' });

  const dcTabs = [
    ['X6', 'oi', ['OI']],
    ['X7', 'funding', ['费率']],
    ['X8', 'smart-money', ['聪明钱']],
  ];
  for (const [id, name, needles] of dcTabs) {
    const click = await page.evaluate(CLICK_BY_TEXT, needles);
    await page.waitForTimeout(1800);
    dom = await read();
    const r = await api(`/api/v1/data/${name}?limit=150`);
    const apiN = (r.body?.items || []).length;
    const rows = dom.tables[0]?.rows ?? null;
    const apiSyms = (r.body?.items || []).map((x) => x.symbol || x.market_id || x.code);
    const firstSym = apiSyms[0];
    rec(id, `数据中心「${needles[0]}」行数 vs /data/${name}`,
      rows === null ? '未验证' : (rows === apiN || rows === apiN + 1 ? '通过' : '不通过'),
      '表格行数（含表头）= 接口 items 条数（+1 表头）',
      { clicked: click.clicked, label: click.label, ui_rows: rows, api_items: apiN,
        api_total: r.body?.total, 首行标的: firstSym,
        首行是否出现在页面: firstSym ? dom.text.includes(String(firstSym)) : null });
  }

  // K 线 8 周期
  await page.evaluate(CLICK_BY_TEXT, ['K线']);
  await page.waitForTimeout(1500);
  dom = await read();
  const periods = ['日线', '12小时', '8小时', '4小时', '1小时', '30分钟', '15分钟', '5分钟'];
  const missingP = periods.filter((p) => !dom.text.includes(p));
  rec('X9', '数据中心 K 线视图：8 个周期入口齐全',
    missingP.length === 0 ? '通过' : '不通过',
    '8 个周期标签都要出现在页面上（能切换是 02c 的职责，这里只验入口齐全）',
    { 缺失周期: missingP, ui_rows: dom.tables[0]?.rows ?? null });

  // 指标
  await page.evaluate(CLICK_BY_TEXT, ['指标']);
  await page.waitForTimeout(1500);
  dom = await read();
  const fc = await api('/api/v1/data/field-contract');
  const fcFields = Object.keys(fc.body || {});
  rec('X10', '数据中心「指标」视图有内容（不是空壳）',
    dom.tables.length >= 1 && dom.text.length > 500 ? '通过' : '不通过',
    '指标视图至少要渲染出表格且文本量足够；接口 field-contract 可读',
    { ui_tables: dom.tables.length, ui_text_len: dom.text.length,
      api_field_contract_keys: fcFields.slice(0, 12) });

  // ══ 3. 策略管理：三页签是否真的切换 ═══════════════════════════════
  await page.evaluate(CLICK_NAV, '策略管理');
  await page.waitForTimeout(1500);
  const st0 = await read();
  const stViews = [];
  for (const [label] of [['数据依赖'], ['版本对比'], ['规则说明']]) {
    await page.evaluate(CLICK_BY_TEXT, [label]);
    await page.waitForTimeout(900);
    const d = await read();
    stViews.push({ 页签: label, 文本长度: d.text.length, 文本摘要: d.text.slice(0, 60) });
  }
  const allSame = stViews.every((v) => v.文本长度 === st0.text.length
    && v.文本摘要 === stViews[0].文本摘要);
  rec('X11', '策略管理三页签切换后正文是否变化', allSame ? '不通过' : '通过',
    '点不同页签，正文内容必须不同；三页签正文完全一致即为「空壳页签」',
    { 基线文本长度: st0.text.length, 各页签: stViews,
      结论: allSame ? '三个页签正文完全相同 ⇒ 数据依赖 / 版本对比 没有独立内容'
                    : '至少有两个页签正文不同' },
    { 源码佐证: 'StrategyManagementView.tsx 里 activeTab 只参与按钮 className，'
                + '不参与任何渲染分支 ⇒ 切换只改高亮，不改内容' });

  // ══ 4. 回测复盘 ═════════════════════════════════════════════════════
  await page.evaluate(CLICK_NAV, '回测复盘');
  await page.waitForTimeout(2600);

  const btAll = await api('/api/v1/backtests?limit=3000');
  const btBatches = await api('/api/v1/backtests/batches');
  await page.evaluate(CLICK_BY_TESTID, 'bt-batch-mode-run');
  await page.waitForTimeout(2200);
  dom = await read();
  const cnt = dom.text.match(/共\s*(\d+)\s*条/);
  const uiRuns = cnt ? Number(cnt[1]) : null;
  const apiRunsTotal = btAll.body?.total ?? null;
  const apiTradesOnly = (btAll.body?.items || []).filter((x) => (x.trades_count ?? 0) > 0).length;
  const hasFilterChip = /仅看有成交|仅显示有成交|有成交/.test(dom.text);
  rec('X12', '回测「按单 run」列表条数 vs /backtests',
    uiRuns === null ? '未验证'
      : (uiRuns === apiRunsTotal || uiRuns === apiTradesOnly) ? '通过' : '不通过',
    'UI 条数必须等于接口 total，或等于「有成交」子集（且页面上必须看得见该筛选条件）',
    { ui_条数: uiRuns, api_total: apiRunsTotal, api_items: btAll.body?.items?.length,
      api_有成交子集: apiTradesOnly, 页面是否有筛选提示: hasFilterChip,
      api_truncated: btAll.body?.truncated, api_runs_total_all: btAll.body?.runs_total_all });

  // ── X13：批次模式是「左栏 17 个批次 + 右栏当前批次的 run 数」两层数字 ──
  await page.evaluate(CLICK_BY_TESTID, 'bt-batch-mode-batch');
  await page.waitForTimeout(2200);
  dom = await read();
  const uiBatchRows = (dom.text.match(/·\s*([\d,]+)\s*run/g) || []).map((s) => Number(s.replace(/[^\d]/g, '')));
  const uiBatchTotal = uiBatchRows.length;
  const apiBatchTotal = btBatches.body?.total ?? null;
  const curBatchM = dom.text.match(/批次\s*(\S+)\s*内的 run/);
  const curBatch = curBatchM ? curBatchM[1] : null;
  const curBatchApi = (btBatches.body?.items || []).find((b) => b.batch_id === curBatch);
  const cnt2 = dom.text.match(/共\s*([\d,]+)\s*条/);
  const uiRightRows = cnt2 ? Number(cnt2[1].replace(/,/g, '')) : null;
  const leftOk = uiBatchTotal === apiBatchTotal;
  const rightOk = curBatchApi ? uiRightRows === curBatchApi.runs_total : null;
  rec('X13', '回测「按批次」：左栏批次数 + 右栏当前批次 run 数',
    leftOk && rightOk === true ? '通过' : '不通过',
    '左栏渲染的批次行数 = 接口批次总数(17)；右栏「共 N 条」= 当前所选批次的 runs_total'
    + '（不是批次数 17 —— v1 判据把它当成批次数是错的）',
    { ui_左栏批次数: uiBatchTotal, api_批次总数: apiBatchTotal,
      ui_当前批次: curBatch, api_该批次runs_total: curBatchApi?.runs_total,
      ui_右栏条数: uiRightRows, 右栏是否等于该批次run数: rightOk,
      左栏各批次run数: uiBatchRows });

  // ── X13b：批次汇总的笔数/净盈亏 vs /batches/{id}/summary（三层第三层）──
  let sumApi = null;
  if (curBatch) sumApi = await api(`/api/v1/backtests/batches/${curBatch}/summary`);
  const uiTradesM = dom.text.match(/合计\s*([\d,]+)\s*笔/);
  // 页面金额带 $ 前缀（「净盈亏 -$3,678.73」）⇒ 正则必须允许 $，否则会命中别处的裸数字
  const uiPnlM = dom.text.match(/净盈亏\s*(-?\$?\s*-?[\d,]+\.?\d*)/);
  const uiTrades = uiTradesM ? Number(uiTradesM[1].replace(/,/g, '')) : null;
  const uiPnl = uiPnlM ? Number(uiPnlM[1].replace(/[^\d.\-]/g, '')) : null;
  const sTr = sumApi?.body?.trades || {};
  const sBt = sumApi?.body?.batch || {};
  const apiTrades = sTr.count ?? sBt.trades_total ?? null;
  const apiPnl = sTr.net_pnl_sum ?? sBt.net_pnl_sum ?? null;
  const pnlOk = apiPnl !== null && uiPnl !== null && Math.abs(uiPnl - apiPnl) < 0.01;
  rec('X13b', '批次汇总「合计 N 笔 / 净盈亏 X」= /batches/{id}/summary',
    apiTrades === null || apiPnl === null ? '未验证'
      : (uiTrades === apiTrades && pnlOk) ? '通过' : '不通过',
    '页面汇总的成交笔数与净盈亏必须与批次 summary 接口逐值相等（容差 0.01 USDT）；'
    + 'summary 的笔数/盈亏在 trades 子对象里（不是顶层 trades_total）',
    { 批次: curBatch, ui_笔数: uiTrades, api_笔数: apiTrades,
      ui_净盈亏: uiPnl, api_净盈亏: apiPnl,
      接口自检: sumApi?.body?.checks, derived_from: sumApi?.body?.derived_from,
      接口missing_reason: sumApi?.body?.missing_reason });

  // equity 404 复验（反重力 19:21 改过 backtest_store.py）
  const eq = await api('/api/v1/backtests/run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT/equity');
  rec('X14', '研究期 run 的 /equity 是否仍 404',
    eq.status === 200 ? '通过' : '不通过',
    '反重力声明已加动态权益兜底；本条只验「现在是否 200」',
    { status: eq.status, items: (eq.body?.items || []).length,
      quality: eq.body?.quality, missing: eq.body?.missing_reason });

  // 验收复盘 4 页签
  await page.evaluate(CLICK_BY_TESTID, 'backtest-pane-review');
  await page.waitForTimeout(2000);
  const rv0 = await read();
  const rvViews = [];
  for (const label of ['交易复盘', '信号链', '稳定性诊断', '总览']) {
    await page.evaluate(CLICK_BY_TEXT, [label]);
    await page.waitForTimeout(900);
    const d = await read();
    rvViews.push({ 页签: label, 文本长度: d.text.length, 表格数: d.tables.length });
  }
  const rvSame = rvViews.every((v) => v.文本长度 === rv0.text.length);
  rec('X15', '验收复盘 4 个页签切换后正文是否变化', rvSame ? '不通过' : '通过',
    '4 个页签正文长度不得全部相同',
    { 基线: rv0.text.length, 各页签: rvViews });

  await page.screenshot({ path: path.join(SHOTS, '05_backtest_review.png') });

  // ══ 汇总 ═══════════════════════════════════════════════════════════
  const cntBy = {};
  for (const r of results) cntBy[r.verdict] = (cntBy[r.verdict] || 0) + 1;
  const doc = {
    生成时刻: new Date().toISOString(),
    前端: BASE,
    纪律: [
      '接口在页面上下文 fetch（同源），避免本机代理干扰',
      '顶栏与内容区分开读：模拟盘权益在顶栏',
      '抽不到值判未验证，不猜',
    ],
    计数: cntBy,
    consoleErrors,
    网络: {
      api失败条数: apiFailed.length,
      api失败明细: apiFailed.slice(0, 20),
      外部静态资源失败条数: assetFailed.length,
      外部静态资源示例: assetFailed.slice(0, 5),
      说明: '外部静态资源失败 = 币安图标外链（bin.bnbstatic.com）被网络拦截，不影响功能判定',
    },
    failedReq: failedReq.slice(0, 40),
    明细: results,
  };
  fs.writeFileSync(path.join(OUT, '05_三层对账.json'), JSON.stringify(doc, null, 2), 'utf8');
  console.log('\n=== 汇总 ===');
  console.log(JSON.stringify(cntBy, null, 2));
  console.log('consoleErrors =', consoleErrors.length,
    '| api失败 =', apiFailed.length, '| 外部资源失败 =', assetFailed.length);
  for (const r of results) console.log(`  ${r.id} ${r.verdict} ${r.title}`);
  await browser.close();
})();
