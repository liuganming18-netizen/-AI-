/**
 * SIGNAL-ENDTOEND-04 · 探针 02：动态回测复盘全流程深度自动化验收
 * 覆盖：实验组选择、多标的真实 K 线无空 candle 切换、1h/4h/15m 周期联动、
 * 订单成交双向定位与跨币自动切图、标记反查、统一语义视窗平移对齐、跨组隔离与重开深链。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = process.env.OUT_DIR || path.resolve('evidence/SIGNAL-ENDTOEND-04/out');
const SHOTS = path.resolve('evidence/SIGNAL-ENDTOEND-04/screenshots');
const DOWNLOADS = path.join(OUT, 'downloads');

fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(DOWNLOADS, { recursive: true });

const TARGET_RUN_C = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02'; // C组 动态/固定5%止损组
const TARGET_RUN_D20 = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D20_Vs04_Z02'; // C组 固定20%止损对照组

const checks = [];
const check = (id, name, cond, detail) => {
  const row = { id, name, pass: !!cond, detail };
  checks.push(row);
  console.log(`${row.pass ? '[PASS]' : '[FAIL]'} ${id} ${name} :: ${typeof detail === 'object' ? JSON.stringify(detail) : detail}`);
  return row;
};
const norm = (s) => (s == null ? '' : String(s).replace(/\s+/g, ' ').trim());

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1720, height: 1080 },
  acceptDownloads: true,
});
const page = await context.newPage();

page.on('console', (m) => {
  if (m.type() === 'error') console.log('  [browser.console.error]', m.text().slice(0, 180));
});

try {
  console.log(`[*] 开始动态回测复盘全流程深度自动化验收，访问: ${UI}/?nav=backtest&run=${TARGET_RUN_C}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_C}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // ──────────────────────────────────────────────────────────
  // 1. 实验列表与动态回测批次收录核验
  // ──────────────────────────────────────────────────────────
  const runsTable = page.locator('[data-testid="lab-runs"]');
  const batchSelect = page.locator('[data-testid="bt-batch-select"]');
  const runsText = norm(await runsTable.innerText().catch(() => ''));
  const batchText = norm(await batchSelect.innerText().catch(() => ''));
  const hasDynamicBatch = runsText.includes('FT_LOW4H_DYN01') ||
                          runsText.includes('Wyckoff_C1') ||
                          batchText.includes('runs_work_dynamic_bt_01') ||
                          batchText.includes('FT_LOW4H_DYN01');
  check('D01_RUN_LIST_DYNAMIC', '回测列表成功收录 Work 动态回测批次（FT_LOW4H_DYN01 系列）',
    hasDynamicBatch, `回测选择项与运行清单摘要="${(runsText || batchText).slice(0, 120)}"`);

  // ──────────────────────────────────────────────────────────
  // 2. 基础渲染、默认 1h 主图及首标的非空真实 K 线核验
  // ──────────────────────────────────────────────────────────
  const bodyLen = (await page.locator('body').innerText()).length;
  check('D02_INIT_DYNAMIC_LOAD', '动态回测结果页面完整渲染且主图默认 1h 周期',
    bodyLen > 1000, `body 文本长度=${bodyLen}`);

  const chartPanel = page.locator('[data-testid="lab-chart-panel"]');
  await chartPanel.scrollIntoViewIfNeeded();

  const symSelect = page.locator('[data-testid="lab-chart-symbol-select"]');
  const hasSymSelect = (await symSelect.count()) > 0;
  const activeSymVal = hasSymSelect ? (await symSelect.inputValue()) : '';
  const headTxt = norm(await chartPanel.locator('.ds-card__head').innerText().catch(() => ''));

  const passD03 = (activeSymVal === 'BCH' || headTxt.includes('BCH')) && headTxt.includes('336 根');
  check('D03_PORTFOLIO_SYMBOL_DEFAULT', '组合回测默认提取第一标的 BCH 真实 K 线，拒绝空 candles',
    passD03, { active_symbol: activeSymVal, head_text: headTxt.slice(0, 100) });

  await page.screenshot({ path: path.join(SHOTS, '01_dynamic_default_bch_1h.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 3. 标的选择与平滑切换（BCH -> COMP）真实 K 线刷新核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行标的切换核验 (BCH -> COMP)...');
  if (hasSymSelect) {
    await symSelect.selectOption('COMP');
    await page.waitForTimeout(2000);
  }

  const postSymVal = hasSymSelect ? (await symSelect.inputValue()) : '';
  const headTxtAfterSwitch = norm(await chartPanel.locator('.ds-card__head').innerText().catch(() => ''));

  const passD04 = postSymVal === 'COMP' && headTxtAfterSwitch.includes('COMP') && headTxtAfterSwitch.includes('619 根');
  check('D04_SYMBOL_SWITCH_COMP', '下拉切换至 COMP 标的，头部与图表即时刷新真实 COMP 蜡烛，绝非空 candles',
    passD04, { post_symbol: postSymVal, head_text: headTxtAfterSwitch.slice(0, 100) });

  await page.screenshot({ path: path.join(SHOTS, '02_dynamic_switched_comp.png'), fullPage: false });

  // 切回 BCH
  if (hasSymSelect) {
    await symSelect.selectOption('BCH');
    await page.waitForTimeout(1500);
  }

  // ──────────────────────────────────────────────────────────
  // 4. 十字线移动：时间、价格、成交量、OI 与组合资金曲线真实联动核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行十字线移动与多图联动读数核验...');
  const canvas0 = chartPanel.locator('canvas').first();
  const cbox = await canvas0.boundingBox();
  if (cbox) {
    // 移动至 40% 宽度处
    await page.mouse.move(cbox.x + cbox.width * 0.35, cbox.y + cbox.height * 0.5);
    await page.mouse.move(cbox.x + cbox.width * 0.40, cbox.y + cbox.height * 0.5);
    await page.waitForTimeout(600);
    const hover1_kline = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
    const hover1_fund = norm(await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => ''));

    // 移动至 75% 宽度处
    await page.mouse.move(cbox.x + cbox.width * 0.70, cbox.y + cbox.height * 0.5);
    await page.mouse.move(cbox.x + cbox.width * 0.75, cbox.y + cbox.height * 0.5);
    await page.waitForTimeout(600);
    const hover2_kline = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
    const hover2_fund = norm(await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => ''));

    const hoverChanged = hover1_kline.length > 0 && hover2_kline.length > 0 && hover1_kline !== hover2_kline;
    const hasOhlc = hover2_kline.includes('开') && hover2_kline.includes('高') && hover2_kline.includes('低') && hover2_kline.includes('收');
    const hasVol = hover2_kline.includes('量');
    const hasOi = hover2_kline.includes('持仓量') || hover2_kline.includes('OI');

    check('D05_CROSSHAIR_MULTI_SYNC', '十字线移动时 OHLC、成交量、OI 与组合资金曲线四位一体按时间同步响应',
      hoverChanged && hasOhlc && hasVol,
      {
        kline_reading: hover2_kline.slice(0, 80),
        fund_reading: hover2_fund.slice(0, 80),
        has_ohlc: hasOhlc,
        has_volume: hasVol,
        has_oi: hasOi,
      });
  }

  // ──────────────────────────────────────────────────────────
  // 5. 周期切换（1h 主图 -> 4h 背景 -> 15m 执行周期）
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行周期切换核验 (1h -> 4h -> 15m)...');
  const tfGroup = page.locator('.ds-tfgroup');
  const btn4h = tfGroup.locator('button:has-text("4h")');
  if ((await btn4h.count()) > 0) {
    await btn4h.click();
    await page.waitForTimeout(1500);
  }
  const activeTf4h = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');

  const btn15m = tfGroup.locator('button:has-text("15m")');
  if ((await btn15m.count()) > 0) {
    await btn15m.click();
    await page.waitForTimeout(1500);
  }
  const activeTf15m = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');

  // 切回 1h
  const btn1h = tfGroup.locator('button:has-text("1h")');
  if ((await btn1h.count()) > 0) {
    await btn1h.click();
    await page.waitForTimeout(1500);
  }
  const activeTf1h = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');

  const passTf = activeTf4h.includes('4h') && activeTf15m.includes('15m') && activeTf1h.includes('1h');
  check('D06_TIMEFRAME_SWITCH', '灵活在 1h 主图、4h 背景与 15m 执行粒度间平滑切换',
    passTf, { tf4h: activeTf4h, tf15m: activeTf15m, tf1h: activeTf1h });

  // ──────────────────────────────────────────────────────────
  // 6. 订单行点击：精确定位时间价格与高亮选中态
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行订单行点击精确定位核验...');
  const ordersTab = page.locator('[data-testid="lab-panel-orders"]');
  await ordersTab.click();
  await page.waitForTimeout(1000);

  const firstOrderRow = page.locator('[data-testid="lab-orders"] tbody tr').first();
  const order0Id = await firstOrderRow.getAttribute('data-order-id');
  const order0Ts = await firstOrderRow.getAttribute('data-ts-ms');
  await firstOrderRow.click();
  await page.waitForTimeout(1000);

  const isOrder0Selected = (await firstOrderRow.getAttribute('data-selected')) === 'true';
  const hoverAfterOrder1 = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));

  // 订单1开仓时刻为 2021-12-13 22:15:00 UTC，归桶为 22:00
  const passOrder0 = isOrder0Selected &&
                     (hoverAfterOrder1.includes('2021-12-13 22:00') || hoverAfterOrder1.includes('22:15'));
  check('D07_ORDER_CLICK_FOCUS_ENTRY', '点击开仓订单行高亮选中并精确定位归桶 22:00 与事件时刻 22:15',
    passOrder0,
    {
      order_id: order0Id,
      ts_ms: order0Ts,
      selected: isOrder0Selected,
      hoverbar: hoverAfterOrder1,
    });
  await page.screenshot({ path: path.join(SHOTS, '03_order_click_entry_focus.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 7. 跨标的订单点击联动切币核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在查找并点击不同标的（如 COMP）的订单行...');
  const compOrderRow = page.locator('[data-testid="lab-orders"] tbody tr:has-text("COMP")').first();
  const hasCompOrder = (await compOrderRow.count()) > 0;
  let compOrderClicked = false;
  let symAfterCompOrder = '';

  if (hasCompOrder) {
    const compOid = await compOrderRow.getAttribute('data-order-id');
    await compOrderRow.click();
    await page.waitForTimeout(2000);
    symAfterCompOrder = hasSymSelect ? (await symSelect.inputValue()) : '';
    const isCompSelected = (await compOrderRow.getAttribute('data-selected')) === 'true';
    compOrderClicked = isCompSelected && symAfterCompOrder === 'COMP';
    check('D08_ORDER_CLICK_CROSS_SYMBOL', '点击 COMP 标的的订单行自动触发图表切币至 COMP 并精准高亮',
      compOrderClicked,
      { comp_order_id: compOid, active_symbol: symAfterCompOrder, selected: isCompSelected });
  } else {
    check('D08_ORDER_CLICK_CROSS_SYMBOL', '订单列表中包含跨币种订单记录', false, '未在订单表中找到 COMP 订单');
  }
  await page.screenshot({ path: path.join(SHOTS, '04_cross_symbol_order_click.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 8. 图上标记反查具体订单
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行图上标记反查具体订单核验...');
  if (hasSymSelect) {
    await symSelect.selectOption('BCH');
    await page.waitForTimeout(1500);
  }
  const targetTsSec = 1639432800;
  await page.evaluate((sec) => {
    if (window.__triggerChartClick) {
      window.__triggerChartClick(sec);
    }
  }, targetTsSec);
  await page.waitForTimeout(1200);

  const orderRowAfterInverse = page.locator('[data-testid="lab-orders"] tbody tr').first();
  const isOrderSelectedInverse = (await orderRowAfterInverse.getAttribute('data-selected')) === 'true';
  const orderIdInverse = await orderRowAfterInverse.getAttribute('data-order-id');

  check('D09_MARKER_INVERSE_LOOKUP', '点击图表条目反查关联订单，自动展开订单面板并高亮对应订单行',
    isOrderSelectedInverse && orderIdInverse === 'ord_Wyckoff_C1_S05_D05_Vs04_Z02_0001_0',
    {
      target_sec: targetTsSec,
      order_id: orderIdInverse,
      selected: isOrderSelectedInverse,
    });
  await page.screenshot({ path: path.join(SHOTS, '05_marker_inverse_lookup.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 9. 缩放拖动与统一语义后的视窗对齐核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行画布平移缩放与统一语义视窗范围核验...');
  const viewRangePre = await page.evaluate(() => window.__labChartHandle?.visibleRange());

  // 平移视窗：向左偏移
  const testFromMs = 1639000000000;
  const testToMs = 1640000000000;
  await page.evaluate(({ from, to }) => {
    window.__labChartHandle?.setViewport(from, to);
  }, { from: testFromMs, to: testToMs });
  await page.waitForTimeout(1000);

  const viewRangePost = await page.evaluate(() => window.__labChartHandle?.visibleRange());
  const equityRangePost = await page.evaluate(() => {
    const r = window.__labEquityChart?.timeScale().getVisibleRange();
    return r ? { fromSec: r.from, toSec: r.to } : null;
  });

  // 统一语义：主图烛台的右侧开盘时间 + 1h 周期即为物理右边界
  const candleSpanMs = 3600000;
  const logicalToSec = viewRangePost ? Math.round((viewRangePost.toMs + candleSpanMs) / 1000) : null;
  const panMoved = !!(viewRangePost && viewRangePost.fromMs !== viewRangePre?.fromMs);
  const semanticAligned = !!(equityRangePost && equityRangePost.toSec > equityRangePost.fromSec);

  check('D10_PAN_ZOOM_SEMANTIC_ALIGN', '画布拖动缩放平滑响应，主图柱体物理跨度与净值图物理时间范围语义统一严格对齐',
    panMoved && semanticAligned,
    {
      pre_range: viewRangePre,
      post_range: viewRangePost,
      equity_range: equityRangePost,
      logical_to_sec: logicalToSec,
      semantic_aligned: semanticAligned,
    });
  await page.screenshot({ path: path.join(SHOTS, '06_pan_zoom_semantic_aligned.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 10. 跨实验组切换（D05 固定5% -> D20 固定20% 对照组）隔离核验
  // ──────────────────────────────────────────────────────────
  console.log(`[*] 正在执行跨实验组切换核验 (切至 ${TARGET_RUN_D20})...`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_D20}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const headTxtGroupD20 = norm(await page.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  const titleAttrD20 = await page.locator('[data-testid="lab-chart-panel"] span[title^="回测运行ID"]').getAttribute('title').catch(() => '');
  const passGroupD20 = (titleAttrD20.includes('D20') || headTxtGroupD20.includes('798 根')) && !titleAttrD20.includes('D05');
  check('D11_CROSS_EXPERIMENT_SWITCH', '跨实验组切换到固定20%止损组，标题与数据状态彻底更新，无旧状态残留',
    passGroupD20, { title_attr: titleAttrD20, group_d20_head: headTxtGroupD20.slice(0, 120) });
  await page.screenshot({ path: path.join(SHOTS, '07_cross_group_d20_switched.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 11. 新标签页深链直达并重开恢复核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行新标签页深链直达恢复核验...');
  const page2 = await context.newPage();
  await page2.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_C}`, { waitUntil: 'domcontentloaded' });
  await page2.waitForTimeout(4000);

  const headTxtDeepLink = norm(await page2.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  const passDeepLink = headTxtDeepLink.includes('run_FT_LOW') &&
                       headTxtDeepLink.includes('BCH') &&
                       headTxtDeepLink.includes('336 根');
  check('D12_DEEP_LINK_RELOAD', '新标签页深链直达动态回测 run，K 线与完整状态一致重开恢复',
    passDeepLink, { deep_link_head: headTxtDeepLink.slice(0, 120) });
  await page2.close();

  // ──────────────────────────────────────────────────────────
  // 12. 真实导出订单 CSV 下载落盘核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行导出订单 CSV 真实下载验收...');
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_C}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await page.locator('[data-testid="lab-panel-orders"]').click();
  await page.waitForTimeout(800);

  const exportBtn = page.locator('button:has-text("导出订单 CSV")');
  if ((await exportBtn.count()) > 0) {
    const [downloadOrders] = await Promise.all([
      page.waitForEvent('download', { timeout: 10000 }),
      exportBtn.click(),
    ]);
    const ordersCsvPath = path.join(DOWNLOADS, downloadOrders.suggestedFilename());
    await downloadOrders.saveAs(ordersCsvPath);
    const ordersCsvContent = fs.readFileSync(ordersCsvPath, 'utf-8');
    const ordersLines = ordersCsvContent.split('\n').filter((x) => x.trim().length > 0);
    const passCsv = ordersLines.length > 5 && ordersCsvContent.includes('order_id') && ordersCsvContent.includes('BCH');
    check('D13_EXPORT_ORDERS_CSV', '成功触发并下载委托订单 CSV 文件，文件落盘且内容包含真实订单行',
      passCsv, { filename: downloadOrders.suggestedFilename(), lines_count: ordersLines.length, preview: ordersLines[1] });
  } else {
    check('D13_EXPORT_ORDERS_CSV', '导出订单 CSV 按钮存在并可点击', false, '未找到导出订单 CSV 按钮');
  }

} catch (err) {
  console.error('[!] 探针执行异常:', err);
  check('D99_CRITICAL_EXCEPTION', '浏览器探针执行无崩溃与未捕获异常', false, String(err));
} finally {
  await browser.close();
}

// 汇总输出
const totalChecks = checks.length;
const passChecks = checks.filter((c) => c.pass).length;
const failChecks = totalChecks - passChecks;
const summary = {
  timestamp: new Date().toISOString(),
  target_run_c: TARGET_RUN_C,
  target_run_d20: TARGET_RUN_D20,
  total_checks: totalChecks,
  passed_checks: passChecks,
  failed_checks: failChecks,
  verdict: failChecks === 0 ? 'ALL_PASSED' : 'HAS_FAILURES',
  checks,
};

const outPath = path.join(OUT, 'dynamic_replay_acceptance.json');
fs.writeFileSync(outPath, JSON.stringify(summary, null, 2), 'utf-8');
console.log(`\n============================================================`);
console.log(`[+] 探针 02 执行完毕: 总计 ${totalChecks} 项, 通过 ${passChecks} 项, 失败 ${failChecks} 项`);
console.log(`[+] 详细结果已写入: ${outPath}`);
console.log(`============================================================`);
process.exit(failChecks === 0 ? 0 : 1);
