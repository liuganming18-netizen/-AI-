/**
 * UI-LINK-01 · 隔离环境真实工作台端到端操作（playwright）
 *
 * 只做「人点界面」这件事：选信号 → 点开多 → 看挂单 → 看持仓 → 看止损轨迹 → 看暂停态。
 * 所有数据都来自页面自己向后端取的接口，脚本不注入任何夹具数据，也不直接调业务接口改账本
 * （挂单成交由真实引擎函数推进，见 e2e_driver.py 的说明）。
 *
 * 用法：node e2e_ui.js <phase> <截图输出> [结果JSON输出]
 *   phase = select_open | after_fill | paused | resumed | final
 */
const { chromium } = require('playwright');
const fs = require('fs');

const PHASE = process.argv[2] || 'select_open';
const SHOT = process.argv[3] || ('/tmp/e2e_' + PHASE + '.png');
const JSONOUT = process.argv[4] || null;
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:3100';
// 账本首屏就绪的最长等待。实测隔离后端 `/paper/accounts` 单次 23s（见 GAP-06：
// 行情库 candle_versions 无可用索引 ⇒ 每次取标记价都全表扫描），因此不能沿用
// 「等 5 秒就判定没有持仓」的短超时——那会把「慢」误判成「无数据」。
const LEDGER_TIMEOUT = parseInt(process.env.E2E_LEDGER_TIMEOUT || '180000', 10);

/** 等右栏账本真正就绪：data-connected=1，或已出现持仓行 / 账户真实数字。 */
async function waitLedger(page) {
  const t0 = Date.now();
  try {
    await page.waitForFunction(() => {
      const panel = document.querySelector('[data-testid="paper-panel"]');
      if (!panel) return false;
      if (panel.getAttribute('data-connected') === '1') return true;
      if (document.querySelector('[data-testid="paper-position-row"]')) return true;
      return false;
    }, { timeout: LEDGER_TIMEOUT, polling: 500 });
    return { ok: true, ms: Date.now() - t0 };
  } catch (e) {
    return { ok: false, ms: Date.now() - t0, error: e.message };
  }
}

