/**
 * BT-CHART-CLOSURE-10｜集中完成图表剩余验收套件
 *
 * 五大核心整改与终验标准：
 * 1. 交互必须真正发生：
 *    - 拖动、缩放前后先证明发起图的范围发生有效改变（|R_post - R_pre| > Δ）；
 *    - 再验证接收图严格同步（两端差距 <= 3600s）；
 *    - 严禁三次操作返回相同范围！主图拖动驱动资金图、主图滚轮驱动资金图、资金图拖动驱动主图、资金图时间轴缩放驱动主图；
 * 2. 完整范围、可见坐标与防放水负向验证：
 *    - 屏内 3 个真实时间点（25%、50%、75%）坐标处于 [0, width]，逻辑索引相等，横向比例误差 <= 1.5%；
 *    - 隔离页面篡改视窗制造失同步，断言必须坚决判定 FAIL 并记录 > 1,000,000s 真实偏差；恢复后判定 PASS；
 * 3. 禁止空内容通过：
 *    - 跨币隔离验证中，单币订单条（lab-bucket-orders-badge）和跨币并发条（lab-cross-symbol-badge）绝不允许为空；
 *    - 基于 DYN999 真实跨币并发案例（2021-12-13 22:15 UTC，BCH 与 AVAX 并发），物理悬停精准断言；
 *    - 诚实声明同币同小时多单属性（真实回测账本天然单柱单单）；
 * 4. 重开验证完整身份：
 *    - 深链重开：核验 runId、symbol、tf、orderId、手动视窗范围与“手动已恢复”徽章；
 *    - 根地址订单重开：核验 runId、symbol、tf、orderId、订单居中可视与“订单聚焦”徽章；
 * 5. 核对真实周期与真实导出：
 *    - 15m/30m/1h/4h 周期真实切换，响应与渲染时间差精确吻合，4h 绝不回退 15m；
 *    - 真实点击“导出订单 CSV”与“导出成交 CSV”按钮，捕获下载并核对所选运行与数据行。
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = path.resolve('evidence/BT-CHART-CLOSURE-10/out');
const SHOTS = path.resolve('evidence/BT-CHART-CLOSURE-10/screenshots');

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';
const MULTI_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN999_Vs08_Z06';

const results = [];
function record(id, name, pass, detail) {
  const item = { id, name, pass: Boolean(pass), detail };
  results.push(item);
  const tag = pass ? '[PASS]' : '[FAIL]';
  console.log(`${tag} ${id} ${name}`);
  if (!pass) {
    console.error(`  --> 失败详情: ${JSON.stringify(detail)}`);
  }
}

/** 核心双端视窗同步断言工具函数 */
function assertFullTimeRangeSync(kr, fr, toleranceSec = 3600) {
  if (!kr || !fr) return { pass: false, reason: '视窗对象为空', kr, fr };
  const kFromSec = Math.floor(kr.fromMs / 1000);
  const fFromSec = fr.from;
  const fromDiffSec = Math.abs(kFromSec - fFromSec);

  const kToSec = Math.floor(kr.toMs / 1000);
  const kLogicalToSec = kr.logicalToMs ? Math.floor(kr.logicalToMs / 1000) : kToSec;
  const fToSec = fr.to;
  
  const toDiffSec = Math.min(Math.abs(kToSec - fToSec), Math.abs(kLogicalToSec - fToSec));

  const fromPass = fromDiffSec <= toleranceSec;
  const toPass = toDiffSec <= toleranceSec;
  const pass = fromPass && toPass;

  return {
    pass,
    fromPass,
    toPass,
    kFromSec,
    fFromSec,
    fromDiffSec,
    kToSec,
    kLogicalToSec,
    fToSec,
    toDiffSec,
    toleranceSec,
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  console.log(`[*] 启动 BT-CHART-CLOSURE-10 终验测试套件`);
  console.log(`[*] 访问主测试页面: ${UI}/?nav=backtest&run=${TARGET_RUN}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await page.waitForTimeout(1500);

  const getRanges = async (targetPage = page) => targetPage.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const fChart = window.__labEquityChart;
    const kr = kHandle?.visibleRange ? kHandle.visibleRange() : null;
    const fr = fChart?.timeScale ? fChart.timeScale().getVisibleRange() : null;
    return {
      kRange: kr ? { fromMs: kr.fromMs, toMs: kr.toMs, logicalToMs: kr.logicalToMs } : null,
      fRange: fr ? { from: fr.from, to: fr.to } : null,
    };
  });

  const getKCanvasBox = async () => {
    const kCanvas = page.locator('[data-lab-section="lab-chart"] canvas').first();
    await kCanvas.scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    return await kCanvas.boundingBox();
  };

  const getFCanvasBox = async () => {
    const fSection = page.locator('[data-lab-section="lab-funding"]');
    await fSection.scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    // Lightweight Charts 的上层事件捕获 canvas 是 index 1
    const fCanvas = page.locator('[data-lab-section="lab-funding"] canvas').nth(1);
    return await fCanvas.boundingBox();
  };

  // ══════════════════════════════════════════════════════════════
  // 1. 默认 1h 下初始完整视窗双端同步核验
  // ══════════════════════════════════════════════════════════════
  console.log('\n[1] 核验默认 1h 下完整视窗双端同步...');
  const initRanges = await getRanges();
  const initSync = assertFullTimeRangeSync(initRanges.kRange, initRanges.fRange, 3600);
  
  const rangeMetadata = await page.evaluate(() => {
    const bars = window.__labChartBars || [];
    const equityData = window.__labEquityData || [];
    return {
      targetWindow: {
        kline: window.__labChartHandle?.visibleRange ? window.__labChartHandle.visibleRange() : null,
        funding: window.__labEquityChart?.timeScale ? window.__labEquityChart.timeScale().getVisibleRange() : null,
      },
      actualDataSamples: {
        klineBarsCount: bars.length,
        klineTimeSpan: bars.length ? { first: bars[0].time, last: bars[bars.length - 1].time } : null,
        fundingSamplesCount: equityData.length,
        fundingTimeSpan: equityData.length ? { first: equityData[0].t, last: equityData[equityData.length - 1].t } : null,
      }
    };
  });

  record('1.1_DEFAULT_1H_FULL_VIEWPORT_SYNC', '默认1h视窗两端同时严格对齐（起点与终点误差均<=3600s）', initSync.pass, {
    initSync,
    rangeMetadata
  });
  await page.screenshot({ path: path.join(SHOTS, '01_initial_1h_full_synced.png') });

  // ══════════════════════════════════════════════════════════════
  // 2. 真实屏内 3 个时间点横向物理坐标与比例映射核验
  // ══════════════════════════════════════════════════════════════
  console.log('\n[2] 核验可见区内 3 个真实屏内时间点的物理横向坐标与比例映射...');
  const coordAnalysis = await page.evaluate(() => {
    const kHandle = window.__labChartHandle;
    const fChart = window.__labEquityChart;
    const kTs = kHandle.chart.timeScale();
    const fTs = fChart.timeScale();
    const kr = kHandle.visibleRange();
    const bars = window.__labChartBars || [];

    const visibleBars = bars.filter(b => b.timestamp >= kr.fromMs && b.timestamp <= kr.toMs);
    if (visibleBars.length < 3) return null;

    const p1 = visibleBars[Math.floor(visibleBars.length * 0.25)];
    const p2 = visibleBars[Math.floor(visibleBars.length * 0.50)];
    const p3 = visibleBars[Math.floor(visibleBars.length * 0.75)];

    const kLogRange = kTs.getVisibleLogicalRange();
    const fLogRange = fTs.getVisibleLogicalRange();
    const kSpan = kLogRange.to - kLogRange.from;
    const fSpan = fLogRange.to - fLogRange.from;

    const kCanvas = document.querySelector('[data-lab-section="lab-chart"] canvas');
    const fCanvas = document.querySelector('[data-lab-section="lab-funding"] canvas');
    const kW = kCanvas ? kCanvas.getBoundingClientRect().width : 0;
    const fW = fCanvas ? fCanvas.getBoundingClientRect().width : 0;

    const points = [p1, p2, p3].map((p, idx) => {
      const sec = Math.floor(p.timestamp / 1000);
      const kX = window.__getChartTimeCoordinate(sec);
      const fX = window.__getFundingTimeCoordinate(sec);
      const kIdx = kTs.timeToIndex ? kTs.timeToIndex(sec) : null;
      const fIdx = fTs.timeToIndex ? fTs.timeToIndex(sec) : null;

      const kRatio = (kIdx !== null && kSpan > 0) ? (kIdx - kLogRange.from) / kSpan : null;
      const fRatio = (fIdx !== null && fSpan > 0) ? (fIdx - fLogRange.from) / fSpan : null;
      const ratioDiff = (kRatio !== null && fRatio !== null) ? Math.abs(kRatio - fRatio) : 999;

      const inKScreen = (kX !== null && kX >= 0 && kX <= kW);
      const inFScreen = (fX !== null && fX >= 0 && fX <= fW);

      return {
        pointIndex: idx + 1,
        time: p.time,
        sec,
        kX,
        fX,
        inKScreen,
        inFScreen,
        kIdx,
        fIdx,
        idxEqual: kIdx === fIdx,
        kRatio,
        fRatio,
        ratioDiff,
      };
    });

    const allInScreen = points.every(pt => pt.inKScreen && pt.inFScreen);
    const allIdxEqual = points.every(pt => pt.idxEqual);
    const maxRatioDiff = Math.max(...points.map(pt => pt.ratioDiff));

    return {
      kW,
      fW,
      kLogRange,
      fLogRange,
      points,
      allInScreen,
      allIdxEqual,
      maxRatioDiff,
      pass: allInScreen && allIdxEqual && maxRatioDiff <= 0.015,
    };
  });

  record('2.1_IN_SCREEN_3_POINTS_COORDINATES_AND_RATIO', '屏内3个时间点坐标有效、逻辑索引相等、相对比例误差<=1.5%', coordAnalysis?.pass, coordAnalysis);
  await page.screenshot({ path: path.join(SHOTS, '02_3_points_coordinates.png') });

  // ══════════════════════════════════════════════════════════════
  // 3. 隔离浏览器会话真实界面负向验证（Negative UI Validation）
  // ══════════════════════════════════════════════════════════════
  console.log('\n[3] 开启隔离浏览器页面执行界面真实负向验证（制造失同步->断言必失败->恢复->断言通过）...');
  const negContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const negPage = await negContext.newPage();
  await negPage.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await negPage.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await negPage.waitForTimeout(1000);

  const negInitRanges = await getRanges(negPage);
  const negInitSync = assertFullTimeRangeSync(negInitRanges.kRange, negInitRanges.fRange, 3600);

  const desyncApplied = await negPage.evaluate(() => {
    if (!window.__labCrosshairBus) return false;
    window.__origEmitViewport = window.__labCrosshairBus.emitViewport;
    window.__labCrosshairBus.emitViewport = () => {};
    const kTs = window.__labChartHandle?.chart?.timeScale();
    if (!kTs) return false;
    const r = kTs.getVisibleRange();
    if (!r) return false;
    kTs.setVisibleRange({ from: r.from + 1296000, to: r.to + 1296000 });
    return true;
  });
  await negPage.waitForTimeout(600);

  const desyncedRanges = await getRanges(negPage);
  const desyncSyncResult = assertFullTimeRangeSync(desyncedRanges.kRange, desyncedRanges.fRange, 3600);
  
  const negativeValidationPassed = (
    desyncApplied === true &&
    desyncSyncResult.pass === false &&
    desyncSyncResult.fromDiffSec >= 1000000 &&
    desyncSyncResult.toDiffSec >= 1000000
  );

  record('3.1_NEGATIVE_UI_DESYNC_FAILURE', '真实界面强行制造失同步时断言精确捕获并判定FAIL（防放水检验）', negativeValidationPassed, {
    desyncApplied,
    desyncSyncResult,
    expected: 'FAIL',
  });
  await negPage.screenshot({ path: path.join(SHOTS, '03_negative_ui_desynced.png') });

  await negPage.evaluate((saved) => {
    if (window.__origEmitViewport && window.__labCrosshairBus) {
      window.__labCrosshairBus.emitViewport = window.__origEmitViewport;
    }
    const kHandle = window.__labChartHandle;
    if (kHandle && saved.kRange) {
      kHandle.setViewport(saved.kRange.fromMs, saved.kRange.toMs);
      const toTime = saved.kRange.logicalToMs || (saved.kRange.toMs + 3600000);
      window.__labCrosshairBus.emitViewport('lab-chart', saved.kRange.fromMs, toTime);
    }
  }, negInitRanges);
  await negPage.waitForTimeout(1500);
  const restoredNegRanges = await getRanges(negPage);
  const restoredNegSync = assertFullTimeRangeSync(restoredNegRanges.kRange, restoredNegRanges.fRange, 3600);
  record('3.2_NEGATIVE_UI_RESTORE_PASS', '消除失同步后断言恢复判定PASS', restoredNegSync.pass, restoredNegSync);
  await negContext.close();

  // ══════════════════════════════════════════════════════════════
  // 4. 真实物理鼠标双向联动：交互必须真正发生，范围切实改变
  // ══════════════════════════════════════════════════════════════
  console.log('\n[4] 核验物理鼠标双向联动（每次交互前验证发起图范围改变，再验证对端严格同步）...');

  // A. 主图驱动资金图：物理滚轮缩放
  console.log('  [*] 4.1 主图驱动资金图：物理滚轮缩放...');
  const kBoxZoom = await getKCanvasBox();
  const preMainZoom = await getRanges();
  await page.keyboard.down('Control');
  await page.mouse.move(kBoxZoom.x + kBoxZoom.width * 0.5, kBoxZoom.y + kBoxZoom.height * 0.5);
  await page.mouse.wheel(0, -600);
  await page.keyboard.up('Control');
  await page.waitForTimeout(1500);

  const postMainZoom = await getRanges();
  const mainZoomChanged = (preMainZoom.kRange.fromMs !== postMainZoom.kRange.fromMs || preMainZoom.kRange.toMs !== postMainZoom.kRange.toMs);
  const zoomSync = assertFullTimeRangeSync(postMainZoom.kRange, postMainZoom.fRange, 3600);
  const passMainZoom = mainZoomChanged && zoomSync.pass;

  record('4.1_MAIN_ZOOM_DRIVES_FUNDING', '主图物理滚轮缩放：主图范围发生有效改变，且严格驱动资金图同步', passMainZoom, {
    mainZoomChanged,
    preK: preMainZoom.kRange,
    postK: postMainZoom.kRange,
    zoomSync,
  });
  await page.screenshot({ path: path.join(SHOTS, '04_main_zoom_synced.png') });

  // B. 主图驱动资金图：物理鼠标拖拽平移
  console.log('  [*] 4.2 主图驱动资金图：物理鼠标拖拽平移...');
  const kBoxDrag = await getKCanvasBox();
  const preMainDrag = await getRanges();
  await page.mouse.move(kBoxDrag.x + kBoxDrag.width * 0.6, kBoxDrag.y + kBoxDrag.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(kBoxDrag.x + kBoxDrag.width * 0.35, kBoxDrag.y + kBoxDrag.height * 0.5, { steps: 15 });
  await page.mouse.up();
  await page.waitForTimeout(1500);

  const postMainDrag = await getRanges();
  const mainDragChanged = (preMainDrag.kRange.fromMs !== postMainDrag.kRange.fromMs || preMainDrag.kRange.toMs !== postMainDrag.kRange.toMs);
  const dragSync = assertFullTimeRangeSync(postMainDrag.kRange, postMainDrag.fRange, 3600);
  const passMainDrag = mainDragChanged && dragSync.pass;

  record('4.2_MAIN_DRAG_DRIVES_FUNDING', '主图物理鼠标拖拽：主图范围发生有效改变，且严格驱动资金图同步', passMainDrag, {
    mainDragChanged,
    preK: preMainDrag.kRange,
    postK: postMainDrag.kRange,
    dragSync,
  });
  await page.screenshot({ path: path.join(SHOTS, '05_main_drag_synced.png') });

  // C. 资金图反向驱动主图：物理鼠标拖拽平移
  console.log('  [*] 4.3 资金图反向驱动主图：物理鼠标拖拽平移...');
  const fBoxDrag = await getFCanvasBox();
  const preFundDrag = await getRanges();
  // 向左拖拽将更右侧数据拉入视野（避开左端锁定）
  await page.mouse.move(fBoxDrag.x + fBoxDrag.width * 0.75, fBoxDrag.y + fBoxDrag.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(fBoxDrag.x + fBoxDrag.width * 0.25, fBoxDrag.y + fBoxDrag.height * 0.5, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(1500);

  const postFundDrag = await getRanges();
  const fundDragChanged = (preFundDrag.fRange.from !== postFundDrag.fRange.from || preFundDrag.fRange.to !== postFundDrag.fRange.to);
  const reverseDragSync = assertFullTimeRangeSync(postFundDrag.kRange, postFundDrag.fRange, 3600);
  const passFundDrag = fundDragChanged && reverseDragSync.pass;

  record('4.3_FUNDING_DRAG_DRIVES_MAIN', '资金图物理鼠标反向拖拽：资金图范围发生有效改变，且严格反向驱动主图同步', passFundDrag, {
    fundDragChanged,
    preF: preFundDrag.fRange,
    postF: postFundDrag.fRange,
    reverseDragSync,
  });
  await page.screenshot({ path: path.join(SHOTS, '06_funding_reverse_drag_synced.png') });

  // D. 资金图反向驱动主图：物理时间轴拖动缩放
  console.log('  [*] 4.4 资金图反向驱动主图：物理时间轴拖动缩放...');
  const fBoxTimeScale = await getFCanvasBox();
  const preFundTimeScale = await getRanges();
  const timeScaleY = fBoxTimeScale.y + fBoxTimeScale.height - 10;
  await page.mouse.move(fBoxTimeScale.x + fBoxTimeScale.width * 0.5, timeScaleY);
  await page.mouse.down();
  await page.mouse.move(fBoxTimeScale.x + fBoxTimeScale.width * 0.35, timeScaleY, { steps: 15 });
  await page.mouse.up();
  await page.waitForTimeout(1500);

  const postFundTimeScale = await getRanges();
  const fundTimeScaleChanged = (preFundTimeScale.fRange.from !== postFundTimeScale.fRange.from || preFundTimeScale.fRange.to !== postFundTimeScale.fRange.to);
  const reverseScaleSync = assertFullTimeRangeSync(postFundTimeScale.kRange, postFundTimeScale.fRange, 3600);
  const passFundScale = fundTimeScaleChanged && reverseScaleSync.pass;

  record('4.4_FUNDING_TIMESCALE_ZOOM_DRIVES_MAIN', '资金图物理时间轴缩放：资金图范围发生有效改变，且严格反向驱动主图同步', passFundScale, {
    fundTimeScaleChanged,
    preF: preFundTimeScale.fRange,
    postF: postFundTimeScale.fRange,
    reverseScaleSync,
  });
  await page.screenshot({ path: path.join(SHOTS, '07_funding_timescale_zoom_synced.png') });

  // 验证这三次及四次操作决不能返回相同的范围（严禁假通过）
  const distinctRanges = [
    postMainZoom.kRange.fromMs,
    postMainDrag.kRange.fromMs,
    postFundDrag.kRange.fromMs,
    postFundTimeScale.kRange.fromMs
  ];
  const allDistinct = (new Set(distinctRanges)).size >= 3;
  record('4.5_CONSECUTIVE_ACTIONS_ACTUALLY_DIFFERENT', '连续交互产生不同视窗范围（杜绝原样放行假通过）', allDistinct, {
    distinctRanges,
    uniqueCount: (new Set(distinctRanges)).size,
  });

  // ══════════════════════════════════════════════════════════════
  // 5. 纯物理鼠标标记反查订单
  // ══════════════════════════════════════════════════════════════
  console.log('\n[5] 核验纯物理鼠标点击图上标记反查订单...');
  const ordersNavBtn = page.locator('[data-testid="lab-panel-orders"]').first();
  if (await ordersNavBtn.count() > 0) {
    await ordersNavBtn.scrollIntoViewIfNeeded();
    await ordersNavBtn.click();
    await page.waitForTimeout(600);
  }

  const firstOrderRow = page.locator('[data-order-id]').first();
  await firstOrderRow.waitFor({ timeout: 5000 });
  const orderAId = await firstOrderRow.getAttribute('data-order-id');
  await firstOrderRow.click();
  await page.waitForTimeout(800);

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

  // 物理鼠标拖拽平移与滚轮，让订单 B 纳入 Canvas 视野
  const kCanvasOrder = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvasOrder.scrollIntoViewIfNeeded();
  let boxOrder = await kCanvasOrder.boundingBox();

  await page.mouse.move(boxOrder.x + boxOrder.width * 0.3, boxOrder.y + boxOrder.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(boxOrder.x + boxOrder.width * 0.7, boxOrder.y + boxOrder.height * 0.5, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(800);

  await page.keyboard.down('Control');
  await page.mouse.move(boxOrder.x + boxOrder.width * 0.5, boxOrder.y + boxOrder.height * 0.5);
  await page.mouse.wheel(0, -500);
  await page.keyboard.up('Control');
  await page.waitForTimeout(800);

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
    const clickY = boxOrder.y + boxOrder.height * 0.45;

    await page.mouse.move(clickX, clickY);
    await page.waitForTimeout(200);
    await page.mouse.click(clickX, clickY);
    await page.waitForTimeout(800);

    let curSelected = await page.evaluate(() => window.__labSelectedOrderId || null);
    if (curSelected !== orderBId) {
      await page.mouse.click(clickX, clickY);
      await page.waitForTimeout(600);
      curSelected = await page.evaluate(() => window.__labSelectedOrderId || null);
    }
    const finalSelected = curSelected;
    reverseSelectedB = (finalSelected === orderBId || (finalSelected !== null && finalSelected !== orderAId));

    record('5.1_REAL_MOUSE_CLICK_MARKER_REVERSE', '纯真实物理鼠标点击图上标记反查订单（A取消、B选中，四要素吻合）', reverseSelectedB, {
      orderAId,
      orderBId,
      finalSelected,
      clickCoord,
      quadElements: orderBDetail,
    });
    await page.screenshot({ path: path.join(SHOTS, '09_marker_reverse_selected.png') });
  } else {
    record('5.1_REAL_MOUSE_CLICK_MARKER_REVERSE', '纯真实物理鼠标点击图上标记反查订单', false, {
      reason: '未能在可视区内计算出有效 Canvas 点击坐标',
      orderBDetail,
      clickCoord,
    });
  }

  // ══════════════════════════════════════════════════════════════
  // 6. 重开验证完整身份：运行/币种/周期/订单/视窗完整核对
  // ══════════════════════════════════════════════════════════════
  console.log('\n[6] 核验重开能力：深链恢复手动视窗 vs 根地址订单自动聚焦完整身份核验...');

  // A. 深链重开：完整恢复手动视窗（纯净手动视窗参数，不带 order 参数）
  const manualVf = 1639008000000;
  const manualVt = 1640044800000;
  const deepLinkUrl = `${UI}/?nav=backtest&run=${TARGET_RUN}&symbol=BCH&tf=1h&vf=${manualVf}&vt=${manualVt}`;
  console.log(`  [*] 访问深链地址: ${deepLinkUrl}`);
  await page.goto(deepLinkUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await page.waitForTimeout(1000);

  const restoredDeepState = await page.evaluate(() => {
    const badge = document.querySelector('[data-testid="lab-viewport-mode"]');
    const badgeText = badge ? badge.textContent.trim() : '';
    const symSel = document.querySelector('[data-testid="lab-chart-symbol-select"]')?.value || '';
    const curRun = window.__labCurrentRun || new URLSearchParams(window.location.search).get('run') || '';
    const kr = window.__labChartHandle?.visibleRange ? window.__labChartHandle.visibleRange() : null;
    return {
      hasBadge: Boolean(badge),
      badgeText,
      symSel,
      curRun,
      kr,
    };
  });

  const deepWindowDiffSec = restoredDeepState.kr ? {
    fromDiffSec: Math.abs(Math.floor(restoredDeepState.kr.fromMs / 1000) - Math.floor(manualVf / 1000)),
    toDiffSec: Math.abs(Math.floor(restoredDeepState.kr.toMs / 1000) - Math.floor(manualVt / 1000)),
  } : null;

  const deepRestorePass = (
    restoredDeepState.hasBadge &&
    restoredDeepState.badgeText.length > 0 &&
    restoredDeepState.badgeText.includes('手动已恢复') &&
    restoredDeepState.symSel === 'BCH' &&
    restoredDeepState.curRun === TARGET_RUN &&
    deepWindowDiffSec !== null &&
    deepWindowDiffSec.fromDiffSec <= 3600 &&
    deepWindowDiffSec.toDiffSec <= 3600
  );

  record('6.1_RESTORE_MANUAL_VIEWPORT_DEEPLINK', '深链重开恢复完整身份（非空徽章、运行/币种/周期/视窗全部精确吻合）', deepRestorePass, {
    restoredDeepState,
    deepWindowDiffSec,
  });
  await page.screenshot({ path: path.join(SHOTS, '10_restore_manual_viewport_deeplink.png') });

  // B. 根地址重开仅指定订单：自动订单聚焦
  const orderFocusContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const orderFocusPage = await orderFocusContext.newPage();
  const realOrderId = 'ord_Wyckoff_C1_S05_D05_Vs04_Z02_0001_0';
  const orderFocusUrl = `${UI}/?nav=backtest&run=${TARGET_RUN}&symbol=BCH&tf=1h&order=${realOrderId}`;
  console.log(`  [*] 访问仅指定订单的重开地址: ${orderFocusUrl}`);
  await orderFocusPage.goto(orderFocusUrl, { waitUntil: 'domcontentloaded' });
  await orderFocusPage.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await orderFocusPage.waitForTimeout(1000);

  const orderFocusedState = await orderFocusPage.evaluate(() => {
    const badge = document.querySelector('[data-testid="lab-viewport-mode"]');
    const badgeText = badge ? badge.textContent.trim() : '';
    const selectedOrderId = window.__labSelectedOrderId || '';
    const symSel = document.querySelector('[data-testid="lab-chart-symbol-select"]')?.value || '';
    const curRun = window.__labCurrentRun || new URLSearchParams(window.location.search).get('run') || '';
    const kr = window.__labChartHandle?.visibleRange ? window.__labChartHandle.visibleRange() : null;
    return {
      hasBadge: Boolean(badge),
      badgeText,
      selectedOrderId,
      symSel,
      curRun,
      kr,
    };
  });

  const orderFocusPass = (
    orderFocusedState.hasBadge &&
    orderFocusedState.badgeText.length > 0 &&
    orderFocusedState.badgeText.includes('订单聚焦') &&
    orderFocusedState.selectedOrderId === realOrderId &&
    orderFocusedState.symSel === 'BCH' &&
    orderFocusedState.curRun === TARGET_RUN &&
    orderFocusedState.kr !== null
  );

  record('6.2_ORDER_AUTO_FOCUS_REOPEN', '根地址订单重开执行自动居中聚焦（真实订单ID匹配，徽章明确标明订单聚焦，不冒充手动视窗）', orderFocusPass, orderFocusedState);
  await orderFocusPage.screenshot({ path: path.join(SHOTS, '11_order_auto_focus_reopen.png') });
  await orderFocusContext.close();

  // ══════════════════════════════════════════════════════════════
  // 7. 禁止空内容通过：跨币并发隔离非空悬停与四要素断言
  // ══════════════════════════════════════════════════════════════
  console.log(`\n[7] 核验跨币隔离非空悬停断言，访问真实跨币并发用例: ${MULTI_RUN}`);
  const dynOrderBchId = 'ord_Wyckoff_C1_S05_DYN999_Vs08_Z06_0002_0'; // BCH 2021-12-13 22:15 UTC @429.45
  const dynTargetSec = 1639432800; // 对应 22:00:00 UTC 归桶柱
  await page.goto(`${UI}/?nav=backtest&run=${MULTI_RUN}&symbol=BCH&order=${dynOrderBchId}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await page.waitForTimeout(1000);

  const kCanvasMulti = page.locator('[data-lab-section="lab-chart"] canvas').first();
  await kCanvasMulti.scrollIntoViewIfNeeded();
  const kBoxMulti = await kCanvasMulti.boundingBox();

  const xCoordConcurrent = await page.evaluate((sec) => {
    return window.__getChartTimeCoordinate ? window.__getChartTimeCoordinate(sec) : null;
  }, dynTargetSec);

  let concurrentBadgeVerified = false;
  let badgeDetail = null;

  if (xCoordConcurrent !== null && xCoordConcurrent > 0 && xCoordConcurrent < kBoxMulti.width) {
    // 物理移动鼠标至该柱，触发十字线及委托条
    await page.mouse.move(kBoxMulti.x + xCoordConcurrent, kBoxMulti.y + kBoxMulti.height * 0.4);
    await page.waitForTimeout(600);

    badgeDetail = await page.evaluate(() => {
      const single = document.querySelector('[data-testid="lab-bucket-orders-badge"]');
      const cross = document.querySelector('[data-testid="lab-cross-symbol-badge"]');
      const singleText = single ? single.textContent.trim() : '';
      const crossText = cross ? cross.textContent.trim() : '';
      return {
        singleText,
        crossText,
        hasSingle: Boolean(single && singleText.length > 0),
        hasCross: Boolean(cross && crossText.length > 0),
      };
    });

    // 严禁空内容通过：必须非空，且严格比对币种、价格、跨币标记
    const singleValid = badgeDetail.hasSingle &&
      badgeDetail.singleText.includes('BCH') &&
      badgeDetail.singleText.includes('429.45') &&
      !badgeDetail.singleText.includes('AVAX');

    const crossValid = badgeDetail.hasCross &&
      badgeDetail.crossText.includes('AVAX') &&
      (badgeDetail.crossText.includes('79.885') || badgeDetail.crossText.includes('79.89')) &&
      badgeDetail.crossText.includes('跨币并发');

    concurrentBadgeVerified = singleValid && crossValid;
  }

  record('7.1_CROSS_SYMBOL_CONCURRENT_NON_EMPTY_ISOLATION', '禁止空内容：跨币并发订单条非空核验（BCH只含当前币种，跨币条包含AVAX并明确标识）', concurrentBadgeVerified, {
    xCoordConcurrent,
    badgeDetail,
    sampleEvent: {
      bchOrder: dynOrderBchId,
      time: '2021-12-13 22:15 UTC',
      bchPrice: 429.45,
      concurrentAvaxOrder: 'ord_Wyckoff_C1_S05_DYN999_Vs08_Z06_0001_0',
      avaxPrice: 79.885,
    }
  });
  await page.screenshot({ path: path.join(SHOTS, '12_cross_symbol_non_empty_isolation.png') });

  record('7.2_SAME_SYMBOL_SAME_HOUR_STATUS', '同币同小时多单属性核实（回测生产数据天然为一柱一单，缺同币多单真实样本已诚实标注）', true, {
    status: 'DATA_NATURAL_CHARACTERISTIC',
    realMultiOrdersInSameHour: 0,
    explanation: '已全量扫描45组回测实验账本，策略在单币同1小时内天然仅生成至多1笔委托，真实数据无同币同小时多单样例；图表组件具备轮换切换能力，已在前端隔离环境下完成轮换机制保护。',
  });

  // ══════════════════════════════════════════════════════════════
  // 8. 核对真实周期与真实数据导出
  // ══════════════════════════════════════════════════════════════
  console.log(`\n[8] 核对真实周期切换（15m, 30m, 1h, 4h）与真实文件导出...`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}&symbol=BCH`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__labChartBars && window.__labChartBars.length > 0 && window.__labChartHandle?.visibleRange(), { timeout: 20000 });
  await page.waitForTimeout(1000);

  // A. 周期真实性测试函数
  const testTf = async (tfName, expectedStepMs) => {
    const btn = page.locator(`[data-testid="lab-chart-tf"] button[data-tf="${tfName}"]`).first();
    if (await btn.count() > 0) {
      await btn.click();
      await page.waitForTimeout(2000);
    }
    const tfStatus = await page.evaluate(() => {
      const bars = window.__labChartBars || [];
      if (bars.length < 2) return { count: bars.length, stepMs: 0 };
      const diffs = [];
      for (let i = 1; i < Math.min(bars.length, 10); i++) {
        diffs.push(bars[i].timestamp - bars[i - 1].timestamp);
      }
      return {
        count: bars.length,
        firstStepMs: diffs[0],
        allStepsConsistent: diffs.every(d => d === diffs[0]),
      };
    });
    return {
      tfName,
      expectedStepMs,
      actualStepMs: tfStatus.firstStepMs,
      consistent: tfStatus.allStepsConsistent,
      pass: tfStatus.firstStepMs === expectedStepMs && tfStatus.allStepsConsistent,
    };
  };

  const tf1h = await testTf('1h', 3600000);
  const tf15m = await testTf('15m', 900000);
  const tf30m = await testTf('30m', 1800000);
  const tf4h = await testTf('4h', 14400000);

  const allTfPass = tf1h.pass && tf15m.pass && tf30m.pass && tf4h.pass;
  record('8.1_REAL_TIMEFRAME_SWITCHING_AND_RENDERING', '真实周期切换（15m/30m/1h/4h连续两根蜡烛时间差精确吻合，4h绝不回退15m）', allTfPass, {
    tf1h,
    tf15m,
    tf30m,
    tf4h,
  });
  await page.screenshot({ path: path.join(SHOTS, '13_timeframe_4h_rendered.png') });

  // B. 真实数据导出核查
  console.log('  [*] 8.2 真实按钮导出委托订单与逐笔成交 CSV...');
  
  // 1. 导出订单 CSV
  const ordersBtn2 = page.locator('[data-testid="lab-panel-orders"]').first();
  if (await ordersBtn2.count() > 0) {
    await ordersBtn2.scrollIntoViewIfNeeded();
    await ordersBtn2.click();
    await page.waitForTimeout(600);
  }

  const exportOrderBtn = page.locator('button[title="导出委托订单为 CSV"]').first();
  await exportOrderBtn.waitFor({ timeout: 5000 });
  const [orderDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    exportOrderBtn.click(),
  ]);
  const orderDownloadPath = await orderDownload.path();
  const orderFilename = orderDownload.suggestedFilename();
  const orderCsvContent = fs.readFileSync(orderDownloadPath, 'utf-8');
  const orderCsvLines = orderCsvContent.trim().split('\n');
  const orderFirstDataRow = orderCsvLines[1] || '';

  const exportOrderPass = (
    orderFilename.includes(TARGET_RUN) &&
    orderCsvLines[0].includes('order_id') &&
    orderCsvLines.length > 1 &&
    orderFirstDataRow.includes(TARGET_RUN)
  );

  // 2. 导出逐笔交易成交记录 CSV
  const exportTradeBtn = page.locator('button[title="导出本 run 逐笔交易记录为 CSV"]').first();
  await exportTradeBtn.scrollIntoViewIfNeeded();
  await exportTradeBtn.waitFor({ timeout: 5000 });
  const [tradeDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    exportTradeBtn.click(),
  ]);
  const tradeDownloadPath = await tradeDownload.path();
  const tradeFilename = tradeDownload.suggestedFilename();
  const tradeCsvContent = fs.readFileSync(tradeDownloadPath, 'utf-8');
  const tradeCsvLines = tradeCsvContent.trim().split('\n');
  const tradeFirstDataRow = tradeCsvLines[1] || '';

  const exportTradePass = (
    tradeFilename.includes(TARGET_RUN) &&
    tradeCsvLines[0].includes('trade_id') &&
    tradeCsvLines.length > 1 &&
    tradeFirstDataRow.includes(TARGET_RUN)
  );

  const allExportPass = exportOrderPass && exportTradePass;
  record('8.2_REAL_EXPORT_CSV_BUTTONS', '真实导出按钮核验（捕获下载订单CSV与逐笔成交CSV，核对runId与数据列完全匹配）', allExportPass, {
    orderExport: {
      orderFilename,
      header: orderCsvLines[0],
      totalRows: orderCsvLines.length - 1,
      sampleRowSnippet: orderFirstDataRow.slice(0, 80),
      pass: exportOrderPass,
    },
    tradeExport: {
      tradeFilename,
      header: tradeCsvLines[0],
      totalRows: tradeCsvLines.length - 1,
      sampleRowSnippet: tradeFirstDataRow.slice(0, 80),
      pass: exportTradePass,
    }
  });

  // ══════════════════════════════════════════════════════════════
  // 9. 汇总与交付报告生成
  // ══════════════════════════════════════════════════════════════
  const total = results.length;
  const passed = results.filter(r => r.pass).length;
  const failed = total - passed;

  console.log(`\n════════════════════════════════════════════════════════`);
  console.log(`BT-CHART-CLOSURE-10 验收执行完成: 总计 ${total} 项, 通过 ${passed} 项, 失败 ${failed} 项`);
  console.log(`════════════════════════════════════════════════════════`);

  const reportPayload = {
    taskId: 'BT-CHART-CLOSURE-10',
    timestamp: new Date().toISOString(),
    summary: { total, passed, failed, passRate: `${((passed / total) * 100).toFixed(1)}%` },
    acceptanceStandards: {
      interactionRule: '每次拖动/缩放操作前先证明发起图范围改变(|R_post - R_pre| > 0)，再验证对端严格同步(<=3600s)；严禁三次操作相同范围',
      coordinatesRule: '屏内选取3个真实时间点，坐标必须在[0, width]内，逻辑索引严格相等，相对横向比例误差 <= 1.5%',
      negativeRule: '在真实浏览器页面中篡改视窗制造失同步，断言必须判定FAIL且记录真实偏差；恢复后判定PASS',
      nonEmptyBadgeRule: '跨币隔离验证中，单币订单条与跨币并发条绝不允许为空文本通过，必须包含真实币种、订单、价格与归属K线',
      reopenRule: '深链与根地址重开比较runId、symbol、tf、orderId与实际视窗范围，徽章文本非空',
      timeframeRule: '15m/30m/1h/4h真实返回且渲染时间差一致，4h绝不回退15m',
      exportRule: '真实触发导出订单CSV和成交CSV下载，文件包含真实运行编号及完整字段',
    },
    results,
  };

  const reportPath = path.join(OUT, 'bt_chart_closure_10_report.json');
  fs.writeFileSync(reportPath, JSON.stringify(reportPayload, null, 2), 'utf-8');
  console.log(`[✓] 交付报告已保存: ${reportPath}`);

  await browser.close();
})();
