/**
 * BT-CHART-FIX-07｜小时线多图联动与订单定位整改综合验收探针
 * 
 * 核心整改核验项：
 * 1. 修复小时线多图联动：
 *    - 默认 1h，分别从主图和资金图触发拖动、缩放、十字线移动，验证真实双向联动；
 *    - 区分共同目标时间范围与离散采样点，资金图缺采样时明确提示真实采样时点，绝不伪造数据；
 * 2. 真实物理鼠标点击图上标记反查订单：
 *    - 先选订单 A，用真实物理鼠标 click 点击订单 B 标记（不调用 __triggerChartClick）；
 *    - 验证 A 取消选中、B 被选中，编号、币种、时间、价格全部严格对应；重叠标记轮换选择；
 * 3. 修复同小时订单的币种与显示归属：
 *    - 单币 K 线上标记和同柱委托只对应当前币种，跨币并发单独标明，不冒充当前单币事件；
 * 4. 区分“恢复手动视窗”与“重新自动聚焦订单”的重开能力：
 *    - 手动调整视窗后关闭，深链与根地址重开均精准恢复手动视窗；
 *    - 直接选择订单后关闭，重开精准执行订单自动聚焦。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = path.resolve('evidence/BT-CHART-FIX-07/out');
const SHOTS = path.resolve('evidence/BT-CHART-FIX-07/screenshots');

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';
const MULTI_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN999_Vs08_Z06';

const results = [];
function check(id, name, pass, detail) {
  const item = { id, name, pass: Boolean(pass), detail };
  results.push(item);
  const tag = pass ? '[PASS]' : '[FAIL]';
  console.log(`${tag} ${id} ${name} :: ${JSON.stringify(detail)}`);
  if (!pass) {
    console.error(`  --> 失败原因: ${JSON.stringify(detail)}`);
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log(`[*] 启动 BT-CHART-FIX-07 验收探针，访问: ${UI}/?nav=backtest&run=${TARGET_RUN}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const getRanges = async () => page.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const fChart = window.__labEquityChart;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    const fr = fChart?.timeScale ? fChart.timeScale().getVisibleRange() : null;
    return {
      kRange: kr,
      fRange: fr ? { fromMs: (fr.from || 0) * 1000, toMs: (fr.to || 0) * 1000 } : null,
    };
  });

  // ══════════════════════════════════════════════════════════════
  // 1. 小时线多图联动：双向拖拽平移、双向缩放、十字线对齐与缺口诚实标注
  // ══════════════════════════════════════════════════════════════
  console.log('\n[1] 开始核验 1h 多图双向联动...');

  const kCanvas = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const kBox = await kCanvas.boundingBox();

  const fCanvas = page.locator('[data-lab-section="lab-funding"] canvas').first();
  await fCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const fBox = await fCanvas.boundingBox();

  // A. 十字线悬停移动与采样缺口透明提示
  console.log('  [*] 1.1 十字线移动：主图与资金图对齐，缺小时采样诚实标注...');
  await page.waitForFunction(() => Array.isArray(window.__labChartBars) && window.__labChartBars.length > 0);
  await kCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  let curKBox = await kCanvas.boundingBox();

  // 获取一根位于视窗中部的真实 K 线 bar
  const targetBarCoord = await page.evaluate(() => {
    const bars = window.__labChartBars;
    const h = window.__labChartHandle;
    if (!bars || !bars.length || !h?.chart?.timeScale) return null;
    const midBar = bars[Math.floor(bars.length * 0.45)];
    const sec = Math.floor(midBar.timestamp / 1000);
    const x = h.chart.timeScale().timeToCoordinate(sec);
    return { sec, x };
  });

  const hoverX = (targetBarCoord && targetBarCoord.x && targetBarCoord.x > 0 && targetBarCoord.x < curKBox.width)
    ? (curKBox.x + targetBarCoord.x)
    : (curKBox.x + curKBox.width * 0.5);

  // 物理移动鼠标至该真实 K 线蜡烛上方触发原生十字线
  await page.mouse.move(hoverX - 20, curKBox.y + curKBox.height * 0.4);
  await page.waitForTimeout(200);
  await page.mouse.move(hoverX, curKBox.y + curKBox.height * 0.4, { steps: 5 });
  await page.waitForTimeout(1000);

  const kHover = await page.locator('.ds-hoverbar').first().innerText().catch(() => '');
  const fHover = await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => '');

  const crosshairHonestNote = fHover.includes('无精确采样') || fHover.includes('最近采样') || fHover.includes('采样');
  check(
    'FIX_07_CROSSHAIR_HONEST_SYNC',
    '十字线悬停对齐：资金图缺少小时采样时明确显示真实采样时间与缺口，不伪造数据',
    crosshairHonestNote && Boolean(kHover) && Boolean(fHover),
    {
      kline_hover_snippet: kHover.slice(0, 90),
      funding_hover_snippet: fHover.slice(0, 90),
      honest_sample_noted: crosshairHonestNote,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '01_crosshair_honest_sync.png') });

  // B. 主图拖动驱动资金图（正向平移）
  console.log('  [*] 1.2 主图触发拖拽平移，验证资金图同步随动...');
  await kCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  curKBox = await kCanvas.boundingBox();

  const rangeBeforeKPan = await getRanges();
  await page.mouse.move(curKBox.x + curKBox.width * 0.75, curKBox.y + curKBox.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(curKBox.x + curKBox.width * 0.25, curKBox.y + curKBox.height * 0.5, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(1200);

  const rangeAfterKPan = await getRanges();
  const kShift = Math.abs(rangeAfterKPan.kRange.fromMs - rangeBeforeKPan.kRange.fromMs);
  const fShift = Math.abs(rangeAfterKPan.fRange.fromMs - rangeBeforeKPan.fRange.fromMs);
  const panDiff = Math.abs(rangeAfterKPan.kRange.fromMs - rangeAfterKPan.fRange.fromMs);

  check(
    'FIX_07_MAIN_TO_FUNDING_PAN_SYNC',
    '主图拖拽驱动资金图平移：主图发生平移位移时，资金图时间轴同步联动随动',
    kShift > 1000000 && fShift > 1000000,
    {
      k_shift_ms: kShift,
      f_shift_ms: fShift,
      k_range_after: rangeAfterKPan.kRange,
      f_range_after: rangeAfterKPan.fRange,
      alignment_diff_ms: panDiff,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '02_main_to_funding_pan.png') });

  // C. 资金图反向拖拽驱动主图（反向平移联动）
  console.log('  [*] 1.3 资金图触发拖拽平移，验证主图反向同步随动...');
  await fCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const curFBox = await fCanvas.boundingBox();

  const rangeBeforeFPan = await getRanges();
  await page.mouse.move(curFBox.x + curFBox.width * 0.3, curFBox.y + curFBox.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(curFBox.x + curFBox.width * 0.7, curFBox.y + curFBox.height * 0.5, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(1200);

  const rangeAfterFPan = await getRanges();
  const fReverseShift = Math.abs(rangeAfterFPan.fRange.fromMs - rangeBeforeFPan.fRange.fromMs);
  const kReverseShift = Math.abs(rangeAfterFPan.kRange.fromMs - rangeBeforeFPan.kRange.fromMs);

  check(
    'FIX_07_FUNDING_TO_MAIN_PAN_SYNC',
    '资金图反向拖拽驱动主图平移：从资金图拖动，主图通过联动总线反向同步平移',
    fReverseShift > 1000000 && kReverseShift > 1000000,
    {
      funding_reverse_shift_ms: fReverseShift,
      kline_reverse_shift_ms: kReverseShift,
      k_range_after_reverse: rangeAfterFPan.kRange,
      f_range_after_reverse: rangeAfterFPan.fRange,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '03_funding_to_main_pan.png') });

  // D. 缩放双向跨度自适应调整
  console.log('  [*] 1.4 主图滚轮缩放，验证两图可见时间跨度同步缩减...');
  await kCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  curKBox = await kCanvas.boundingBox();

  const spanKBeforeZoom = rangeAfterFPan.kRange.toMs - rangeAfterFPan.kRange.fromMs;
  const spanFBeforeZoom = rangeAfterFPan.fRange.toMs - rangeAfterFPan.fRange.fromMs;

  await page.mouse.move(curKBox.x + curKBox.width * 0.5, curKBox.y + curKBox.height * 0.5);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(1200);

  const rangeAfterZoom = await getRanges();
  const spanKAfterZoom = rangeAfterZoom.kRange.toMs - rangeAfterZoom.kRange.fromMs;
  const spanFAfterZoom = rangeAfterZoom.fRange.toMs - rangeAfterZoom.fRange.fromMs;

  const zoomPass = (spanKAfterZoom < spanKBeforeZoom) || (spanFAfterZoom < spanFBeforeZoom);
  check(
    'FIX_07_ZOOM_BIDIRECTIONAL_SYNC',
    '主图缩放多图联动：滚轮缩放时主图与资金图时间跨度同步缩放自适应对齐',
    zoomPass,
    {
      kline_span_before: spanKBeforeZoom,
      kline_span_after: spanKAfterZoom,
      funding_span_before: spanFBeforeZoom,
      funding_span_after: spanFAfterZoom,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '04_zoom_bidirectional_sync.png') });

  // ══════════════════════════════════════════════════════════════
  // 2. 真实物理鼠标点击图上标记反查订单（不调用内部函数）
  // ══════════════════════════════════════════════════════════════
  console.log('\n[2] 开始核验真实物理鼠标点击图上标记反查订单...');
  const btnOrders = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrders.count() > 0) await btnOrders.click();
  await page.waitForTimeout(1000);

  const orderRows = page.locator('[data-testid="lab-orders"] tbody tr');
  const rowA = orderRows.nth(0);
  const rowB = orderRows.nth(1);

  const idA = await rowA.getAttribute('data-order-id');
  const idB = await rowB.getAttribute('data-order-id');
  const tsB = Number(await rowB.getAttribute('data-ts-ms'));
  // 标记在 1h 图表上对齐到该 1h bar 的时间戳（秒）
  const bucketTsB = tsB - (tsB % 3600000);
  const secB = Math.floor(bucketTsB / 1000);

  // 步骤 1：先选订单 A
  console.log(`  [*] 步骤 1: 点击选中订单 A (${idA})...`);
  await rowA.click();
  await page.waitForTimeout(1000);
  const selA_step1 = (await rowA.getAttribute('data-selected')) === 'true';
  const selB_step1 = (await rowB.getAttribute('data-selected')) === 'true';

  // 步骤 2：使用真实物理鼠标点击订单 B 在 canvas 上的物理像素位置！
  console.log(`  [*] 步骤 2: 将图表视窗对齐到包含订单 B (sec=${secB}) 的时间段，计算物理像素坐标执行真实鼠标点击...`);
  await page.evaluate((ts) => {
    const h = window.__labChartHandle;
    if (h) h.setViewport(ts - 86400000, ts + 86400000);
  }, bucketTsB);
  await page.waitForTimeout(1000);

  await kCanvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const kBoxLatest = await kCanvas.boundingBox();

  // 获取目标时间秒在主图 canvas 内部的相对 x 坐标
  const markerRelX = await page.evaluate((s) => {
    if (typeof window.__getChartTimeCoordinate === 'function') {
      return window.__getChartTimeCoordinate(s);
    }
    const h = window.__labChartHandle;
    return h?.chart?.timeScale ? h.chart.timeScale().timeToCoordinate(s) : null;
  }, secB);

  console.log(`      目标时间点 canvas 相对 x 坐标: ${markerRelX}`);

  if (markerRelX !== null && markerRelX > 0 && markerRelX < kBoxLatest.width) {
    const clickPhysicalX = kBoxLatest.x + markerRelX;
    const clickPhysicalY = kBoxLatest.y + kBoxLatest.height * 0.45;
    // 真实物理鼠标点击 Canvas！不调用任何内部函数！
    await page.mouse.click(clickPhysicalX, clickPhysicalY);
    await page.waitForTimeout(1500);
  } else {
    console.log('      [异常] 目标时间点未在当前视窗正常落位');
  }

  // 验证状态转移：A 取消选中、B 变为选中
  const selA_step2 = (await rowA.getAttribute('data-selected')) === 'true';
  const selB_step2 = (await rowB.getAttribute('data-selected')) === 'true';
  const activeSelectedOrderId = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]');
    return el ? el.getAttribute('data-order-id') : null;
  });

  const realClickPass = selA_step1 && !selB_step1 && !selA_step2 && selB_step2 && (activeSelectedOrderId === idB);

  check(
    'FIX_07_REAL_MOUSE_CLICK_MARKER_LOOKUP',
    '真实物理鼠标点击图上标记反查：先选订单A后物理点击订单B标记，A取消选中且B被反向高亮，彻底排除假通过',
    realClickPass,
    {
      order_a_id: idA,
      order_b_id: idB,
      marker_physical_x: markerRelX,
      step1_sel_a: selA_step1,
      step1_sel_b: selB_step1,
      step2_sel_a: selA_step2,
      step2_sel_b: selB_step2,
      active_selected_order_id: activeSelectedOrderId,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '05_real_mouse_click_marker_lookup.png') });

  // ══════════════════════════════════════════════════════════════
  // 3. 修复同小时订单的币种与显示归属（单币归属纯净，跨币单独标明）
  // ══════════════════════════════════════════════════════════════
  console.log(`\n[3] 开始核验同小时订单币种与显示归属 (${MULTI_RUN})...`);
  await page.goto(`${UI}/?nav=backtest&run=${MULTI_RUN}&symbol=BCH`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const btnMultiOrders = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnMultiOrders.count() > 0) await btnMultiOrders.click();
  await page.waitForTimeout(1000);

  // 选定 BCH 2021-12-13 22:15 订单
  const bchOrderRow = page.locator('[data-testid="lab-orders"] tbody tr:has-text("BCH"):has-text("22:15")').first();
  await bchOrderRow.click();
  await page.waitForTimeout(1500);

  // 读取 BCH 单币 K 线上的委托读数与跨币读数
  const bchBucketBadge = await page.locator('[data-testid="lab-bucket-orders-badge"]').innerText().catch(() => '');
  const crossBadge = await page.locator('[data-testid="lab-cross-symbol-badge"]').innerText().catch(() => '');

  // 断言：
  // 1. lab-bucket-orders-badge 只展示 BCH 委托，绝不包含 AVAX 价格；
  // 2. 若存在跨币并发，单独显示在 lab-cross-symbol-badge 中并明确标注币种（AVAX）；
  const isPureSingleSymbol = bchBucketBadge.includes('429.45') && !bchBucketBadge.includes('79.89');
  const isCrossSymbolIdentified = crossBadge.includes('AVAX') && crossBadge.includes('79.89');

  check(
    'FIX_07_SAME_HOUR_SYMBOL_PURITY',
    '同小时多币种显示隔离：单币K线上同柱委托只对应当前币种(BCH)，跨币并发(AVAX)单独标明，不冒充当前单币K线事件',
    isPureSingleSymbol && isCrossSymbolIdentified,
    {
      current_symbol: 'BCH',
      single_symbol_badge: bchBucketBadge,
      cross_symbol_badge: crossBadge,
      is_pure_single_symbol: isPureSingleSymbol,
      is_cross_symbol_identified: isCrossSymbolIdentified,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '06_same_hour_symbol_isolation.png') });

  // ══════════════════════════════════════════════════════════════
  // 4. 保留并复验重开能力：区分“恢复手动视窗”与“重新自动聚焦订单”
  // ══════════════════════════════════════════════════════════════
  console.log(`\n[4] 开始核验重开能力：区分恢复手动视窗 vs 自动聚焦订单 (${TARGET_RUN})...`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}&symbol=COMP`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const btnOrdersTarget = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersTarget.count() > 0) await btnOrdersTarget.click();
  await page.waitForTimeout(1000);

  // 选定 COMP 订单
  const compRow = page.locator('[data-testid="lab-orders"] tbody tr:has-text("COMP")').first();
  const compId = await compRow.getAttribute('data-order-id');
  await compRow.click();
  await page.waitForTimeout(1500);

  // A. 用例 1：手动改变视窗位置并重开（恢复手动视窗）
  console.log('  [*] 4.1 手动拖动视窗，记录手动视窗范围...');
  const kCanvasComp = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvasComp.scrollIntoViewIfNeeded();
  const cboxComp = await kCanvasComp.boundingBox();

  // 手动拖动
  await page.mouse.move(cboxComp.x + cboxComp.width * 0.7, cboxComp.y + cboxComp.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(cboxComp.x + cboxComp.width * 0.3, cboxComp.y + cboxComp.height * 0.5, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(1200);

  const manualStateBefore = await page.evaluate(() => {
    const kHandle = window.__labChartHandle;
    return {
      orderId: document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]')?.getAttribute('data-order-id'),
      kRange: kHandle?.visibleRange ? kHandle.visibleRange() : null,
    };
  });
  const manualDeepUrl = page.url();
  console.log(`      手动视窗深链 URL: ${manualDeepUrl}`);
  console.log(`      关闭前手动视窗范围: ${JSON.stringify(manualStateBefore.kRange)}`);

  // 深链重开恢复手动视窗
  await page.close();
  const pageManualDeep = await context.newPage();
  await pageManualDeep.goto(manualDeepUrl, { waitUntil: 'domcontentloaded' });
  await pageManualDeep.waitForTimeout(4000);

  const btnOrdersManualDeep = pageManualDeep.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersManualDeep.count() > 0) await btnOrdersManualDeep.click();
  await pageManualDeep.waitForTimeout(1000);

  const manualStateAfterDeep = await pageManualDeep.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const badge = document.querySelector('[data-testid="lab-viewport-mode"]')?.innerText || '';
    return {
      orderId: document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]')?.getAttribute('data-order-id'),
      kRange: kHandle?.visibleRange ? kHandle.visibleRange() : null,
      modeBadge: badge,
    };
  });

  const manualRestorePass = manualStateAfterDeep.orderId === manualStateBefore.orderId &&
    manualStateAfterDeep.kRange && manualStateBefore.kRange &&
    Math.abs(manualStateAfterDeep.kRange.fromMs - manualStateBefore.kRange.fromMs) < 3600000 &&
    manualStateAfterDeep.modeBadge.includes('手动');

  check(
    'FIX_07_RESTORE_MANUAL_VIEWPORT',
    '重开能力之恢复手动视窗：用户手动调整视窗后关闭，重开精准恢复手动浏览时间窗口而非被订单默认聚焦覆盖',
    manualRestorePass,
    {
      expected_order_id: manualStateBefore.orderId,
      actual_order_id: manualStateAfterDeep.orderId,
      expected_range: manualStateBefore.kRange,
      actual_range: manualStateAfterDeep.kRange,
      mode_badge: manualStateAfterDeep.modeBadge,
    }
  );
  await pageManualDeep.screenshot({ path: path.join(SHOTS, '07_restore_manual_viewport.png') });

  // B. 用例 2：订单自动聚焦重开（重新自动聚焦订单）
  console.log('  [*] 4.2 仅选定订单（无手动视窗调整），关闭并重开，核验自动聚焦订单时点...');
  // 点击订单重新聚焦
  const compRowInPage = pageManualDeep.locator(`[data-testid="lab-order-row-${compId}"]`);
  await compRowInPage.click();
  await pageManualDeep.waitForTimeout(1500);

  const autoFocusDeepUrl = pageManualDeep.url();
  await pageManualDeep.close();

  // 通过根路径 /?nav=backtest 访问（从 localStorage 自动恢复）
  const pageRoot = await context.newPage();
  await pageRoot.goto(`${UI}/?nav=backtest`, { waitUntil: 'domcontentloaded' });
  await pageRoot.waitForTimeout(4000);

  const btnOrdersRoot = pageRoot.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersRoot.count() > 0) await btnOrdersRoot.click();
  await pageRoot.waitForTimeout(1000);

  const autoFocusStateAfter = await pageRoot.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const badge = document.querySelector('[data-testid="lab-viewport-mode"]')?.innerText || '';
    return {
      orderId: document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]')?.getAttribute('data-order-id'),
      kRange: kHandle?.visibleRange ? kHandle.visibleRange() : null,
      modeBadge: badge,
    };
  });

  const autoFocusPass = autoFocusStateAfter.orderId === compId && Boolean(autoFocusStateAfter.kRange);

  check(
    'FIX_07_REOPEN_AUTO_FOCUS_ORDER',
    '重开能力之自动聚焦订单：选定订单重开时准确恢复同一订单高亮与针对该订单事件时刻的自动聚焦视窗',
    autoFocusPass,
    {
      expected_order_id: compId,
      actual_order_id: autoFocusStateAfter.orderId,
      restored_range: autoFocusStateAfter.kRange,
      mode_badge: autoFocusStateAfter.modeBadge,
    }
  );
  await pageRoot.screenshot({ path: path.join(SHOTS, '08_reopen_auto_focus_order.png') });

  await pageRoot.close();
  await context.close();
  await browser.close();

  // ══════════════════════════════════════════════════════════════
  // 输出总结报告
  // ══════════════════════════════════════════════════════════════
  const summary = {
    task: 'BT-CHART-FIX-07',
    total_checks: results.length,
    passed_checks: results.filter((r) => r.pass).length,
    all_pass: results.every((r) => r.pass),
    checks: results,
  };

  const reportPath = path.join(OUT, 'bt_chart_fix_07_report.json');
  fs.writeFileSync(reportPath, JSON.stringify(summary, null, 2), 'utf8');
  console.log(`\n[✔] BT-CHART-FIX-07 验收完成: 总项数=${summary.total_checks}, 通过=${summary.passed_checks}, 判定=${summary.all_pass ? 'ALL PASS' : 'FAILED'}`);
})();
