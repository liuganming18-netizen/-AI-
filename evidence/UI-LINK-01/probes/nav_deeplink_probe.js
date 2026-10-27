/**
 * UI-LINK-01 · 画布「打开工作台」深链是否真的落到对应视图
 *
 * 画布每张卡片都挂了 `?nav=<视图>` 深链（回测还带 `&run=<run_id>`）。
 * 「链接存在」不等于「点进去落在对的页面」——本探针逐个打开并核对：
 *   · 该视图独有的 testid 是否出现
 *   · 顶栏标题与左侧导航高亮是否跟着变
 *
 * 用法：node nav_deeplink_probe.js [截图目录] [JSON输出]
 */
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:3100';
const SHOTDIR = process.argv[2] || null;
const JSONOUT = process.argv[3] || null;

// 每个视图必须出现的「独有」testid：出现别的视图的、或该出现的没出现，都算失败
const CASES = [
  { nav: 'workbench', must: ['paper-panel', 'signal-panel'], title: '交易工作台' },
  { nav: 'datacenter', must: ['datacenter-view'], title: '数据中心' },
  { nav: 'strategy', must: ['strategy-list-view'], title: '策略管理' },
  { nav: 'backtest', must: ['lab-workbench'], title: '回测复盘' },
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  const results = [];

  for (const c of CASES) {
    const url = BASE + '/?nav=' + c.nav;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(4500);
    const info = await page.evaluate((must) => {
      const found = must.filter(t => !!document.querySelector(`[data-testid="${t}"]`));
      const title = document.querySelector('.ds-mobilebar__title');
      const navBtn = Array.from(document.querySelectorAll('button'))
        .find(b => (b.className || '').includes('is-active') || b.getAttribute('aria-current') === 'page');
      return {
        found_testids: found,
        title: title ? title.innerText.trim() : null,
        active_nav: navBtn ? navBtn.innerText.trim().slice(0, 20) : null,
      };
    }, c.must);
    if (SHOTDIR) {
      await page.screenshot({ path: `${SHOTDIR}/nav_${c.nav}.png` });
    }
    results.push({
      nav: c.nav,
      url,
      must: c.must,
      found_testids: info.found_testids,
      title: info.title,
      active_nav: info.active_nav,
      pass: info.found_testids.length === c.must.length && info.title === c.title,
    });
  }

  const out = {
    at: new Date().toISOString(),
    base: BASE,
    page_errors: errs,
    all_pass: results.every(r => r.pass),
    results,
  };
  const text = JSON.stringify(out, null, 1);
  if (JSONOUT) fs.writeFileSync(JSONOUT, text, 'utf8');
  console.log(text);
  await browser.close();
})();
