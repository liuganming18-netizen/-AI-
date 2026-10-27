/**
 * UI-LINK-01 · 账本慢路径探针（只读）
 *
 * 目的：把「右栏账本长时间空白」从现象变成可复算的数字。
 *   ① 记录浏览器侧每个 /api/v1/paper/** 请求的发出时刻、响应时刻、耗时、状态、字节；
 *   ② 记录持仓行 / 账户格出现的最早时刻（或超时）；
 *   ③ 记录页面在此期间的全部 console 错误与 requestfailed。
 *
 * 用法：
 *   NODE_PATH=UI界面/web/node_modules node ledger_latency_probe.js [base] [waitMs] [outJson]
 *   默认 base=http://127.0.0.1:3100，waitMs=120000
 */
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = process.argv[2] || 'http://127.0.0.1:3100';
const WAIT = parseInt(process.argv[3] || '120000', 10);
const OUT = process.argv[4] || '';

(async () => {
  const t0 = Date.now();
  const stamp = () => Date.now() - t0;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });

  const reqs = [];
  const byReq = new Map();
  page.on('request', (r) => {
    const u = r.url();
    if (!u.includes('/api/v1/paper/')) return;
    const rec = { url: u.replace(BASE, ''), at: stamp(), done: null, status: null, bytes: null };
    reqs.push(rec);
    byReq.set(r, rec);
  });
  page.on('response', async (resp) => {
    const rec = byReq.get(resp.request());
    if (!rec) return;
    rec.done = stamp();
    rec.status = resp.status();
    rec.ms = rec.done - rec.at;
    try {
      const b = await resp.body();
      rec.bytes = b.length;
    } catch (e) { rec.bytes = -1; }
  });
  const failed = [];
  page.on('requestfailed', (r) => {
    if (!r.url().includes('/api/v1/paper/')) return;
    failed.push({ url: r.url().replace(BASE, ''), at: stamp(), error: r.failure() && r.failure().errorText });
  });
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push({ at: stamp(), text: m.text().slice(0, 300) }); });

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });

  // 等「账户格出现真实数字」或「持仓行出现」，两者任一即算右栏就绪
  let readyAt = null;
  let readyWhy = null;
  const deadline = Date.now() + WAIT;
  while (Date.now() < deadline) {
    const st = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="paper-panel"]');
      const rows = document.querySelectorAll('[data-testid="paper-position-row"]');
      const box = document.querySelector('[data-testid="paper-account-box"], .paper-account-box');
      const txt = box ? box.innerText : '';
      const hasNum = /[0-9]/.test(txt) && !/—/.test(txt.replace(/[0-9]/g, ''));
      return {
        connected: panel ? panel.getAttribute('data-connected') : null,
        arms: panel ? panel.getAttribute('data-arms') : null,
        pos: rows.length,
        boxText: txt.slice(0, 400),
        hasNum,
      };
    }).catch(() => null);
    if (st && (st.pos > 0 || st.hasNum || st.connected === '1')) {
      readyAt = stamp();
      readyWhy = st.pos > 0 ? 'position_row' : (st.hasNum ? 'account_numbers' : 'connected_attr');
      break;
    }
    await page.waitForTimeout(500);
  }

  const finalDom = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="paper-panel"]');
    const rows = [...document.querySelectorAll('[data-testid="paper-position-row"]')].map((r) => r.innerText.slice(0, 400));
    const box = document.querySelector('[data-testid="paper-account-box"], .paper-account-box');
    return {
      connected: panel ? panel.getAttribute('data-connected') : null,
      arms: panel ? panel.getAttribute('data-arms') : null,
      position_rows: rows,
      account_text: box ? box.innerText.slice(0, 600) : null,
    };
  }).catch(() => null);

  const shot = OUT ? OUT.replace(/\.json$/, '.png') : '';
  if (shot) await page.screenshot({ path: shot, fullPage: false });

  const result = {
    probe: 'ledger_latency_probe',
    base: BASE,
    at: new Date().toISOString(),
    wait_ms: WAIT,
    ready: readyAt !== null,
    ready_at_ms: readyAt,
    ready_why: readyWhy,
    paper_requests: reqs,
    request_failed: failed,
    console_errors: consoleErrors,
    final_dom: finalDom,
    screenshot: shot || null,
  };
  const text = JSON.stringify(result, null, 1);
  if (OUT) fs.writeFileSync(OUT, text);
  console.log(text);
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
