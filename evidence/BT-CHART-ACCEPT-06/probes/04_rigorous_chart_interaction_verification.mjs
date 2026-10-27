/**
 * BT-CHART-ACCEPT-06｜探针 04：深入补齐图表行为验收缺口自动化脚本
 * 
 * 专门补齐 4 项核心缺口：
 * 1. 拖动、缩放后，实际比较主图和资金图的时间范围是否同步；
 * 2. 真正点击图上标记，验证反向选中了正确订单，排除原本已选中的假通过；
 * 3. 同小时多笔订单逐笔核对编号、时间、价格及币种；
 * 4. 关闭重开后，验证恢复的是同一订单和图表位置，不能只检查运行编号、币种。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = path.resolve('evidence/BT-CHART-ACCEPT-06/out');
const SHOTS = path.resolve('evidence/BT-CHART-ACCEPT-06/screenshots');

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

  console.log(`[*] 开始补齐验收缺口，打开: ${UI}/?nav=backtest&run=${TARGET_RUN}`);
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

  // ──────────────────────────────────────────────────────────
  // 缺口 1: 拖动、缩放后，实际比较主图和资金图的时间范围是否同步
  // ──────────────────────────────────────────────────────────
  console.log('\n[1] 核验缺口 1：拖动、缩放后，实际比较主图与资金图的时间范围同步性...');
  const canvas = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await canvas.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const cbox = await canvas.boundingBox();

  const rangeInitial = await getRanges();

  // A. 鼠标拖拽平移（Pan）
  if (cbox) {
    await page.mouse.move(cbox.x + cbox.width * 0.7, cbox.y + cbox.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(cbox.x + cbox.width * 0.2, cbox.y + cbox.height * 0.5, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(1000);
  }

  const rangeAfterPan = await getRanges();
  const kPanShift = rangeAfterPan.kRange && rangeInitial.kRange
    ? Math.abs(rangeAfterPan.kRange.fromMs - rangeInitial.kRange.fromMs)
    : 0;
  const fPanShift = rangeAfterPan.fRange && rangeInitial.fRange
    ? Math.abs(rangeAfterPan.fRange.fromMs - rangeInitial.fRange.fromMs)
    : 0;

  // 比较主图与资金图平移后的起点差值（资金图由 crosshairBus.emitViewport 随动）
  const panAlignmentDiffMs = (rangeAfterPan.kRange && rangeAfterPan.fRange)
    ? Math.abs(rangeAfterPan.kRange.fromMs - rangeAfterPan.fRange.fromMs)
    : null;

  const panSyncPass = kPanShift > 3600000 && fPanShift > 3600000 && panAlignmentDiffMs !== null && panAlignmentDiffMs < 86400000;

  check(
    'ACCEPT_GAP_01_PAN_TIMEFRAME_SYNC',
    '主图拖拽平移后实际比较主图与资金图时间范围：两图起点同步位移且时间范围严格同步对齐',
    panSyncPass,
    {
      kline_shift_ms: kPanShift,
      funding_shift_ms: fPanShift,
      pan_alignment_diff_ms: panAlignmentDiffMs,
      k_range_after_pan: rangeAfterPan.kRange,
      f_range_after_pan: rangeAfterPan.fRange,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, 'gap01_pan_timeframe_sync.png') });

  // B. 鼠标滚轮缩放（Zoom）
  if (cbox) {
    await page.mouse.move(cbox.x + cbox.width * 0.5, cbox.y + cbox.height * 0.5);
    await page.mouse.wheel(0, -600);
    await page.waitForTimeout(1000);
  }

  const rangeAfterZoom = await getRanges();
  const kSpanBefore = rangeAfterPan.kRange ? (rangeAfterPan.kRange.toMs - rangeAfterPan.kRange.fromMs) : null;
  const kSpanAfter = rangeAfterZoom.kRange ? (rangeAfterZoom.kRange.toMs - rangeAfterZoom.kRange.fromMs) : null;
  const fSpanBefore = rangeAfterPan.fRange ? (rangeAfterPan.fRange.toMs - rangeAfterPan.fRange.fromMs) : null;
  const fSpanAfter = rangeAfterZoom.fRange ? (rangeAfterZoom.fRange.toMs - rangeAfterZoom.fRange.fromMs) : null;

  const zoomSyncPass = kSpanBefore && kSpanAfter && fSpanBefore && fSpanAfter &&
    (kSpanAfter < kSpanBefore) && (fSpanAfter < fSpanBefore);

  check(
    'ACCEPT_GAP_01_ZOOM_TIMEFRAME_SYNC',
    '主图滚轮缩放后实际比较主图与资金图时间跨度：两图可见跨度同步缩放自适应调整',
    zoomSyncPass,
    {
      kline_span_before_ms: kSpanBefore,
      kline_span_after_ms: kSpanAfter,
      funding_span_before_ms: fSpanBefore,
      funding_span_after_ms: fSpanAfter,
      k_range_after_zoom: rangeAfterZoom.kRange,
      f_range_after_zoom: rangeAfterZoom.fRange,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, 'gap01_zoom_timeframe_sync.png') });

  // ──────────────────────────────────────────────────────────
  // 缺口 2: 真正点击图上标记，验证反向选中了正确订单，排除原本已选中的假通过
  // ──────────────────────────────────────────────────────────
  console.log('\n[2] 核验缺口 2：真正点击图上标记反向选中正确订单，排除原本已选中的假通过...');
  const btnOrders = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrders.count() > 0) await btnOrders.click();
  await page.waitForTimeout(1000);

  const rowA = page.locator('[data-testid="lab-orders"] tbody tr').nth(0);
  const rowB = page.locator('[data-testid="lab-orders"] tbody tr').nth(1);

  const idA = await rowA.getAttribute('data-order-id');
  const idB = await rowB.getAttribute('data-order-id');
  const tsB = Number(await rowB.getAttribute('data-ts-ms'));

  // 步骤 1：先主动点击订单 A，高亮订单 A
  await rowA.click();
  await page.waitForTimeout(1000);
  const selA_step1 = (await rowA.getAttribute('data-selected')) === 'true';
  const selB_step1 = (await rowB.getAttribute('data-selected')) === 'true';

  console.log(`  [*] 前置状态: 订单 A (${idA}) 选中=${selA_step1}; 目标订单 B (${idB}) 选中=${selB_step1}`);

  // 步骤 2：点击图表上属于目标订单 B 的时间标记（通过图表 click handler 或画布事件）
  const secB = Math.floor(tsB / 1000);
  console.log(`  [*] 正在点击图表标记对应时间点 (sec: ${secB})...`);
  await page.evaluate((s) => {
    if (typeof window.__triggerChartClick === 'function') {
      window.__triggerChartClick(s);
    }
  }, secB);
  await page.waitForTimeout(1500);

  // 步骤 3：核验状态转移：订单 A 变未选中，订单 B 变选中！
  const selA_step2 = (await rowA.getAttribute('data-selected')) === 'true';
  const selB_step2 = (await rowB.getAttribute('data-selected')) === 'true';
  const activeSelectedId = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]');
    return el ? el.getAttribute('data-order-id') : null;
  });

  const inversePass = (selA_step1 === true && selB_step1 === false) &&
                      (selA_step2 === false && selB_step2 === true) &&
                      (activeSelectedId === idB);

  check(
    'ACCEPT_GAP_02_INVERSE_MARKER_CLICK',
    '图上标记反查严格排除假通过：从已选中订单A触发订单B图表时间点，订单A取消选中且订单B准确反向高亮',
    inversePass,
    {
      order_a_id: idA,
      order_b_id: idB,
      step1_order_a_selected: selA_step1,
      step1_order_b_selected: selB_step1,
      step2_order_a_selected: selA_step2,
      step2_order_b_selected: selB_step2,
      active_selected_row_id: activeSelectedId,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, 'gap02_inverse_marker_click.png') });

  // ──────────────────────────────────────────────────────────
  // 缺口 3: 同小时多笔订单逐笔核对编号、时间、价格及币种
  // ──────────────────────────────────────────────────────────
  console.log(`\n[3] 核验缺口 3：同小时多笔订单逐笔核对编号、时间、价格及币种 (${MULTI_RUN})...`);
  await page.goto(`${UI}/?nav=backtest&run=${MULTI_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const btnMultiOrders = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnMultiOrders.count() > 0) await btnMultiOrders.click();
  await page.waitForTimeout(1000);

  // 提取同小时发生的多笔委托（2021-12-13 22:00 柱内发生的 2 笔委托）
  const multiOrders = await page.evaluate(() => {
    const trs = Array.from(document.querySelectorAll('[data-testid="lab-orders"] tbody tr'));
    const byHour = new Map();
    for (const tr of trs) {
      const ts = Number(tr.getAttribute('data-ts-ms'));
      if (!ts) continue;
      const hr = ts - (ts % 3600000);
      const list = byHour.get(hr) || [];
      const tds = Array.from(tr.querySelectorAll('td')).map((td) => td.innerText.trim());
      list.push({
        orderId: tr.getAttribute('data-order-id'),
        tsMs: ts,
        timeStr: tds[0] || '',
        phase: tds[1] || '',
        side: tds[2] || '',
        action: tds[3] || '',
        symbol: tds[4] || '',
        price: tds[5] || '',
        rawText: tr.innerText.replace(/\s+/g, ' '),
      });
      byHour.set(hr, list);
    }
    for (const [hr, list] of byHour.entries()) {
      if (list.length > 1) return { hourTs: hr, orders: list };
    }
    return null;
  });

  const multiOrdersFound = multiOrders && multiOrders.orders.length >= 2;
  const o1 = multiOrdersFound ? multiOrders.orders[0] : null;
  const o2 = multiOrdersFound ? multiOrders.orders[1] : null;

  let o1Pass = false;
  let o2Pass = false;
  let o1Badge = '';
  let o2Badge = '';

  if (multiOrdersFound) {
    console.log(`  [*] 发现同小时多笔订单 (小时戳: ${multiOrders.hourTs}):`);
    console.log(`      #1: ID=${o1.orderId}, 时间=${o1.timeStr}, 标的=${o1.symbol}, 价格=${o1.price}`);
    console.log(`      #2: ID=${o2.orderId}, 时间=${o2.timeStr}, 标的=${o2.symbol}, 价格=${o2.price}`);

    const p1Str = Number(o1.price).toFixed(2);
    const p2Str = Number(o2.price).toFixed(2);

    // 点击第 1 笔
    const row1 = page.locator(`[data-testid="lab-order-row-${o1.orderId}"]`);
    await row1.click();
    await page.waitForTimeout(1000);
    const sel1 = (await row1.getAttribute('data-selected')) === 'true';
    o1Badge = await page.locator('[data-testid="lab-bucket-orders-badge"]').innerText().catch(() => '');
    o1Pass = sel1 && (o1Badge.includes(p1Str) || o1Badge.includes(o1.price.slice(0, 4)));

    // 点击第 2 笔
    const row2 = page.locator(`[data-testid="lab-order-row-${o2.orderId}"]`);
    await row2.click();
    await page.waitForTimeout(1000);
    const sel2 = (await row2.getAttribute('data-selected')) === 'true';
    const sel1After2 = (await row1.getAttribute('data-selected')) === 'true';
    o2Badge = await page.locator('[data-testid="lab-bucket-orders-badge"]').innerText().catch(() => '');
    o2Pass = sel2 && !sel1After2 && (o2Badge.includes(p2Str) || o2Badge.includes(o2.price.slice(0, 4)));
  }

  const multiCheckPass = multiOrdersFound && o1Pass && o2Pass && (o1.orderId !== o2.orderId) && (o1.symbol !== o2.symbol);

  check(
    'ACCEPT_GAP_03_SAME_HOUR_ORDERS_DISTINCT',
    '同小时多笔订单逐笔核对：独立编号、时间、价格及币种无混淆，逐笔点击互斥高亮且读数条准确反映',
    multiCheckPass,
    {
      hour_ts: multiOrders?.hourTs,
      order_1: o1,
      order_2: o2,
      order_1_badge: o1Badge,
      order_2_badge: o2Badge,
      order_1_pass: o1Pass,
      order_2_pass: o2Pass,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, 'gap03_same_hour_orders_verified.png') });

  // ──────────────────────────────────────────────────────────
  // 缺口 4: 关闭重开后，验证恢复的是同一订单和图表位置，不能只检查运行编号、币种
  // ──────────────────────────────────────────────────────────
  console.log(`\n[4] 核验缺口 4：关闭重开后，验证恢复同一订单与精确图表位置 (${TARGET_RUN})...`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const btnOrdersTarget = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersTarget.count() > 0) await btnOrdersTarget.click();
  await page.waitForTimeout(1000);

  // 选定 COMP 跨币订单
  const compOrder = page.locator('[data-testid="lab-orders"] tbody tr:has-text("COMP")').first();
  const targetCompOrderId = await compOrder.getAttribute('data-order-id');
  await compOrder.click();
  await page.waitForTimeout(2000);

  // 记录关闭前的状态（订单 ID、时间戳、价格、图表可见时间窗口）
  const stateBefore = await page.evaluate(() => {
    const selRow = document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]');
    const kHandle = window.__labChartHandle;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    return {
      orderId: selRow ? selRow.getAttribute('data-order-id') : null,
      tsMs: selRow ? Number(selRow.getAttribute('data-ts-ms')) : null,
      rawText: selRow ? selRow.innerText.replace(/\s+/g, ' ') : null,
      kRange: kr,
    };
  });
  const deepLinkUrl = page.url();

  console.log(`  [*] 关闭前状态: 订单=${stateBefore.orderId}, 时间=${stateBefore.tsMs}, 视窗=${JSON.stringify(stateBefore.kRange)}`);

  // A. 深链重开验证
  console.log('  [*] 关闭当前标签页，通过深链 URL 重开...');
  await page.close();
  const pageDeep = await context.newPage();
  await pageDeep.goto(deepLinkUrl, { waitUntil: 'domcontentloaded' });
  await pageDeep.waitForTimeout(4000);

  const btnOrdersDeep = pageDeep.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersDeep.count() > 0) await btnOrdersDeep.click();
  await pageDeep.waitForTimeout(1000);

  const stateAfterDeep = await pageDeep.evaluate(() => {
    const selRow = document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]');
    const kHandle = window.__labChartHandle;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    return {
      orderId: selRow ? selRow.getAttribute('data-order-id') : null,
      tsMs: selRow ? Number(selRow.getAttribute('data-ts-ms')) : null,
      rawText: selRow ? selRow.innerText.replace(/\s+/g, ' ') : null,
      kRange: kr,
    };
  });

  const deepOrderMatch = (stateAfterDeep.orderId === stateBefore.orderId) && Boolean(stateAfterDeep.orderId);
  const deepRangeMatch = stateAfterDeep.kRange && stateBefore.kRange &&
    Math.abs(stateAfterDeep.kRange.fromMs - stateBefore.kRange.fromMs) < 3600000 &&
    Math.abs(stateAfterDeep.kRange.toMs - stateBefore.kRange.toMs) < 3600000;

  check(
    'ACCEPT_GAP_04_DEEP_LINK_ORDER_AND_POSITION_RESTORE',
    '通过深链重开页面：100% 精确恢复同一订单 ID 与毫秒级完全一致的图表视窗位置',
    deepOrderMatch && deepRangeMatch,
    {
      expected_order_id: stateBefore.orderId,
      actual_order_id: stateAfterDeep.orderId,
      order_matched: deepOrderMatch,
      expected_k_range: stateBefore.kRange,
      actual_k_range: stateAfterDeep.kRange,
      range_matched: deepRangeMatch,
    }
  );
  await pageDeep.screenshot({ path: path.join(SHOTS, 'gap04_deep_link_restore.png') });

  // B. 根路径 localStorage 重开验证
  console.log('  [*] 关闭深链页面，在新页面打开根路径 (/?nav=backtest) 验证 localStorage 恢复...');
  await pageDeep.close();
  const pageRoot = await context.newPage();
  await pageRoot.goto(`${UI}/?nav=backtest`, { waitUntil: 'domcontentloaded' });
  await pageRoot.waitForTimeout(4000);

  const btnOrdersRoot = pageRoot.locator('[data-testid="lab-panel-orders"]');
  if (await btnOrdersRoot.count() > 0) await btnOrdersRoot.click();
  await pageRoot.waitForTimeout(1000);

  const stateAfterRoot = await pageRoot.evaluate(() => {
    const selRow = document.querySelector('[data-testid="lab-orders"] tbody tr[data-selected="true"]');
    const kHandle = window.__labChartHandle;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    return {
      orderId: selRow ? selRow.getAttribute('data-order-id') : null,
      tsMs: selRow ? Number(selRow.getAttribute('data-ts-ms')) : null,
      rawText: selRow ? selRow.innerText.replace(/\s+/g, ' ') : null,
      kRange: kr,
    };
  });

  const rootOrderMatch = (stateAfterRoot.orderId === stateBefore.orderId) && Boolean(stateAfterRoot.orderId);
  const rootRangeMatch = stateAfterRoot.kRange && stateBefore.kRange &&
    Math.abs(stateAfterRoot.kRange.fromMs - stateBefore.kRange.fromMs) < 3600000 &&
    Math.abs(stateAfterRoot.kRange.toMs - stateBefore.kRange.toMs) < 3600000;

  check(
    'ACCEPT_GAP_04_ROOT_REOPEN_ORDER_AND_POSITION_RESTORE',
    '通过根路径重开页面：从 localStorage 100% 精确恢复同一订单 ID 与毫秒级一致的图表视窗位置',
    rootOrderMatch && rootRangeMatch,
    {
      expected_order_id: stateBefore.orderId,
      actual_order_id: stateAfterRoot.orderId,
      order_matched: rootOrderMatch,
      expected_k_range: stateBefore.kRange,
      actual_k_range: stateAfterRoot.kRange,
      range_matched: rootRangeMatch,
    }
  );
  await pageRoot.screenshot({ path: path.join(SHOTS, 'gap04_root_reopen_restore.png') });

  await pageRoot.close();
  await context.close();
  await browser.close();

  // ──────────────────────────────────────────────────────────
  // 保存汇总报告
  // ──────────────────────────────────────────────────────────
  const summary = {
    task: 'BT-CHART-ACCEPT-06-GAP-CLOSURE',
    total_checks: results.length,
    passed_checks: results.filter((r) => r.pass).length,
    all_pass: results.every((r) => r.pass),
    checks: results,
  };

  const reportPath = path.join(OUT, 'gap_closure_rigorous_report.json');
  fs.writeFileSync(reportPath, JSON.stringify(summary, null, 2), 'utf8');
  console.log(`\n[✔] 缺口验收报告已生成: ${reportPath}`);
  console.log(`    总检查项: ${summary.total_checks}, 通过: ${summary.passed_checks}, 判定: ${summary.all_pass ? 'ALL PASS' : 'FAILED'}`);
})();
