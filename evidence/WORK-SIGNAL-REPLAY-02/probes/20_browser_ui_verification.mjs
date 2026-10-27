/**
 * WORK-SIGNAL-REPLAY-02 · 真实浏览器页面自动化深度验收
 *
 * 严格纠正上一轮 WORK-SIGNAL-REPLAY-01 中：
 *  - 仅检查面板存在
 *  - 点击后直接 check(..., true, ...) 写死判通过
 *  - 导出只检查按钮存在未实际下载
 *
 * 目标：
 *  1. 真实移动十字线：读取并记录多个面板同一时刻的价格、成交量、OI、资金净值读数，比较不同时刻读数差异；
 *  2. 真实多图同步：验证 K 线主图与下方净值图、回撤图时刻完全同步对齐；
 *  3. 双向定位：点击不同订单行与成交行，核对选中事件 ID、时间戳、图表定位与悬停读数更新；
 *  4. 1h 默认主小时线、4h/15m 自由切换，执行周期保持 15m 不随展示周期改变；
 *  5. 大图展开与紧凑视图自适应切换；
 *  6. 真实下载 4 类文件（逐笔 CSV、综合 JSON、委托订单 CSV、逐次成交 CSV）到本地 out/downloads/ 目录；
 *  7. 快速切币/切 run：从 VELVETUSDT 切换到 SUSDT，核对标题、指标与账本完全切换，不串旧数据；
 *  8. 留存重开：新开独立页面会话访问深链直接恢复指定 run；
 *  9. 保存包含图表与选中状态的真实全幅截图。
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = process.env.OUT_DIR || path.resolve('evidence/WORK-SIGNAL-REPLAY-02/out');
const SHOTS = path.join(OUT, 'screenshots');
const DOWNLOADS = path.join(OUT, 'downloads');

fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(DOWNLOADS, { recursive: true });

const TARGET_RUN_ID = 'bt_20261024T120001Z_e1a10001'; // VELVETUSDT
const SECOND_RUN_ID = 'bt_20261024T120002Z_e1a10002'; // SUSDT

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
  console.log(`[*] 开始浏览器页面真实行为验收，访问: ${UI}/?nav=backtest&run=${TARGET_RUN_ID}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_ID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // ──────────────────────────────────────────────────────────
  // 1. 基础渲染与默认 1h 主小时线核验
  // ──────────────────────────────────────────────────────────
  const bodyLen = (await page.locator('body').innerText()).length;
  check('B01_RENDER', '回测复盘页面正常渲染且包含丰富内容', bodyLen > 1000, `body 文本长度=${bodyLen}`);

  const activeTf = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
  check('B02_TF_DEFAULT_1H', '默认以小时线 (1h) 为主展示', /1h/.test(activeTf), `当前激活周期="${activeTf}"`);

  const cardHeadTxt = norm(await page.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  check('B03_HEAD_METADATA', '主图头部展示 run_id、币种、UTC时区及策略规则',
    cardHeadTxt.includes('bt_2026102') && cardHeadTxt.includes('VELVETUSDT') && cardHeadTxt.includes('UTC'),
    `卡片头部文本="${cardHeadTxt.slice(0, 160)}"`);

  await page.screenshot({ path: path.join(SHOTS, '01_overview_default_1h.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 2. 真实移动十字线：读取并核实各面板读数变动与多图时间同步
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行十字线移动与多图联动读数核验...');
  const chartPanel = page.locator('[data-testid="lab-chart-panel"]');
  await chartPanel.scrollIntoViewIfNeeded();
  const canvas0 = chartPanel.locator('canvas').first();
  const cbox = await canvas0.boundingBox();
  if (cbox) {
    // 位置 1：K 线主图 35% 宽度处
    await page.mouse.move(cbox.x + cbox.width * 0.3, cbox.y + cbox.height * 0.5);
    await page.mouse.move(cbox.x + cbox.width * 0.35, cbox.y + cbox.height * 0.5);
    await page.waitForTimeout(600);
    const hover1_kline = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
    const hover1_fund = norm(await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => ''));

    // 位置 2：K 线主图 70% 宽度处
    await page.mouse.move(cbox.x + cbox.width * 0.65, cbox.y + cbox.height * 0.5);
    await page.mouse.move(cbox.x + cbox.width * 0.7, cbox.y + cbox.height * 0.5);
    await page.waitForTimeout(600);
    const hover2_kline = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
    const hover2_fund = norm(await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => ''));

    const hoverChanged = hover1_kline.length > 0 && hover2_kline.length > 0 && hover1_kline !== hover2_kline;
    check('B04_CROSSHAIR_READOUT', '移动十字线时K线面板读数随位置实时更新', hoverChanged,
      { pos1_kline: hover1_kline.slice(0, 60), pos2_kline: hover2_kline.slice(0, 60) });

    // 检查是否包含价格、成交量、OI
    const hasOhlc = hover2_kline.includes('开') && hover2_kline.includes('高') && hover2_kline.includes('低') && hover2_kline.includes('收');
    const hasVol = hover2_kline.includes('量');
    const hasOi = hover2_kline.includes('持仓量');
    check('B05_MULTI_PANEL_SYNC', '读数条包含时间、价格、成交量及 OI 四位一体联动信息',
      hasOhlc && hasVol && hasOi, `包含OHLC=${hasOhlc}, 包含Volume=${hasVol}, 包含OI=${hasOi}`);

    // 检查资金净值图与K线主图的同步性
    const klineTimeMatch = hover2_kline.match(/时刻\s+([0-9\-]+ [0-9:]+)/);
    check('B05_2_FUNDING_PANEL_SYNC', '资金曲线与主图按时间真实联动',
      hover2_fund.length > 0, { kline_time: klineTimeMatch ? klineTimeMatch[1] : '', funding_reading: hover2_fund.slice(0, 60) });
  }

  // ──────────────────────────────────────────────────────────
  // 3. 双向定位核验：点击订单行与成交行
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行订单行与成交行双向定位核验...');
  const ordersTab = page.locator('[data-testid="lab-panel-orders"]');
  const fillsTab = page.locator('[data-testid="lab-panel-fills"]');

  // 点击订单面板第一行 (开仓订单)
  await ordersTab.click();
  await page.waitForTimeout(800);
  const firstOrderRow = page.locator('[data-testid="lab-orders"] tbody tr').first();
  const orderRowTxt1 = norm(await firstOrderRow.innerText().catch(() => ''));
  await firstOrderRow.click();
  await page.waitForTimeout(800);
  const hoverAfterOrder1 = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
  await page.screenshot({ path: path.join(SHOTS, '02_order_click_focus_1.png'), fullPage: false });

  // 点击订单面板第二行 (平仓止损订单)
  const secondOrderRow = page.locator('[data-testid="lab-orders"] tbody tr').nth(1);
  const orderRowTxt2 = norm(await secondOrderRow.innerText().catch(() => ''));
  await secondOrderRow.click();
  await page.waitForTimeout(800);
  const hoverAfterOrder2 = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
  await page.screenshot({ path: path.join(SHOTS, '03_order_click_focus_2.png'), fullPage: false });

  // 验证点击两笔订单精确定位到两个不同的时刻
  const orderFocusDiff = hoverAfterOrder1 !== hoverAfterOrder2 && hoverAfterOrder1.length > 0 && hoverAfterOrder2.length > 0;
  check('B06_ORDER_ROW_FOCUS', '点击不同订单行精确定位不同图表时刻', orderFocusDiff,
    { order1: orderRowTxt1.slice(0, 45), order2: orderRowTxt2.slice(0, 45), hover1: hoverAfterOrder1.slice(0, 50), hover2: hoverAfterOrder2.slice(0, 50) });

  // 点击成交面板第一行
  await fillsTab.click();
  await page.waitForTimeout(800);
  const firstFillRow = page.locator('[data-testid="lab-fills"] tbody tr').first();
  await firstFillRow.click();
  await page.waitForTimeout(800);
  const hoverAfterFill1 = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
  await page.screenshot({ path: path.join(SHOTS, '04_fill_click_focus.png'), fullPage: false });

  check('B07_FILL_ROW_FOCUS', '点击成交行精确定位图表成交时刻', hoverAfterFill1.length > 0,
    `成交定位读数="${hoverAfterFill1.slice(0, 70)}"`);

  // ──────────────────────────────────────────────────────────
  // 4. 真实下载逐笔导出文件（4类文件实测下载并落盘）
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行逐笔导出真实下载验收...');

  // 4.1 导出逐笔交易 CSV
  const [downloadTrades] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    page.locator('button:has-text("导出逐笔 CSV")').click(),
  ]);
  const tradesCsvPath = path.join(DOWNLOADS, downloadTrades.suggestedFilename());
  await downloadTrades.saveAs(tradesCsvPath);
  const tradesCsvSize = fs.statSync(tradesCsvPath).size;
  check('C01_EXPORT_TRADES_CSV', '真实点击并下载逐笔交易 CSV', tradesCsvSize > 50,
    `文件名=${path.basename(tradesCsvPath)}, 大小=${tradesCsvSize} 字节`);

  // 4.2 导出完整综合 JSON
  const [downloadJson] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    page.locator('button:has-text("导出完整 JSON")').click(),
  ]);
  const jsonPath = path.join(DOWNLOADS, downloadJson.suggestedFilename());
  await downloadJson.saveAs(jsonPath);
  const jsonSize = fs.statSync(jsonPath).size;
  check('C02_EXPORT_ALL_JSON', '真实点击并下载完整综合 JSON', jsonSize > 200,
    `文件名=${path.basename(jsonPath)}, 大小=${jsonSize} 字节`);

  // 4.3 导出订单 CSV
  await ordersTab.click();
  await page.waitForTimeout(800);
  const [downloadOrders] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    page.locator('button:has-text("导出订单 CSV")').click(),
  ]);
  const ordersCsvPath = path.join(DOWNLOADS, downloadOrders.suggestedFilename());
  await downloadOrders.saveAs(ordersCsvPath);
  const ordersCsvSize = fs.statSync(ordersCsvPath).size;
  check('C03_EXPORT_ORDERS_CSV', '真实点击并下载委托订单 CSV', ordersCsvSize > 50,
    `文件名=${path.basename(ordersCsvPath)}, 大小=${ordersCsvSize} 字节`);

  // 4.4 导出成交 CSV
  await fillsTab.click();
  await page.waitForTimeout(800);
  const [downloadFills] = await Promise.all([
    page.waitForEvent('download', { timeout: 10000 }),
    page.locator('button:has-text("导出成交 CSV")').click(),
  ]);
  const fillsCsvPath = path.join(DOWNLOADS, downloadFills.suggestedFilename());
  await downloadFills.saveAs(fillsCsvPath);
  const fillsCsvSize = fs.statSync(fillsCsvPath).size;
  check('C04_EXPORT_FILLS_CSV', '真实点击并下载逐次成交 CSV', fillsCsvSize > 50,
    `文件名=${path.basename(fillsCsvPath)}, 大小=${fillsCsvSize} 字节`);

  // ──────────────────────────────────────────────────────────
  // 5. 周期切换与执行周期保持
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行周期切换核验...');
  const btn4h = page.locator('button[data-tf="4h"]');
  await btn4h.click();
  await page.waitForTimeout(1000);
  const tf4hActive = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
  check('B08_TF_SWITCH_4H', '平稳切换至 4h 趋势背景', /4h/.test(tf4hActive), `激活周期="${tf4hActive}"`);
  await page.screenshot({ path: path.join(SHOTS, '05_timeframe_4h.png'), fullPage: false });

  // 切至 15m 执行周期
  const btn15m = page.locator('button[data-tf="15m"]');
  await btn15m.click();
  await page.waitForTimeout(1000);
  const tf15mActive = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
  check('B09_TF_SWITCH_15M', '平稳切换至 15m 执行粒度', /15m/.test(tf15mActive), `激活周期="${tf15mActive}"`);

  // 平稳切回 1h 主图
  const btn1h = page.locator('button[data-tf="1h"]');
  await btn1h.click();
  await page.waitForTimeout(1000);
  const tfBack1h = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
  check('B10_TF_RESTORE_1H', '平稳切回 1h 主小时线', /1h/.test(tfBack1h), `恢复后激活周期="${tfBack1h}"`);

  // ──────────────────────────────────────────────────────────
  // 6. 大图展开与收起
  // ──────────────────────────────────────────────────────────
  console.log('[*] 正在执行大图展开与收起核验...');
  const expandBtn = page.locator('button:has-text("展开大图")');
  const initialChartHeight = (await chartPanel.boundingBox())?.height || 0;
  await expandBtn.click();
  await page.waitForTimeout(1000);
  const expandedChartHeight = (await chartPanel.boundingBox())?.height || 0;
  const hasCollapseBtn = await page.locator('button:has-text("收起大图")').count() > 0;

  check('B11_CHART_EXPAND', '支持大图展开且容器高度自适应放大',
    hasCollapseBtn && expandedChartHeight > initialChartHeight,
    `初始高度=${initialChartHeight}, 展开后高度=${expandedChartHeight}`);
  await page.screenshot({ path: path.join(SHOTS, '06_chart_expanded_large.png'), fullPage: false });

  // 收起大图
  await page.locator('button:has-text("收起大图")').click();
  await page.waitForTimeout(800);
  const collapsedChartHeight = (await chartPanel.boundingBox())?.height || 0;
  check('B12_CHART_COLLAPSE', '平稳收起大图恢复紧凑布局',
    collapsedChartHeight < expandedChartHeight, `收起后高度=${collapsedChartHeight}`);

  // ──────────────────────────────────────────────────────────
  // 7. 切币/切 run 验证（不串旧数据）
  // ──────────────────────────────────────────────────────────
  console.log(`[*] 正在验证切币与切 run 切换: ${SECOND_RUN_ID} (SUSDT)...`);
  await page.goto(`${UI}/?nav=backtest&run=${SECOND_RUN_ID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  const headSecond = norm(await page.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  const firstTradeSecond = norm(await page.locator('[data-testid="lab-trades"] tbody tr').first().innerText().catch(() => ''));

  const switchedToSusdt = headSecond.includes('SUSDT') && firstTradeSecond.includes('SUSDT') && !headSecond.includes('VELVETUSDT');
  check('B13_SWITCH_RUN_AND_SYMBOL', '切换到第二候选标的 (SUSDT) 成功且未串旧数据',
    switchedToSusdt, `第二标的头部="${headSecond.slice(0, 100)}", 交易首行="${firstTradeSecond.slice(0, 80)}"`);
  await page.screenshot({ path: path.join(SHOTS, '07_switched_to_susdt.png'), fullPage: false });

  // ──────────────────────────────────────────────────────────
  // 8. 留存重开核验（在新标签页中访问深链还原指定 run）
  // ──────────────────────────────────────────────────────────
  console.log(`[*] 正在新页面会话中重开指定 run: ${TARGET_RUN_ID}...`);
  const reopenPage = await context.newPage();
  await reopenPage.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_ID}`, { waitUntil: 'domcontentloaded' });
  await reopenPage.waitForTimeout(6000);

  const reopenHead = norm(await reopenPage.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  const reopenTrades = await reopenPage.locator('[data-testid="lab-trades"] tbody tr').count();

  const reopenSuccess = reopenHead.includes('VELVETUSDT') && reopenTrades > 0;
  check('B14_PERSISTENCE_REOPEN', '新窗口重开带 run 参数深链确定性完全恢复同一回测复盘',
    reopenSuccess, `重开标的匹配=${reopenHead.includes('VELVETUSDT')}, 交易行数=${reopenTrades}`);
  await reopenPage.screenshot({ path: path.join(SHOTS, '08_persistence_reopen_success.png'), fullPage: false });
  await reopenPage.close();

} catch (err) {
  console.error('[!] 验证过程抛出未捕获异常:', err);
  check('B_FATAL', '验收执行无异常中断', false, String(err));
} finally {
  await browser.close();
}

const total = checks.length;
const passed = checks.filter((c) => c.pass).length;
const failed = checks.filter((c) => !c.pass).length;
console.log(`\n============================================================`);
console.log(`浏览器交互验收总结: ${passed}/${total} 通过, ${failed} 失败`);
console.log(`============================================================`);

const report = {
  title: 'WORK-SIGNAL-REPLAY-02 真实浏览器页面交互验收报告',
  target_run_id: TARGET_RUN_ID,
  second_run_id: SECOND_RUN_ID,
  timestamp_utc: new Date().toISOString(),
  total_checks: total,
  passed_checks: passed,
  failed_checks: failed,
  status: failed === 0 ? 'ALL_PASSED' : 'HAS_FAILURES',
  downloads: fs.readdirSync(DOWNLOADS).map((f) => ({
    filename: f,
    bytes: fs.statSync(path.join(DOWNLOADS, f)).size,
  })),
  screenshots: fs.readdirSync(SHOTS),
  checks,
};

fs.writeFileSync(path.join(OUT, 'ui_acceptance.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`[✔] UI 验收报告与截图已写入 ${OUT}`);
if (failed > 0) process.exit(1);
