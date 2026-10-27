// DATA-CANVAS-01 截图取证：一次运行 = 一个 url + 一张图 + 一份 DOM 摘要 + 一份接口日志切片
// 用法：node shot_canvas.mjs <url> <outPng> <outJson> [waitMs] [faultAfter]
import pw from '/Users/boos/Desktop/威科夫因子共振趋势/UI界面/web/node_modules/playwright/index.js';
const { chromium } = pw;
import fs from 'fs';

(async () => {
  const url = process.argv[2];
  const outPng = process.argv[3];
  const outJson = process.argv[4];
  const waitMs = Number(process.argv[5] || 9000);
  const faultAfter = process.argv[6] || '';   // 形如 "/api/v1/paper/status|500"，第二次刷新前注入

  const browser = await chromium.launch({ args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1200 }, deviceScaleFactor: 1 });
  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  const requests = [];
  page.on('response', async r => {
    const u = r.url();
    if (u.includes('/api/v1/') || u.includes('/__fault')) {
      let len = null;
      try { len = (await r.body()).length; } catch (e) { len = null; }
      requests.push({ url: u, status: r.status(), bytes: len });
    }
  });

  const t0 = new Date();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 等画布首轮判定完成：verdict 标题不再是“正在读取”
  await page.waitForFunction(() => {
    const t = document.getElementById('verdict-title');
    return t && !/正在读取/.test(t.textContent);
  }, { timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(waitMs);

  let afterFault = null;
  if (faultAfter) {
    const [p, mode] = faultAfter.split('|');
    await page.evaluate(async (args) => {
      await fetch('/__fault?path=' + encodeURIComponent(args.p) + '&mode=' + args.m, { cache: 'no-store' });
    }, { p, m: mode || '500' });
    await page.waitForTimeout(1500);
    await page.click('#btn-refresh').catch(() => {});
    await page.waitForTimeout(8000);
    afterFault = { path: p, mode: mode || '500' };
  }

  // 抓取画布判定结果（DOM 事实，不依赖截图 OCR）
  // 注意：这里必须单独记时刻 —— 接口失败态是先注入故障再刷新再抓，
  // 与首次加载（t0）相差 10~20 秒，用 t0 当“截图时刻”会错标。
  const domAt = new Date();
  const dom = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.chain-node')].map(n => ({
      key: n.dataset.node,
      state: n.dataset.state,
      pill: (n.querySelector('[data-pill]') || {}).textContent || '',
      headline: (n.querySelector('[data-headline]') || {}).innerText || '',
      facts: [...n.querySelectorAll('.fact')].map(f => ({
        k: (f.querySelector('.k') || {}).textContent,
        v: (f.querySelector('.v') || {}).innerText
      })),
      blocker: (n.querySelector('[data-blocker]') || {}).innerText || '',
      checks: [...n.querySelectorAll('[data-tech] .chk')].map(c => ({
        ok: c.dataset.ok, text: c.innerText
      }))
    }));
    const chips = [...document.querySelectorAll('.flow .chip')].map(c => ({
      key: c.dataset.chip, state: c.dataset.state,
      text: c.innerText.replace(/\n/g, ' '), isBreak: c.dataset.break === '1'
    }));
    const pools = [...document.querySelectorAll('#pools .pool')].map(p => p.innerText.replace(/\n/g, ' | '));
    return {
      title: document.title,
      verdict: {
        state: (document.getElementById('verdict') || {}).dataset ? document.getElementById('verdict').dataset.state : null,
        title: (document.getElementById('verdict-title') || {}).textContent,
        sub: (document.getElementById('verdict-sub') || {}).textContent,
        meta: (document.getElementById('verdict-meta') || {}).innerText
      },
      build: (document.getElementById('build-id') || {}).textContent,
      chips, pools, nodes,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      docHeight: document.documentElement.scrollHeight
    };
  });

  await page.screenshot({ path: outPng, fullPage: true });
  const shotAt = new Date();
  fs.writeFileSync(outJson, JSON.stringify({
    page_load_at_local: t0.toString(),
    page_load_at_utc: t0.toISOString(),
    dom_captured_at_utc: domAt.toISOString(),
    screenshot_at_utc: shotAt.toISOString(),
    captured_at_local: t0.toString(),
    captured_at_utc: t0.toISOString(),
    url, viewport: { width: 1680, height: 1200 },
    wait_ms: waitMs, fault_after: afterFault,
    console_errors: consoleErrors,
    api_responses: requests,
    dom
  }, null, 1), 'utf-8');

  await browser.close();
  console.log('OK ' + outPng);
})().catch(e => { console.error('FAIL', e); process.exit(1); });
