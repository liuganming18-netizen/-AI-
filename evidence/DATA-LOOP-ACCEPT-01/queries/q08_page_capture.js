// q08｜页面采证：打开本机前端，截图并抓取可见文本（只读，不改任何业务状态）。
// 目的：证明「上游正常、下游断开」在页面上实际显示成什么（是否被标注为健康/已连接）。
const path = require('path');
const fs = require('fs');

const OUT = path.resolve(__dirname, '..', 'screenshots');
const EXE = '/Users/boos/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell';

(async () => {
  const { chromium } = require('playwright');
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: EXE, headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  const resp = [];
  // 前端走 vite 代理（3000/api/... → 8787），因此按 /api/ 匹配，而不是直连 8787
  page.on('response', (r) => { const u = r.url(); if (u.includes('/api/')) resp.push(`${r.status()} ${u}`); });

  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(9000);

  const text = await page.evaluate(() => document.body.innerText);
  fs.writeFileSync(path.join(OUT, 'page_text_home.txt'), text, 'utf8');
  await page.screenshot({ path: path.join(OUT, '01_home.png'), fullPage: false });

  // 侧栏里与交易台/模拟盘相关的入口逐个点开采证
  const navs = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a,button,[role="button"]'))
      .map((e) => (e.innerText || '').trim())
      .filter((t) => t && t.length <= 12)
  );
  fs.writeFileSync(path.join(OUT, 'nav_labels.json'), JSON.stringify([...new Set(navs)], null, 2), 'utf8');

  const wanted = ['交易台', '模拟盘', '数据中心', '策略', '回测'];
  let idx = 1;
  for (const label of wanted) {
    try {
      const loc = page.locator(`text=${label}`).first();
      if (await loc.count() === 0) continue;
      await loc.click({ timeout: 5000 });
      await page.waitForTimeout(6000);
      idx += 1;
      const t = await page.evaluate(() => document.body.innerText);
      fs.writeFileSync(path.join(OUT, `page_text_${label}.txt`), t, 'utf8');
      await page.screenshot({ path: path.join(OUT, `0${idx}_${label}.png`), fullPage: false });
    } catch (e) {
      fs.appendFileSync(path.join(OUT, 'nav_errors.txt'), `${label}: ${e.message}\n`);
    }
  }

  fs.writeFileSync(path.join(OUT, 'api_calls.txt'), resp.join('\n'), 'utf8');
  fs.writeFileSync(path.join(OUT, 'console_errors.txt'), consoleErrors.join('\n'), 'utf8');
  await browser.close();
  console.log('done; api calls:', resp.length, 'console errors:', consoleErrors.length);
})();
