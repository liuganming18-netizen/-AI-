/**
 * UI-LINK-01 · 画布实测探针
 *
 * 作用：用真实浏览器打开 system-flow.html，等所有卡片脱离「探测中」后，
 *      把每张卡片的状态、证据文本、接口层 HTTP 码、工作台深链导出为 JSON，
 *      并落一张整页截图。状态不是脚本判的，是页面自己按业务字段判的 ——
 *      脚本只负责如实抄下来，避免「人工抄写」再次出错。
 *
 * 用法：
 *   node canvas_probe.js <页面URL> <截图输出> [后端覆盖URL] [JSON输出]
 *
 *   - 不传后端覆盖：页面走自己同源代理（隔离环境 = vite 3100 → 后端 8799）。
 *   - 传后端覆盖：把页面里所有 /api/** 请求改投到指定后端（生产只读复验用）。
 *     用 route.fetch + route.fulfill 是为了绕开跨域：对页面而言仍是同源响应，
 *     但真实数据来自另一台后端。只读，不发任何写请求。
 */
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const pageUrl = process.argv[2] || 'http://127.0.0.1:3100/system-flow.html';
  const shot = process.argv[3] || '/tmp/canvas.png';
  const backend = process.argv[4] || null;
  const jsonOut = process.argv[5] || null;

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1500 }, deviceScaleFactor: 1 });

  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

  const rewritten = [];
  if (backend) {
    await page.route('**/api/**', async route => {
      const target = route.request().url().replace(/^https?:\/\/[^/]+/, backend);
      rewritten.push(route.request().url() + ' -> ' + target);
      const resp = await route.fetch({ url: target });
      await route.fulfill({ response: resp });
    });
  }

  await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => {
    const pills = Array.from(document.querySelectorAll('[data-pill]'));
    return pills.length > 0 && pills.every(p => p.textContent !== '探测中');
  }, { timeout: 90000 }).catch(e => errs.push('wait: ' + e.message));
  await page.waitForTimeout(900);

  const info = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.node')).map(el => {
      const link = el.querySelector('[data-link]');
      return {
        key: el.dataset.key,
        state: el.dataset.state,
        pill: (el.querySelector('[data-pill]') || {}).textContent || '',
        endpoint: (el.querySelector('.path') || {}).textContent || '',
        evidence: (el.querySelector('[data-ev]') || {}).textContent || '',
        warn: (el.querySelector('[data-warn]') || {}).textContent || '',
        http: (el.querySelector('[data-code]') || {}).textContent || '',
        link: link ? link.getAttribute('href') : null,
      };
    });
    return {
      title: document.title,
      counters: document.getElementById('counters').innerText.replace(/\n/g, ' | '),
      live: document.getElementById('live-text').textContent,
      cards,
    };
  });

  await page.screenshot({ path: shot, fullPage: true });

  const out = {
    generated_at: new Date().toISOString(),
    page_url: pageUrl,
    backend_override: backend,
    backend_requests_rewritten: rewritten.length,
    page_errors: errs,
    counters: info.counters,
    live_snapshot: info.live,
    cards: info.cards,
  };
  const text = JSON.stringify(out, null, 1);
  if (jsonOut) fs.writeFileSync(jsonOut, text, 'utf8');
  console.log(text);
  await browser.close();
})();
