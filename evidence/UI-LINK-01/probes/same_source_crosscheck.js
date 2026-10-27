/**
 * UI-LINK-01 · 「工作台与画布读同一组真实接口」的数值对账
 *
 * 工单任务 1 的要求不是「两个页面都能打开」，而是**同一组真实接口**。
 * 所以这里不看接口文档，直接看两个页面**各自渲染出来的数字**是否相等：
 *
 *   工作台（/）             画布（/system-flow.html）
 *   代币主表条数            实时快照「行情池 币安 N + 欧易补 M」
 *   信号区条数              「当前信号 N 条」
 *   模拟盘权益              账本四臂 equity
 *   待成交挂单笔数          「挂单 … 四臂 pending 合计 N」
 *
 * 用法：node same_source_crosscheck.js [JSON输出]
 */
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:3100';
const JSONOUT = process.argv[2] || null;

const nums = (s) => (String(s || '').match(/-?\d[\d,]*/g) || []).map(x => Number(x.replace(/,/g, '')));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));

  // ── 工作台 ────────────────────────────────────────────────
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('[data-testid="signal-panel"]', { timeout: 30000 });
  // 右栏账本首屏实测 20–25 秒（GAP-06：行情库缺索引 ⇒ 取标记价全表扫描）。
  // 这里必须等账本真的就绪再抄数字，否则「还没渲染完」会被抄成 0，
  // 制造一个假的「工作台 0 vs 画布 1」不一致。
  await page.waitForFunction(() => {
    const panel = document.querySelector('[data-testid="paper-panel"]');
    if (!panel) return false;
    if (panel.getAttribute('data-connected') === '1') return true;
    return !!document.querySelector('[data-testid="paper-position-row"]');
  }, { timeout: 180000, polling: 500 }).catch(e => errs.push('ledger-wait: ' + e.message));
  await page.waitForTimeout(1500);
  const wb = await page.evaluate(() => {
    const t = el => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
    const body = document.body.innerText;
    const mTokens = body.match(/共\s*([\d,]+)\s*个标的/);
    const mEquity = body.match(/模拟盘权益\s*([\d,]+\.\d+)/);
    return {
      token_count_text: mTokens ? mTokens[1] : null,
      equity_text: mEquity ? mEquity[1] : null,
      signal_rows: document.querySelectorAll('[data-testid="signal-row"]').length,
      pending_block: (() => {
        const el = document.querySelector('[data-testid="paper-pending-block"]');
        return el ? { state: el.dataset.state, n: el.dataset.nPending } : null;
      })(),
      position_rows: document.querySelectorAll('[data-testid="paper-position-row"]').length,
      raw_signals_label: (() => {
        const m = body.match(/(\d+)\s*笔/);
        return m ? m[1] : null;
      })(),
    };
  });

  // ── 画布 ──────────────────────────────────────────────────
  await page.goto(BASE + '/system-flow.html', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => {
    const pills = Array.from(document.querySelectorAll('[data-pill]'));
    return pills.length > 0 && pills.every(p => p.textContent !== '探测中');
  }, { timeout: 180000 }).catch(e => errs.push('wait: ' + e.message));
  await page.waitForTimeout(800);
  const cv = await page.evaluate(() => {
    const live = document.getElementById('live-text').textContent;
    const card = (k) => {
      const el = document.querySelector(`[data-key="${k}"]`);
      return el ? { state: el.dataset.state, ev: (el.querySelector('[data-ev]') || {}).textContent || '' } : null;
    };
    return {
      live,
      pool_line: (live.match(/行情池 币安 ([\d,]+) \+ 欧易补 ([\d,]+) = ([\d,]+)/) || []).slice(1),
      signal_line: (live.match(/当前信号：([\d,]+) 条/) || [])[1] || null,
      pending_line: (live.match(/四臂 pending 合计 ([\d,]+)/) || [])[1] || null,
      positions_line: (live.match(/\/paper\/positions ([\d,]+) 条/) || [])[1] || null,
      cards: { wb_table: card('wb_table'), sig_stream: card('sig_stream'), pending: card('pending'), positions: card('positions') },
    };
  });

  const pool = cv.pool_line.map(Number);
  const checks = [
    {
      item: '监控池（币安 + 欧易补）',
      workbench: wb.token_count_text,
      canvas: pool.length ? String(pool[0] + pool[1]) : null,
      note: '工作台「共 N 个标的」 vs 画布「币安 N + 欧易补 M」',
    },
    {
      item: '当前信号条数',
      workbench: String(wb.signal_rows),
      canvas: cv.signal_line,
      note: '工作台信号区行数 vs 画布「当前信号 N 条」',
    },
    {
      item: '待成交挂单笔数',
      workbench: wb.pending_block ? String(wb.pending_block.n) : null,
      canvas: cv.pending_line,
      note: '工作台待成交区块 data-n-pending vs 画布「四臂 pending 合计」',
    },
    {
      item: '持仓条数',
      workbench: String(wb.position_rows),
      canvas: cv.positions_line,
      note: '工作台持仓行数 vs 画布「/paper/positions N 条」',
    },
  ];
  checks.forEach(c => { c.match = (c.workbench === c.canvas); });

  const out = {
    at: new Date().toISOString(),
    base: BASE,
    page_errors: errs,
    workbench: wb,
    canvas: cv,
    checks,
    all_match: checks.every(c => c.match),
  };
  const text = JSON.stringify(out, null, 1);
  if (JSONOUT) fs.writeFileSync(JSONOUT, text, 'utf8');
  console.log(text);
  await browser.close();
})();