const dump = () => {
  const q = s => Array.from(document.querySelectorAll(s));
  const txt = el => (el ? el.innerText.replace(/\s+/g, ' ').trim() : null);
  const pending = document.querySelector('[data-testid="paper-pending-block"]');
  return {
    gate_button: (() => {
      const b = q('button').find(x => /^(运行中|已暂停)$/.test((x.innerText || '').trim()));
      return b ? { text: b.innerText.trim(), aria_pressed: b.getAttribute('aria-pressed'), title: b.title } : null;
    })(),
    open_long_button: (() => {
      const b = q('button').find(x => (x.innerText || '').includes('按选中信号开多'));
      return b ? { disabled: b.disabled, title: b.title } : null;
    })(),
    notice: txt(document.querySelector('[data-testid="paper-panel"] [class*="bg-amber-500/10"]')),
    selected_signal: (() => {
      const el = q('[data-testid="signal-row"]').find(x => (x.className || '').includes('border-2'));
      return el ? { uid: el.dataset.signalUid, symbol: el.dataset.symbol, origin: el.dataset.origin, state: el.dataset.executableState } : null;
    })(),
    signal_rows: q('[data-testid="signal-row"]').map(el => ({
      uid: el.dataset.signalUid, symbol: el.dataset.symbol,
      origin: el.dataset.origin, state: el.dataset.executableState,
      filled: el.dataset.filled,
      chain: (el.innerText.match(/③ 计划下单[^\n]*/) || [''])[0].slice(0, 80) + ' | ' +
             (el.innerText.match(/④ 实际成交[^\n]*/) || [''])[0].slice(0, 80),
    })),
    pending_block: pending ? { state: pending.dataset.state, n: pending.dataset.nPending, text: txt(pending) } : null,
    pending_rows: q('[data-testid="paper-pending-row"]').map(el => ({
      arm: el.dataset.arm, origin: el.dataset.origin, status: el.dataset.status, direction: el.dataset.direction,
      pending_id: txt(el.querySelector('[data-testid="paper-pending-id"]')),
      direction_text: txt(el.querySelector('[data-testid="paper-pending-direction"]')),
      origin_text: txt(el.querySelector('[data-testid="paper-pending-origin"]')),
      status_text: txt(el.querySelector('[data-testid="paper-pending-status"]')),
      earliest_text: txt(el.querySelector('[data-testid="paper-pending-earliest"]')),
      reason_text: txt(el.querySelector('[data-testid="paper-pending-reason"]')),
      full_text: txt(el),
    })),
    position_rows: q('[data-testid="paper-position-row"]').map(el => ({ text: txt(el).slice(0, 400) })),
    stop_trail: {
      records: q('[data-testid="stop-trail-records"]').length,
      text: txt(document.querySelector('[data-testid="stop-trail-records"]')),
      empty: txt(document.querySelector('[data-testid="stop-trail-empty"]')),
      error: txt(document.querySelector('[data-testid="stop-trail-error"]')),
    },
    account_boxes: q('[data-testid="paper-account-box"]').map(el => ({ text: txt(el).slice(0, 220) })),
  };
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 30000 });
  const ledger = await waitLedger(page);
  await page.waitForTimeout(1200);

  const steps = [{ step: '等待右栏账本就绪', ok: ledger.ok, detail: ledger.ok ? (ledger.ms + ' ms') : ledger.error }];

  if (PHASE === 'select_open') {
    // ① 选中当前信号区里那条真实可执行信号
    //    E2E_SIGNAL_UID 可指定具体一条（信号区里可能有多条，第一条可能已被用过）
    const wantUid = process.env.E2E_SIGNAL_UID || '';
    let row = page.locator('[data-testid="signal-row"]').first();
    if (wantUid) {
      row = page.locator(`[data-testid="signal-row"][data-signal-uid="${wantUid}"]`).first();
    }
    await row.waitFor({ timeout: 20000 });
    await row.click();
    await page.waitForTimeout(1200);
    steps.push({ step: '选中信号', ok: true, detail: await row.getAttribute('data-signal-uid') });

    // ② 点「按选中信号开多」
    const btn = page.locator('button', { hasText: '按选中信号开多' }).first();
    const disabled = await btn.isDisabled();
    steps.push({ step: '开多按钮状态', ok: !disabled, detail: disabled ? '按钮禁用（不会发出请求）' : '可点击' });
    if (!disabled) {
      await btn.click();
      steps.push({ step: '点击开多', ok: true });
      // 等挂单出现（服务端返回后前端会刷新）
      await page.waitForFunction(() => {
        const el = document.querySelector('[data-testid="paper-pending-block"]');
        return el && el.dataset.state === 'has_pending';
      }, { timeout: 45000 }).catch(e => steps.push({ step: '等待挂单', ok: false, detail: e.message }));
      await page.waitForTimeout(1500);
    }
  }

  if (PHASE === 'after_fill') {
    // 展开止损轨迹：点持仓行上的「止损轨迹」按钮（testid 带 positionId）
    // 先等持仓行真正渲染出来（账本慢，见 GAP-06），再点，避免把「还没渲染」当成「无持仓」。
    const trail = page.locator('[data-testid^="btn-stop-trail-"]').first();
    const t0 = Date.now();
    await trail.waitFor({ timeout: LEDGER_TIMEOUT }).catch(() => {});
    const waited = Date.now() - t0;
    if (await trail.count()) {
      const tid = await trail.getAttribute('data-testid');
      await trail.click().catch(() => {});
      // 止损轨迹走的是独立接口 /paper/stops/{posId}，同样受 GAP-06 影响，给足时间
      await page.waitForFunction(() => {
        const rec = document.querySelector('[data-testid="stop-trail-records"]');
        const empty = document.querySelector('[data-testid="stop-trail-empty"]');
        const err = document.querySelector('[data-testid="stop-trail-error"]');
        return rec || empty || err;
      }, { timeout: LEDGER_TIMEOUT, polling: 500 }).catch(() => {});
      await page.waitForTimeout(800);
      steps.push({ step: '展开止损轨迹', ok: true, detail: tid + ' · 等按钮 ' + waited + 'ms' });
    } else {
      steps.push({ step: '展开止损轨迹', ok: false, detail: '等了 ' + waited + 'ms 页面上仍无持仓行（无持仓）' });
    }
  }

  if (PHASE === 'close') {
    // 点持仓行上的「平仓」按钮（真实 HTTP → 服务端 close_position）
    const closeBtn = page.locator('[data-testid="paper-position-row"] button', { hasText: '平仓' }).first();
    await closeBtn.waitFor({ timeout: LEDGER_TIMEOUT }).catch(() => {});
    if (await closeBtn.count()) {
      const dis = await closeBtn.isDisabled();
      steps.push({ step: '平仓按钮', ok: !dis, detail: dis ? '按钮禁用' : '可点击' });
      if (!dis) {
        await closeBtn.click();
        steps.push({ step: '点击平仓', ok: true });
        // 平仓后页面要重新拉一遍账本（同样慢），等到「持仓行为 0」**且**账户摘要
        // 已经写成「0 仓在持」再截图。
        // 只判 `rows.length === 0` 是不够的：刷新中途 rows 会先被清空，
        // 那一刻条件立即成立，截图就会抓到「还在平仓前」的旧帧（实测踩过）。
        const t0 = Date.now();
        await page.waitForFunction(() => {
          const rows = document.querySelectorAll('[data-testid="paper-position-row"]');
          const box = document.querySelector('[data-testid="paper-account-box"]');
          const t = box ? box.innerText : '';
          return rows.length === 0 && /0 仓在持/.test(t);
        }, { timeout: LEDGER_TIMEOUT, polling: 500 }).catch(() => {});
        steps.push({ step: '等待平仓落账', ok: true, detail: (Date.now() - t0) + ' ms' });
        await page.waitForTimeout(800);
      }
    } else {
      steps.push({ step: '平仓按钮', ok: false, detail: '无持仓行' });
    }
  }

  if (PHASE === 'paused' || PHASE === 'resumed') {
    const want = PHASE === 'paused' ? '运行中' : '已暂停';
    const btn = page.locator('button', { hasText: want }).first();
    if (await btn.count()) {
      await btn.click();
      await page.waitForTimeout(2500);
      steps.push({ step: '点总闸（从 ' + want + ' 切走）', ok: true });
    }
    // 暂停态：点一下开多，看前端是否给出提示（按钮禁用时用 title 取证）
    const longBtn = page.locator('button', { hasText: '按选中信号开多' }).first();
    if (await longBtn.count()) {
      const dis = await longBtn.isDisabled();
      if (!dis) {
        await longBtn.click();
        await page.waitForTimeout(2000);
      }
      steps.push({ step: '暂停态下开多按钮', ok: true, detail: dis ? '按钮已禁用' : '按钮可点（已点一次看提示）' });
    }
  }

  await page.waitForTimeout(800);
  const info = await page.evaluate(dump);
  await page.screenshot({ path: SHOT, fullPage: false });

  const out = { phase: PHASE, at: new Date().toISOString(), base: BASE, steps, page_errors: errs, dom: info };
  const text = JSON.stringify(out, null, 1);
  if (JSONOUT) fs.writeFileSync(JSONOUT, text, 'utf8');
  console.log(text);
  await browser.close();
})();
