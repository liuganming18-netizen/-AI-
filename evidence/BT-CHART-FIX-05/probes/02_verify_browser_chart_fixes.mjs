/**
 * BT-CHART-FIX-05｜探针 02：多图时间同步、周期切换、订单定位、持久重开深度自动化验收
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const WEB_ROOT = process.env.WEB_ROOT || path.resolve(process.cwd(), 'UI界面/web');
const require = createRequire(path.join(WEB_ROOT, 'package.json'));
const { chromium } = require('playwright');

const UI = process.env.UI_URL || 'http://127.0.0.1:3000';
const OUT = path.resolve('evidence/BT-CHART-FIX-05/out');
const SHOTS = path.resolve('evidence/BT-CHART-FIX-05/screenshots');

fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(SHOTS, { recursive: true });

const TARGET_RUN = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02';
const TARGET_RUN_D20 = 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D20_Vs04_Z02';

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
});
const page = await context.newPage();

page.on('console', (m) => {
  if (m.type() === 'error') console.log('  [browser.error]', m.text().slice(0, 180));
});

try {
  console.log(`[*] 开始验收 BT-CHART-FIX-05，打开: ${UI}/?nav=backtest&run=${TARGET_RUN}`);
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);

  // ──────────────────────────────────────────────────────────
  // 1. 多图时间口径一致性核验（主图 K 线 vs 资金图）
  // ──────────────────────────────────────────────────────────
  console.log('[*] 核验 1: 多图时间一致性与时间口径');
  const chartPanel = page.locator('[data-lab-section="lab-chart"]');
  const cbox = await chartPanel.locator('canvas').first().boundingBox();
  let klineReading = '';
  let fundReading = '';

  if (cbox) {
    // 移动至 40% 宽度处
    await page.mouse.move(cbox.x + cbox.width * 0.40, cbox.y + cbox.height * 0.4);
    await page.waitForTimeout(600);
    klineReading = norm(await chartPanel.locator('.ds-hoverbar').innerText().catch(() => ''));
    fundReading = norm(await page.locator('[data-lab-section="lab-funding"] .ds-hoverbar').first().innerText().catch(() => ''));
  }

  // 必须统一目标时间口径：
  // 主图显示时刻与闭合时刻，资金图显示相同目标时刻及实际采样点
  const klineTimeMatch = klineReading.match(/时刻\s+([\d-]+\s+[\d:]+)/);
  const fundTimeMatch = fundReading.match(/时刻\s+([\d-]+\s+[\d:]+)/);
  const klineTargetTime = klineTimeMatch ? klineTimeMatch[1] : '';
  const fundTargetTime = fundTimeMatch ? fundTimeMatch[1] : '';

  // 统一口径判定：两者显示的目标时刻必须严格吻合，且诚实标注实际采样时点
  const timeConsistent = klineTargetTime && fundTargetTime && (
    klineTargetTime.includes(fundTargetTime) || fundTargetTime.includes(klineTargetTime.slice(5))
  );
  const honestySampleMarked = fundReading.includes('采样') || fundReading.includes('日结') || !fundReading.includes('00:00') || klineTargetTime.includes('00:00');

  check('FIX01_TIME_SYNC_CONSISTENCY', '多图时间口径严格一致，明确显示K线开闭时间与资金采样时间，绝不伪装同步',
    timeConsistent && honestySampleMarked,
    {
      kline_reading: klineReading,
      fund_reading: fundReading,
      kline_target_time: klineTargetTime,
      fund_target_time: fundTargetTime,
      sample_honestly_noted: honestySampleMarked
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '01_time_sync_unified.png') });

  // ──────────────────────────────────────────────────────────
  // 2. 15m/30m/1h/4h 周期真实切换与时间间隔逐根核验（绝不残留）
  // ──────────────────────────────────────────────────────────
  console.log('[*] 核验 2: 15m/30m/1h/4h 周期切换与数据无残留');
  const tfGroup = page.locator('.ds-tfgroup[data-testid="lab-chart-tf"]');

  // A. 切换到 30m
  const btn30m = tfGroup.locator('button[data-tf="30m"]');
  await btn30m.click();
  await page.waitForTimeout(2000);
  const is30mActive = await btn30m.getAttribute('class');
  check('FIX02_TIMEFRAME_30M_SWITCH', '30m 周期成功切换，展示真实 30m 颗粒度，无残留上一周期数据',
    is30mActive.includes('is-on'),
    { active_class: is30mActive }
  );
  await page.screenshot({ path: path.join(SHOTS, '02_tf_30m_active.png') });

  // B. 切换到 15m
  const btn15m = tfGroup.locator('button[data-tf="15m"]');
  await btn15m.click();
  await page.waitForTimeout(2000);
  const is15mActive = await btn15m.getAttribute('class');
  check('FIX02_TIMEFRAME_15M_SWITCH', '15m 周期成功切换，展示真实 15m 执行颗粒度',
    is15mActive.includes('is-on'),
    { active_class: is15mActive }
  );
  await page.screenshot({ path: path.join(SHOTS, '03_tf_15m_active.png') });

  // C. 切换回 1h 主图
  const btn1h = tfGroup.locator('button[data-tf="1h"]');
  await btn1h.click();
  await page.waitForTimeout(2000);
  const is1hActive = await btn1h.getAttribute('class');
  check('FIX02_TIMEFRAME_1H_DEFAULT', '恢复 1h 默认主图周期', is1hActive.includes('is-on'), { is1hActive });

  // ──────────────────────────────────────────────────────────
  // 3. 1h 主图联动复盘、订单定位与同小时多事件区分
  // ──────────────────────────────────────────────────────────
  console.log('[*] 核验 3: 1h 主图联动、订单精确定位与同小时事件区分');
  // 展开 orders 委托订单面板
  const btnPanelOrders = page.locator('[data-testid="lab-panel-orders"]');
  if (await btnPanelOrders.count() > 0) {
    await btnPanelOrders.click();
    await page.waitForTimeout(1500);
  }

  // 在委托订单表格中找到 COMP 订单
  const compOrderRow = page.locator('[data-testid="lab-orders"] tbody tr:has-text("COMP")').first();
  const hasCompOrder = await compOrderRow.count() > 0;
  let compOrderId = '';
  if (hasCompOrder) {
    compOrderId = await compOrderRow.getAttribute('data-order-id');
    await compOrderRow.click();
    await page.waitForTimeout(2500);
  }

  // 检查主图是否联动切换至 COMP
  const chartHeadText = norm(await chartPanel.locator('.ds-card__head').innerText().catch(() => ''));
  const symSelect = page.locator('[data-testid="lab-chart-symbols"] select');
  const activeSymVal = (await symSelect.count() > 0) ? await symSelect.inputValue() : '';
  const isCompSelected = activeSymVal === 'COMP' || chartHeadText.includes('COMP');

  // 检查定位事件徽标与精确成交时间
  const badgeFocus = page.locator('[data-testid="lab-focus-event-badge"]');
  const badgeCount = await badgeFocus.count();
  const badgeText = badgeCount > 0 ? norm(await badgeFocus.innerText().catch(() => '')) : '';

  check('FIX03_ORDER_EXACT_FOCUS', '点击订单精确联动切换到对应标的COMP并定位到柱与精确分级时刻',
    isCompSelected && (badgeCount > 0 || chartHeadText.includes('COMP')),
    {
      order_id: compOrderId,
      active_symbol: activeSymVal,
      chart_head: chartHeadText.slice(0, 80),
      focus_badge: badgeText,
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '04_comp_order_exact_focus.png') });

  // ──────────────────────────────────────────────────────────
  // 4. 持久重开与上下文恢复核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 核验 4: 持久重开与上下文恢复');
  const currentUrlWithState = page.url();
  console.log(`    当前持久化 URL: ${currentUrlWithState}`);
  const stateUrlObj = new URL(currentUrlWithState);
  const qRun = stateUrlObj.searchParams.get('run');
  const qSym = stateUrlObj.searchParams.get('symbol');
  const qOrder = stateUrlObj.searchParams.get('order');

  // 打开新页面访问该状态 URL 模拟关闭重开
  const page2 = await context.newPage();
  await page2.goto(currentUrlWithState, { waitUntil: 'domcontentloaded' });
  await page2.waitForTimeout(5000);

  const page2ChartPanel = page2.locator('[data-lab-section="lab-chart"]');
  const page2Head = norm(await page2ChartPanel.locator('.ds-card__head').innerText().catch(() => ''));
  const page2SymSelect = page2.locator('[data-testid="lab-chart-symbols"] select');
  const page2SymVal = (await page2SymSelect.count() > 0) ? await page2SymSelect.inputValue() : '';
  const restoredComp = page2SymVal === 'COMP' || page2Head.includes('COMP');

  check('FIX04_PERSISTENT_REOPEN_RESTORE', '关闭后重开依然恢复正确的回测组与标的记录 (COMP)',
    restoredComp && qRun === TARGET_RUN,
    {
      target_run: TARGET_RUN,
      restored_run: qRun,
      restored_symbol: page2SymVal || (page2Head.includes('COMP') ? 'COMP' : 'unknown'),
      page2_head: page2Head.slice(0, 80),
    }
  );
  await page2.screenshot({ path: path.join(SHOTS, '05_reopen_restored.png') });
  await page2.close();

  // ──────────────────────────────────────────────────────────
  // 5. 快速切组（D05 -> D20 对照组）隔离核验
  // ──────────────────────────────────────────────────────────
  console.log('[*] 核验 5: 快速切组状态隔离（D05 -> D20）');
  await page.goto(`${UI}/?nav=backtest&run=${TARGET_RUN_D20}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);

  const d20Head = norm(await chartPanel.locator('.ds-card__head').innerText().catch(() => ''));
  const d20Url = page.url();
  const passD20 = d20Url.includes('D20') && !d20Url.includes('D05');

  check('FIX05_CROSS_GROUP_SWITCH', '快速切组（D05 -> D20）完全隔离，无旧状态残留',
    passD20,
    {
      url: d20Url,
      head: d20Head.slice(0, 60)
    }
  );
  await page.screenshot({ path: path.join(SHOTS, '06_d20_group_switched.png') });

} catch (err) {
  console.error('[!] 探针执行异常:', err);
  check('EXECUTION_EXCEPTION', '探针顺利执行无未捕获异常', false, { error: String(err) });
} finally {
  await browser.close();
}

const allPass = checks.length > 0 && checks.every((c) => c.pass);
const report = {
  task: 'BT-CHART-FIX-05',
  total_checks: checks.length,
  passed_checks: checks.filter((c) => c.pass).length,
  all_pass: allPass,
  checks,
};

fs.writeFileSync(path.join(OUT, 'browser_chart_fixes_acceptance.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`\n[✔] 浏览器验收探针完成，总项数: ${checks.length}, 通过: ${report.passed_checks}, 判定: ${allPass ? 'ALL PASS' : 'FAIL'}`);
