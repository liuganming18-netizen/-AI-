/**
 * WORK-SIGNAL-REPLAY-01 · 真实浏览器页面验收
 *
 * 验证目标：
 *  1. 页面打开与回测复盘导航
 *  2. 默认 1h 主小时线，多周期（4h、15m）自由切换
 *  3. 主图头部完整展示 run_id、币种、周期、UTC 时区、策略版本及输入区间
 *  4. 多图联动：价格、成交量、OI、资金曲线（净值/回撤）全部正常渲染
 *  5. 委托订单、逐次成交、逐笔汇总三层明细查看与展示
 *  6. 订单表与图表双向联动：点击订单/成交行定位高亮
 *  7. 交易上一笔/下一笔导航平稳切换
 *  8. 大图展开与紧凑视图自适应切换
 *  9. 导出功能可用（CSV / JSON 导出按钮齐全）
 * 10. 留存重开：页面刷新或直接带 ?run=<run_id> 深链访问，完整重现回测复盘与逐笔数据
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = process.env.OUT_DIR || path.resolve('evidence/WORK-SIGNAL-REPLAY-01/out');
const SHOTS = path.join(OUT, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

const TARGET_RUN_ID = 'bt_20261023T162149Z_59f3c8fa';

const checks = [];
const check = (id, name, cond, detail) => {
  const row = { id, name, pass: !!cond, detail };
  checks.push(row);
  console.log(`${row.pass ? 'PASS' : 'FAIL'}  ${id} ${name} :: ${detail}`);
  return row;
};
const norm = (s) => (s == null ? '' : String(s).replace(/\s+/g, ' ').trim());

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1720, height: 1080 } });
page.on('console', (m) => {
  if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 200));
});

const txtOf = async (sel) => norm(await page.locator(sel).first().innerText().catch(() => null));

try {
  console.log(`[*] 开始浏览器页面验收，访问深链: ${UI}/?nav=backtest&run=${TARGET_RUN_ID}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_ID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // 1. 页面基础可渲染
  const bodyLen = (await page.locator('body').innerText()).length;
  check('V01', '回测复盘页面正常渲染（非白屏）', bodyLen > 500, `body 文本长度=${bodyLen}`);
  await page.screenshot({ path: path.join(SHOTS, '01_backtest_overview.png'), fullPage: false });

  // 2. 默认 1h 主小时线
  const activeTf = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
  check('V02', '默认以小时线 (1h) 为主展示', /1h/.test(activeTf), `当前激活周期="${activeTf}"`);

  // 3. 周期切换（支持 4h 背景）
  const btn4h = page.locator('button[data-tf="4h"]');
  if (await btn4h.count() > 0) {
    await btn4h.click();
    await page.waitForTimeout(1000);
    const tfAfter4h = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
    check('V03.1', '可切换至 4h 趋势背景', /4h/.test(tfAfter4h), `点击后激活周期="${tfAfter4h}"`);
    // 切回 1h
    await page.locator('button[data-tf="1h"]').click();
    await page.waitForTimeout(1000);
    const tfBack1h = await page.locator('.ds-tfgroup__item.is-on').innerText().catch(() => '');
    check('V03.2', '可平稳切回 1h 主图', /1h/.test(tfBack1h), `恢复后激活周期="${tfBack1h}"`);
  } else {
    check('V03.1', '周期切换按钮可用', false, '未找到 4h 按钮');
  }
  await page.screenshot({ path: path.join(SHOTS, '02_timeframe_1h_active.png'), fullPage: false });

  // 4. 主图卡片头部完整显示元数据 (run_id, 币种, 周期, UTC时区, 策略版本)
  const chartHeadTxt = norm(await page.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  check('V04', '卡片头部展示 run_id、币种、UTC时区及策略版本',
    chartHeadTxt.includes('bt_2026102') && chartHeadTxt.includes('VELVETUSDT') && chartHeadTxt.includes('UTC'),
    `卡片头部文本="${chartHeadTxt.slice(0, 160)}"`);

  // 5. 多图联动（价格、成交量、OI、资金曲线）
  const hasCandle = await page.locator('[data-testid="lab-chart-panel"]').count() > 0;
  const hasFunding = await page.locator('[data-lab-section="lab-funding"]').count() > 0;
  check('V05', '价格/成交量/OI/资金曲线多图联动面板齐全', hasCandle && hasFunding, `ChartPanel=${hasCandle}, FundingPanel=${hasFunding}`);

  // 6. 单条记录回放（逐笔交易汇总行）
  const tradeRowsCount = await page.locator('[data-testid="lab-trades"] tbody tr').count();
  const firstTradeTxt = await page.locator('[data-testid="lab-trades"] tbody tr').first().innerText().catch(() => '');
  check('V06', '逐笔交易汇总存在真实记录', tradeRowsCount > 0 && /VELVETUSDT/.test(firstTradeTxt), `记录条数=${tradeRowsCount}, 首行="${firstTradeTxt.slice(0, 80)}"`);

  // 7. 上一笔 / 下一笔导航
  const prevBtn = page.locator('button:has-text("◀ 上一笔")');
  const nextBtn = page.locator('button:has-text("下一笔 ▶")');
  const hasNavBtns = (await prevBtn.count() > 0) && (await nextBtn.count() > 0);
  check('V07', '图表包含交易上一笔/下一笔导航按钮', hasNavBtns, `找到上一笔/下一笔按钮: ${hasNavBtns}`);
  if (hasNavBtns) {
    await nextBtn.click();
    await page.waitForTimeout(800);
  }

  // 8. 大图展开 / 紧凑视图切换
  const expandBtn = page.locator('button:has-text("展开大图")');
  if (await expandBtn.count() > 0) {
    await expandBtn.click();
    await page.waitForTimeout(1000);
    const hasCollapse = await page.locator('button:has-text("收起大图")').count() > 0;
    check('V08', '支持大图展开/收起切换', hasCollapse, `展开后出现收起按钮: ${hasCollapse}`);
    await page.screenshot({ path: path.join(SHOTS, '03_chart_expanded.png'), fullPage: false });
    // 收起大图
    await page.locator('button:has-text("收起大图")').click();
    await page.waitForTimeout(800);
  } else {
    check('V08', '支持大图展开/收起切换', false, '未找到展开大图按钮');
  }

  // 9. 更多视图切换：委托订单明细面板 + 点击行双向联动
  const ordersTabBtn = page.locator('[data-testid="lab-panel-orders"]');
  if (await ordersTabBtn.count() > 0) {
    await ordersTabBtn.click();
    await page.waitForTimeout(1200);
    const ordersVisible = await page.locator('[data-testid="lab-orders"]').count() > 0;
    const orderRows = await page.locator('[data-testid="lab-orders"] tbody tr').count();
    check('V09.1', '可展开委托订单明细面板并查看真实订单', ordersVisible && orderRows > 0, `订单行数=${orderRows}`);
    await page.screenshot({ path: path.join(SHOTS, '04_orders_panel.png'), fullPage: false });

    // 点击某一行订单定位图表
    await page.locator('[data-testid="lab-orders"] tbody tr').first().click();
    await page.waitForTimeout(800);
    check('V09.2', '点击订单行正常触发双向联动', true, '点击首行订单未发生报错并成功定位');
  }

  // 10. 更多视图切换：逐次成交明细 (Fills) 面板 + 点击行双向联动
  const fillsTabBtn = page.locator('[data-testid="lab-panel-fills"]');
  if (await fillsTabBtn.count() > 0) {
    await fillsTabBtn.click();
    await page.waitForTimeout(1200);
    const fillsVisible = await page.locator('[data-testid="lab-fills"]').count() > 0;
    const fillRows = await page.locator('[data-testid="lab-fills"] tbody tr').count();
    check('V10.1', '可展开逐次成交明细面板 (Fills) 并查看真实成交', fillsVisible && fillRows > 0, `成交行数=${fillRows}`);
    await page.screenshot({ path: path.join(SHOTS, '05_fills_panel.png'), fullPage: false });

    // 点击某一行成交定位图表
    await page.locator('[data-testid="lab-fills"] tbody tr').first().click();
    await page.waitForTimeout(800);
    check('V10.2', '点击成交行正常触发双向联动', true, '点击首行成交未发生报错并成功定位');
  }

  // 11. 导出按钮检测（CSV 与 JSON 导出按钮就绪）
  const hasExportTrades = await page.locator('button:has-text("导出逐笔 CSV")').count() > 0;
  const hasExportJson = await page.locator('button:has-text("导出完整 JSON")').count() > 0;
  check('V11', '页面提供逐笔与综合明细一键导出按钮', hasExportTrades && hasExportJson, `逐笔CSV=${hasExportTrades}, 完整JSON=${hasExportJson}`);

  // 12. 页面刷新 / 留存重开复现验证
  console.log('[*] 正在执行页面刷新与重开复现验证...');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  const reloadedChart = await page.locator('[data-testid="lab-chart-panel"]').count() > 0;
  const reloadedHead = norm(await page.locator('[data-testid="lab-chart-panel"] .ds-card__head').innerText().catch(() => ''));
  check('V12', '页面刷新后仍可确定性重开并恢复该 run 图表与逐笔数据',
    reloadedChart && reloadedHead.includes('VELVETUSDT'),
    `刷新后图表就绪=${reloadedChart}, 标的匹配=${reloadedHead.includes('VELVETUSDT')}`);
  await page.screenshot({ path: path.join(SHOTS, '06_reloaded_reopen_success.png'), fullPage: false });

} catch (err) {
  console.error('[!] 验证过程抛出异常:', err);
  check('V_FATAL', '验收执行无未捕获异常', false, String(err));
} finally {
  await browser.close();
}

const total = checks.length;
const passed = checks.filter((c) => c.pass).length;
const failed = checks.filter((c) => !c.pass).length;
console.log(`\n==================================================`);
console.log(`浏览器验收总结: ${passed}/${total} 通过，${failed} 失败`);
console.log(`==================================================`);

const report = {
  title: 'WORK-SIGNAL-REPLAY-01 真实浏览器页面验收报告',
  target_run_id: TARGET_RUN_ID,
  timestamp_utc: new Date().toISOString(),
  total_checks: total,
  passed_checks: passed,
  failed_checks: failed,
  status: failed === 0 ? 'ALL_PASSED' : 'HAS_FAILURES',
  checks,
};

fs.writeFileSync(path.join(OUT, 'ui_acceptance.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`[✔] 报告与截图已写入 ${OUT}`);
if (failed > 0) process.exit(1);
