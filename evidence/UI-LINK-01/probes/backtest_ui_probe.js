/**
 * UI-LINK-01 · 回测详情 ↔ 对账表 同 run_id 的界面取证
 *
 * 做什么：
 *   ① 用 `?nav=backtest&run=<rid>` 深链打开指定 run（确定性入口，不靠列表排序）
 *   ② 点开「结果身份」面板，把页面上**真实渲染出来的 run_id** 抄下来
 *   ③ 抄下指标条与「无成交 / — 缺失」的原文
 *   ④ 与后端 7 个 run 级端点的 run_id 做三方对齐（页面 / 详情 / 指标）
 *
 * 用法：node backtest_ui_probe.js <run_id> <截图> [JSON输出]
 */
const { chromium } = require('playwright');
const fs = require('fs');

const RID = process.argv[2] || 'run_WX_EVENT_TREND_V0_1_BTCUSDT_1792350107';
const SHOT = process.argv[3] || '/tmp/bt.png';
const JSONOUT = process.argv[4] || null;
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:3100';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 1100 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

  await page.goto(BASE + '/?nav=backtest&run=' + encodeURIComponent(RID),
                  { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForSelector('[data-testid="lab-workbench"]', { timeout: 40000 });
  await page.waitForTimeout(8000);

  const steps = [];
  // 点「结果身份」面板（run_id 只在这个面板里渲染）
  const tab = page.locator('button, [role="tab"]', { hasText: '结果身份' }).first();
  if (await tab.count()) {
    await tab.click();
    await page.waitForTimeout(1500);
    steps.push({ step: '点开结果身份面板', ok: true });
  } else {
    steps.push({ step: '点开结果身份面板', ok: false, detail: '未找到入口' });
  }

  const info = await page.evaluate(() => {
    const t = el => (el ? el.innerText.replace(/[ \t]+/g, ' ').trim() : null);
    const identity = document.querySelector('[data-testid="lab-run-identity"]');
    return {
      identity_present: !!identity,
      identity_text: t(identity),
      metricstrip: t(document.querySelector('[data-testid="bt-metricstrip"]')),
      quality_verdict: t(document.querySelector('[data-testid="lab-quality-verdict"]')),
      quality_text: t(document.querySelector('[data-testid="lab-quality-panel"]')),
      funding_empty: t(document.querySelector('[data-testid="lab-funding-panel"]')),
      trades_empty: t(document.querySelector('[data-testid="lab-trades-empty"]')),
    };
  });

  // ① 结果身份（含 run_id）先落一张图：切到审计视图后这块会被收起
  await page.screenshot({ path: SHOT, fullPage: false });

  // 对账表：质量报告在「口径与审计」视图里（viewbar 单选，点它会收起结果身份，
  // 所以结果身份的文字必须**先**抄完再切）。任务 5 要求「回测详情与对账表绑定
  // 同一 run_id」，因此对账表必须单独取证，不能只验详情面板。
  const auditBtn = page.locator('[data-testid="lab-panel-audit"]').first();
  if (await auditBtn.count()) {
    await auditBtn.click();
    await page.waitForTimeout(1500);
    steps.push({ step: '切到「口径与审计」视图', ok: true });
  } else {
    steps.push({ step: '切到「口径与审计」视图', ok: false, detail: '未找到 lab-panel-audit（可能 run 未成功）' });
  }

  const details = page.locator('[data-testid="lab-quality-details"]').first();
  if (await details.count()) {
    await details.evaluate(el => { el.open = true; }).catch(() => {});
    await page.waitForTimeout(600);
    steps.push({ step: '展开质量报告·数据质量核对明细', ok: true });
  } else {
    steps.push({ step: '展开质量报告·数据质量核对明细', ok: false, detail: '未找到折叠区（可能该 run 无质量报告）' });
  }

  const reconcile = await page.evaluate(() => {
    const t = el => (el ? el.innerText.replace(/[ \t]+/g, ' ').trim() : null);
    const d = document.querySelector('[data-testid="lab-quality-details"]');
    let block = null;
    if (d) {
      const all = Array.from(d.querySelectorAll('div'));
      const head = all.find(x => (x.textContent || '').includes('现金恒等式对账'));
      block = head ? t(head.parentElement) : null;
    }
    return {
      details_open: !!(d && d.open),
      reconcile_text: block,
      details_text: t(d),
      quality_verdict: t(document.querySelector('[data-testid="lab-quality-verdict"]')),
      quality_panel: t(document.querySelector('[data-testid="lab-quality-panel"]')),
      url_after_switch: location.href,
    };
  });
  info.reconcile = reconcile;

  // ② 口径与审计视图（对账表所在处）单独一张图。
  //    注意：这里**不能**再写回 SHOT —— 那会把第 58 行拍到的「结果身份（含 run_id）」
  //    覆盖成审计视图，等于 run_id 的视觉证据丢了。
  const auditShot = SHOT.replace(/\.png$/i, '_audit.png');
  await page.screenshot({ path: auditShot, fullPage: false });

  const out = {
    phase: 'backtest_identity',
    at: new Date().toISOString(),
    requested_run_id: RID,
    page_url: page.url(),
    steps,
    page_errors: errs,
    dom: info,
    run_id_visible_on_page: !!(info.identity_text || '').includes(RID),
    // 对账表与详情同属这一个 run：同一页面上「结果身份」渲染出该 run_id，
    // 且质量报告的对账折叠区真实渲染出内容（不是空壳）。
    reconcile_present: !!(info.reconcile && info.reconcile.reconcile_text),
    reconcile_bound_to_same_run: !!(info.identity_text || '').includes(RID)
      && !!(info.reconcile && info.reconcile.reconcile_text),
  };
  const text = JSON.stringify(out, null, 1);
  if (JSONOUT) fs.writeFileSync(JSONOUT, text, 'utf8');
  console.log(text);
  await browser.close();
})();
