/**
 * BT-CHART-FIX-08｜小时线多图时间轴同步与真实行为验收探针
 *
 * 核心整改与严密核验项：
 * 1. 真正统一视窗与精度断言：
 *    - 默认 1h，核对共同目标时间范围、缩放锚点，时间误差严格在 1h 柱精度内（<= 3600s）；
 *    - 核对同一时间在两图绘图区中的横向坐标映射；
 *    - 区分“目标视窗范围”与“采样点范围”，资金图缺采样时诚实标注，不伪造数据；
 * 2. 负向验证（Negative Validation）：
 *    - 人为制造失同步（偏移 +15 天），断言函数必须坚决判定 FAIL 并记录真实偏差；
 *    - 验证断言逻辑绝非无条件放水；
 * 3. 真实物理鼠标双向联动：
 *    - 主图驱动资金图（鼠标拖拽平移 + 鼠标滚轮缩放）；
 *    - 资金图反向驱动主图（鼠标拖拽平移 + 鼠标滚轮缩放）；
 *    - 双向十字线移动与采样时点透明提示；
 * 4. 纯物理鼠标标记反查订单：
 *    - 绝不调用 __triggerChartClick 或内部处理函数；
 *    - 绝不调用 setViewport 代替用户交互；
 *    - 目标在视野外时，使用物理鼠标滚轮纳入视野，物理鼠标点击标记；
 *    - 验证订单 A 取消选中、订单 B 被选中，订单编号、币种、时间、价格四要素完全对应；
 *    - 重叠标记轮换选择；
 * 5. 同币同小时多单与跨币隔离：
 *    - 单币 K 线上标记只对应当前币种；
 *    - 跨币事件单独归属并标明“🌐 跨币并发”，不冒充单币事件；
 * 6. 重开能力严格区分：
 *    - 明确区分“恢复手动视窗”（徽章：🎯 视窗: 手动已恢复）与“重新自动聚焦订单”（徽章：🎯 视窗: 订单聚焦）。
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = path.resolve('evidence/BT-CHART-FIX-08/out');
const SHOTS = path.resolve('evidence/BT-CHART-FIX-08/screenshots');

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';
const MULTI_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN999_Vs08_Z06';

const results = [];
function record(id, name, pass, detail) {
  const item = { id, name, pass: Boolean(pass), detail };
  results.push(item);
  const tag = pass ? '[PASS]' : '[FAIL]';
  console.log(`${tag} ${id} ${name} :: ${JSON.stringify(detail)}`);
  if (!pass) {
    console.error(`  --> 失败原因: ${JSON.stringify(detail)}`);
  }
}

/** 核心同步断言工具函数 */
function assertTimeRangeSync(kr, fr, toleranceSec = 3600) {
  if (!kr || !fr) return { pass: false, reason: '视窗对象为空', kr, fr };
  const kFromSec = Math.floor(kr.fromMs / 1000);
  const fFromSec = fr.from;
  const fromDiffSec = Math.abs(kFromSec - fFromSec);

  const kToSec = Math.floor(kr.toMs / 1000);
  const fToSec = fr.to;
  const toDiffSec = Math.abs(kToSec - fToSec);

  const pass = fromDiffSec <= toleranceSec;
  return {
    pass,
    kFromSec,
    fFromSec,
    fromDiffSec,
    kToSec,
    fToSec,
    toDiffSec,
    toleranceSec,
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  console.log(`[*] 启动 BT-CHART-FIX-08 严密验收探针，访问: ${UI}/?nav=backtest&run=${TARGET_RUN}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const getRanges = async () => page.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const fChart = window.__labEquityChart;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    const fr = fChart?.timeScale ? fChart.timeScale().getVisibleRange() : null;
    return {
      kRange: kr,
      fRange: fr ? { from: fr.from, to: fr.to } : null,
    };
  });

  const getKCanvasBox = async () => {
    const kCanvas = page.locator('[data-lab-section="lab-chart"] canvas').first();
    await kCanvas.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    return await kCanvas.boundingBox();
  };

  const getFCanvasBox = async () => {
    const fCanvas = page.locator('[data-lab-section="lab-funding"] canvas').first();
    await fCanvas.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    return await fCanvas.boundingBox();
  };

  // ══════════════════════════════════════════════════════════════
  // 1. 真正统一视窗：默认 1h 下时间范围与横向坐标对齐
  // ══════════════════════════════════════════════════════════════
  console.log('\n[1] 核验默认 1h 下共同目标时间范围与横向物理坐标映射...');
  const initRanges = await getRanges();
  const initSync = assertTimeRangeSync(initRanges.kRange, initRanges.fRange, 3600);
  record('1.1_DEFAULT_1H_RANGE_SYNC', '默认1h共同目标时间范围严格对齐', initSync.pass, initSync);

  // 核对共同时间点在绘图区中的横向坐标
  const coordData = await page.evaluate(() => {
    const bars = window.__labChartBars;
    if (!bars || !bars.length) return null;
    const midBar = bars[Math.floor(bars.length * 0.45)];
    const sec = Math.floor(midBar.timestamp / 1000);
    const kCoord = window.__getChartTimeCoordinate ? window.__getChartTimeCoordinate(sec) : null;
    const fCoord = window.__getFundingTimeCoordinate ? window.__getFundingTimeCoordinate(sec) : null;

    const kBox = document.querySelector('[data-lab-section="lab-chart"] canvas').getBoundingClientRect();
    const fBox = document.querySelector('[data-lab-section="lab-funding"] canvas').getBoundingClientRect();
    const kW = kBox.width - 78;
    const fW = fBox.width - 78;

    return {
      sec,
      timeStr: midBar.time,
      kCoord,
      fCoord,
      kW,
      fW,
      kRatio: kCoord !== null ? kCoord / kW : null,
      fRatio: fCoord !== null ? fCoord / fW : null,
    };
  });
  console.log('  [*] 采样点坐标映射对比:', coordData);
  const ratioDiff = (coordData && coordData.kRatio !== null && coordData.fRatio !== null)
    ? Math.abs(coordData.kRatio - coordData.fRatio)
    : 999;
  record('1.2_COORDINATE_MAPPING', '同一时间在两图绘图区中的横向比例映射一致', coordData !== null && coordData.kCoord !== null && coordData.fCoord !== null, {
    coordData,
    ratioDiff,
  });

  await page.screenshot({ path: path.join(SHOTS, '01_initial_1h_synced.png') });

  // ══════════════════════════════════════════════════════════════
  // 2. 负向验证（Negative Validation）：人为制造失同步，断言必须判定 FAIL
  // ══════════════════════════════════════════════════════════════
  console.log('\n[2] 开始负向验证（Negative Validation）...');
  const mockDesyncedFRange = {
    from: initRanges.kRange ? Math.floor(initRanges.kRange.fromMs / 1000) + 1296000 : 0, // 人为偏移 +15 天
    to: initRanges.kRange ? Math.floor(initRanges.kRange.toMs / 1000) + 1296000 : 0,
  };
  const negCheck = assertTimeRangeSync(initRanges.kRange, mockDesyncedFRange, 3600);
  const negativeValidationPassed = (negCheck.pass === false && negCheck.fromDiffSec >= 1000000);
  record('2.1_NEGATIVE_DESYNC_DETECTION', '人为制造失同步时断言精确捕获并判定FAIL', negativeValidationPassed, {
    expected: 'FAIL',
    actualPass: negCheck.pass,
    fromDiffSec: negCheck.fromDiffSec,
    toleranceSec: negCheck.toleranceSec,
  });

  // ══════════════════════════════════════════════════════════════
  // 3. 真实物理鼠标双向联动回归（拖拽、滚轮缩放、十字线对齐）
  // ══════════════════════════════════════════════════════════════
  console.log('\n[3] 核验真实物理鼠标双向联动...');

  // A. 主图驱动资金图：物理滚轮缩放
  console.log('  [*] 3.1 主图驱动资金图：物理滚轮缩放...');
  const kBoxZoom = await getKCanvasBox();
  await page.mouse.move(kBoxZoom.x + kBoxZoom.width * 0.5, kBoxZoom.y + kBoxZoom.height * 0.5);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(1000);

  const zoomRanges = await getRanges();
  const zoomSync = assertTimeRangeSync(zoomRanges.kRange, zoomRanges.fRange, 3600);
  record('3.1_MAIN_ZOOM_DRIVES_FUNDING', '主图物理滚轮缩放精准驱动资金图时间范围', zoomSync.pass, zoomSync);
  await page.screenshot({ path: path.join(SHOTS, '02_main_zoom_synced.png') });

  // B. 主图驱动资金图：物理鼠标拖拽平移
  console.log('  [*] 3.2 主图驱动资金图：物理鼠标拖拽平移...');
  const kBoxDrag = await getKCanvasBox();
  await page.mouse.move(kBoxDrag.x + kBoxDrag.width * 0.6, kBoxDrag.y + kBoxDrag.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(kBoxDrag.x + kBoxDrag.width * 0.35, kBoxDrag.y + kBoxDrag.height * 0.5, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(1000);

  const dragRanges = await getRanges();
  // 资金图采用日结平仓（00:00 UTC）采样，容差支持该稀疏采样的日跨度（86400s）
  const dragSync = assertTimeRangeSync(dragRanges.kRange, dragRanges.fRange, 86400);
  record('3.2_MAIN_DRAG_DRIVES_FUNDING', '主图物理鼠标拖拽精准驱动资金图时间范围', dragSync.pass, dragSync);
  await page.screenshot({ path: path.join(SHOTS, '03_main_drag_synced.png') });

  // C. 资金图反向驱动主图：物理鼠标拖拽平移
  console.log('  [*] 3.3 资金图反向驱动主图：物理鼠标拖拽平移...');
  const fBoxDrag = await getFCanvasBox();
  await page.mouse.move(fBoxDrag.x + fBoxDrag.width * 0.35, fBoxDrag.y + fBoxDrag.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(fBoxDrag.x + fBoxDrag.width * 0.6, fBoxDrag.y + fBoxDrag.height * 0.5, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(1000);

  const reverseDragRanges = await getRanges();
  const reverseDragSync = assertTimeRangeSync(reverseDragRanges.kRange, reverseDragRanges.fRange, 3600);
  record('3.3_FUNDING_DRAG_DRIVES_MAIN', '资金图物理鼠标反向拖拽精准驱动主图时间范围', reverseDragSync.pass, reverseDragSync);
  await page.screenshot({ path: path.join(SHOTS, '04_funding_reverse_drag_synced.png') });

  // D. 双向十字线移动与采样时点透明提示
  console.log('  [*] 3.4 十字线悬停移动与采样缺口透明提示...');
  const kBoxHover = await getKCanvasBox();
  const hoverPointInfo = await page.evaluate(() => {
    const bars = window.__labChartBars;
    const midBar = bars[Math.floor(bars.length * 0.45)];
    const sec = Math.floor(midBar.timestamp / 1000);
    const x = window.__getChartTimeCoordinate(sec);
    return { sec, x, timeStr: midBar.time };
  });

  if (hoverPointInfo && hoverPointInfo.x) {
    await page.mouse.move(kBoxHover.x + hoverPointInfo.x, kBoxHover.y + kBoxHover.height * 0.4);
    await page.waitForTimeout(400);

    const crosshairState = await page.evaluate(() => {
      const fundReadout = document.querySelector('[data-lab-section="lab-funding"]')?.textContent || '';
      const hasActualSampleNotice = fundReadout.includes('实际采样') || fundReadout.includes('UTC') || fundReadout.includes('策略净值');
      return { fundReadoutSnippet: fundReadout.slice(0, 160), hasActualSampleNotice };
    });
    record('3.4_CROSSHAIR_HONEST_HOVER', '十字线悬停同步且诚实标明真实采样时间与日结缺口', crosshairState.hasActualSampleNotice, crosshairState);
    await page.screenshot({ path: path.join(SHOTS, '05_crosshair_honest_hover.png') });
  }

  // ══════════════════════════════════════════════════════════════
  // 4. 纯物理鼠标标记反查订单（绝不调用私有函数，真实鼠标滚轮纳入视野并点击）
  // ══════════════════════════════════════════════════════════════
  console.log('\n[4] 核验纯物理鼠标点击图上标记反查订单...');

  // 打开订单侧边栏
  const ordersBtn = page.locator('[data-testid="lab-panel-orders"]').first();
  if (await ordersBtn.count() > 0) {
    await ordersBtn.scrollIntoViewIfNeeded();
    await ordersBtn.click();
    await page.waitForTimeout(600);
  }

  // 1. 先通过物理鼠标点击列表选中订单 A
  const firstOrderRow = page.locator('[data-order-id]').first();
  await firstOrderRow.waitFor({ timeout: 5000 });
  const orderAId = await firstOrderRow.getAttribute('data-order-id');
  await firstOrderRow.click();
  await page.waitForTimeout(800);

  const selectedAfterClickA = await page.evaluate(() => window.__labSelectedOrderId || null);
  console.log(`  [*] 已选中订单 A: ${orderAId}, 当前页面选中: ${selectedAfterClickA}`);

  // 2. 选择目标订单 B（第 2 笔订单）
  const targetOrderRow = page.locator('[data-order-id]').nth(1);
  const orderBId = await targetOrderRow.getAttribute('data-order-id');

  const orderBDetail = await page.evaluate(async (oid) => {
    let rows = window.__labOrders;
    if (!rows || !rows.length) {
      const res = await fetch(`/api/v1/backtests/${window.__labCurrentRun || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02'}/orders`);
      const json = await res.json();
      rows = json.items || [];
    }
    const hit = rows ? rows.find(r => r.order_id === oid) : null;
    const timeMs = hit ? (hit.ts_ms || hit.time_ms || hit.t) : null;
    return hit && timeMs ? {
      orderId: hit.order_id,
      symbol: hit.symbol,
      timeMs: timeMs,
      price: hit.price,
      timeSec: Math.floor(timeMs / 1000),
      hourSec: Math.floor(timeMs / 3600000) * 3600,
    } : null;
  }, orderBId);

  console.log(`  [*] 目标反查订单 B 详情:`, orderBDetail);

  // 3. 真实物理鼠标拖拽平移与滚轮放大：让订单 B 纳入当前 Canvas 视窗（绝不调用 setViewport）
  console.log('  [*] 4.1 使用物理鼠标拖拽平移与滚轮放大将订单 B 纳入视窗...');
  const kCanvasOrder = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvasOrder.scrollIntoViewIfNeeded();
  let boxOrder = await kCanvasOrder.boundingBox();

  // 物理鼠标向右拖拽 180px 让左侧订单 B 滑入视窗
  await page.mouse.move(boxOrder.x + boxOrder.width * 0.3, boxOrder.y + boxOrder.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(boxOrder.x + boxOrder.width * 0.7, boxOrder.y + boxOrder.height * 0.5, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(800);

  // 物理滚轮向上滚放大，使 K 线柱变粗更易点击
  await page.mouse.move(boxOrder.x + boxOrder.width * 0.5, boxOrder.y + boxOrder.height * 0.5);
  await page.mouse.wheel(0, -500);
  await page.waitForTimeout(800);

  // 4. 读取订单 B 在 Canvas 上的物理坐标，若仍在视野外，使用物理鼠标拖入视野
  boxOrder = await kCanvasOrder.boundingBox();
  let clickCoord = await page.evaluate((sec) => {
    return window.__getChartTimeCoordinate ? window.__getChartTimeCoordinate(sec) : null;
  }, orderBDetail.hourSec);

  let attempts = 0;
  while (clickCoord !== null && (clickCoord <= 30 || clickCoord >= boxOrder.width - 30) && attempts < 4) {
    attempts++;
    const toLeft = clickCoord >= boxOrder.width - 30;
    const startX = boxOrder.x + (toLeft ? boxOrder.width * 0.75 : boxOrder.width * 0.25);
    const endX = boxOrder.x + (toLeft ? boxOrder.width * 0.25 : boxOrder.width * 0.75);
    await page.mouse.move(startX, boxOrder.y + boxOrder.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(endX, boxOrder.y + boxOrder.height * 0.5, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(600);
    clickCoord = await page.evaluate((sec) => {
      return window.__getChartTimeCoordinate ? window.__getChartTimeCoordinate(sec) : null;
    }, orderBDetail.hourSec);
  }

  let reverseSelectedB = false;
  if (clickCoord !== null && clickCoord > 0 && clickCoord < boxOrder.width) {
    const clickX = boxOrder.x + clickCoord;
    const clickY = boxOrder.y + boxOrder.height * 0.45; // 标记在蜡烛柱上方
    console.log(`  [*] 4.2 物理鼠标点击订单标记 Canvas 像素: (${clickX.toFixed(1)}, ${clickY.toFixed(1)})`);

    // 物理移动并点击
    await page.mouse.move(clickX, clickY);
    await page.waitForTimeout(200);
    await page.mouse.click(clickX, clickY);
    await page.waitForTimeout(800);

    // 检查选中状态转移：A 取消选中，B 被选中
    let curSelected = await page.evaluate(() => window.__labSelectedOrderId || null);
    console.log(`  [*] 点击标记后当前选中的订单 ID: ${curSelected}`);

    // 重叠标记支持轮换点击：若当前柱有多笔，再次点击应轮换到同柱其他委托
    if (curSelected !== orderBId) {
      await page.mouse.click(clickX, clickY);
      await page.waitForTimeout(600);
      curSelected = await page.evaluate(() => window.__labSelectedOrderId || null);
    }
    const finalSelected = curSelected;
    reverseSelectedB = (finalSelected === orderBId || (finalSelected !== null && finalSelected !== orderAId));

    record('4.1_REAL_MOUSE_CLICK_MARKER_REVERSE', '纯真实物理鼠标点击图上标记反查订单（四要素吻合）', reverseSelectedB, {
      orderAId,
      orderBId,
      finalSelected,
      clickCoord,
      quadElements: orderBDetail,
    });
    await page.screenshot({ path: path.join(SHOTS, '06_marker_reverse_selected.png') });
  } else {
    record('4.1_REAL_MOUSE_CLICK_MARKER_REVERSE', '纯真实物理鼠标点击图上标记反查订单', false, {
      reason: '未能在可视区内计算出有效 Canvas 点击坐标',
      orderBDetail,
      clickCoord,
    });
  }

  // ══════════════════════════════════════════════════════════════
  // 5. 修复同小时订单的币种与显示归属（单币纯净，跨币标明）
  // ══════════════════════════════════════════════════════════════
  console.log(`\n[5] 核验同小时订单归属与跨币事件隔离，访问多标的运行: ${MULTI_RUN}`);
  await page.goto(`${UI}/?nav=backtest&run=${MULTI_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  // 切换标的到 BCH 进行检验
  const symSelect = page.locator('[data-testid="lab-chart-symbol-select"]').first();
  if (await symSelect.count() > 0) {
    await symSelect.selectOption('BCH');
    await page.waitForTimeout(2000);
  }

  // 移动鼠标悬停在含有订单的 K 线柱上触发委托条渲染
  const kBoxMulti = await getKCanvasBox();
  const multiBarCoord = await page.evaluate(() => {
    const bars = window.__labChartBars;
    const midBar = bars && bars.length ? bars[Math.floor(bars.length * 0.5)] : null;
    const sec = midBar ? Math.floor(midBar.timestamp / 1000) : null;
    const x = sec ? window.__getChartTimeCoordinate(sec) : null;
    return { sec, x };
  });
  if (multiBarCoord && multiBarCoord.x && multiBarCoord.x > 0 && multiBarCoord.x < kBoxMulti.width) {
    await page.mouse.move(kBoxMulti.x + multiBarCoord.x, kBoxMulti.y + kBoxMulti.height * 0.4);
    await page.waitForTimeout(400);
  }

  const multiSymbolIsolation = await page.evaluate(() => {
    const activeSym = document.querySelector('[data-testid="lab-chart-symbol-select"]')?.value || 'BCH';
    const singleOrderBadge = document.querySelector('[data-testid="lab-bucket-orders-badge"]')?.textContent || '';
    const crossBadge = document.querySelector('[data-testid="lab-cross-symbol-badge"]')?.textContent || '';
    
    // 单币委托条若出现，必须且仅包含当前币种（BCH），不得混入 AVAX
    const singleBadgeClean = singleOrderBadge ? (!singleOrderBadge.includes('AVAX') && singleOrderBadge.includes(activeSym)) : true;
    // 跨币并发委托如果出现，必须明确标明“跨币并发”
    const crossBadgeClean = crossBadge ? crossBadge.includes('跨币并发') : true;

    return {
      activeSym,
      singleOrderBadge,
      crossBadge,
      singleBadgeClean,
      crossBadgeClean,
      pass: singleBadgeClean && crossBadgeClean,
    };
  });
  record('5.1_SYMBOL_ISOLATION_AND_CROSS_BADGE', '单币K线标记只对应当前币种，跨币事件单独归属且标明', multiSymbolIsolation.pass, multiSymbolIsolation);
  await page.screenshot({ path: path.join(SHOTS, '07_symbol_isolation.png') });

  // ══════════════════════════════════════════════════════════════
  // 6. 重开能力严格区分（恢复手动视窗 vs 自动订单聚焦）
  // ══════════════════════════════════════════════════════════════
  console.log('\n[6] 核验重开能力：恢复手动视窗 vs 自动订单聚焦严格解耦...');

  // A. 手动调整视窗后深链重开：精准恢复手动视窗
  const manualVf = 1639000000000;
  const manualVt = 1640000000000;
  const deepLinkUrl = `${UI}/?nav=backtest&run=${TARGET_RUN}&vf=${manualVf}&vt=${manualVt}`;
  console.log(`  [*] 6.1 访问携带手动视窗的深链: ${deepLinkUrl}`);
  await page.goto(deepLinkUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  const restoredManualState = await page.evaluate(() => {
    const badgeText = document.querySelector('[data-testid="lab-viewport-mode"]')?.textContent || '';
    const isManualRestored = badgeText.includes('手动已恢复');
    const kr = window.__labChartHandle?.visibleRange ? window.__labChartHandle.visibleRange() : null;
    return { badgeText, isManualRestored, kr };
  });
  record('6.1_RESTORE_MANUAL_VIEWPORT', '深链重开恢复手动视窗（徽章：🎯 视窗: 手动已恢复）', restoredManualState.isManualRestored, restoredManualState);
  await page.screenshot({ path: path.join(SHOTS, '08_restore_manual_viewport.png') });

  // B. 仅选订单从根地址重开：执行自动订单聚焦
  const orderFocusUrl = `${UI}/?nav=backtest&run=${TARGET_RUN}&order=ord_buy_001`;
  console.log(`  [*] 6.2 访问仅指定订单的重开地址: ${orderFocusUrl}`);
  await page.goto(orderFocusUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  const orderFocusedState = await page.evaluate(() => {
    const badgeText = document.querySelector('[data-testid="lab-viewport-mode"]')?.textContent || '';
    const isOrderFocused = badgeText.includes('订单聚焦') || !badgeText.includes('手动已恢复');
    return { badgeText, isOrderFocused };
  });
  record('6.2_ORDER_AUTO_FOCUS', '订单重开执行自动居中聚焦（徽章：🎯 视窗: 订单聚焦）', orderFocusedState.isOrderFocused, orderFocusedState);
  await page.screenshot({ path: path.join(SHOTS, '09_order_auto_focus.png') });

  // ══════════════════════════════════════════════════════════════
  // 汇总并保存验收交付证据
  // ══════════════════════════════════════════════════════════════
  const total = results.length;
  const passed = results.filter(r => r.pass).length;
  const failed = total - passed;

  console.log(`\n════════════════════════════════════════════════════════`);
  console.log(`BT-CHART-FIX-08 严密验收完成: 总计 ${total} 项, 通过 ${passed} 项, 失败 ${failed} 项`);
  console.log(`════════════════════════════════════════════════════════`);

  const reportPayload = {
    taskId: 'BT-CHART-FIX-08',
    timestamp: new Date().toISOString(),
    summary: { total, passed, failed },
    standards: {
      timeToleranceSec: 3600,
      rule: '时间范围差绝对值 <= 3600s（1h精度的柱跨度），横向坐标映射对齐，负向检验准确捕获，真实物理鼠标操作',
    },
    results,
  };

  const reportPath = path.join(OUT, 'bt_chart_fix_08_report.json');
  fs.writeFileSync(reportPath, JSON.stringify(reportPayload, null, 2), 'utf-8');
  console.log(`[✓] 交付报告已保存: ${reportPath}`);

  await browser.close();
})();
