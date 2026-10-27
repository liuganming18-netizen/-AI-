// 40_render_real_candidate.js — 把隔离环境的**真实候选**响应喂给生产页面，验证渲染。
//
// 为什么这样做：生产自然候选为 0，页面永远是空态，所以「有信号时能不能显示」在生产从未被验证过。
// 做法是**只拦截一个请求的响应**，不写库、不改生产数据、不碰任何文件：
//   · 候选来自隔离库（SIGNAL-UI-LINK-07-R1/iso_live），是真实产生的，不是手编的
//   · 只替换 /api/v1/signals?scope=current 的响应体，其他请求照常走生产
// 判据：页面出现「交易信号 1 笔」且出现该币种代号 ⇒ 渲染链路通。
//
// 用法（先起隔离入口）：
//   python 40_iso_serve.py --mode live --start     # 在 SIGNAL-LIVE-CHECK-09/probes 下
//   node 40_render_real_candidate.js

const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');
const req = createRequire('/Users/boos/.workbuddy-ai/binaries/node/workspace/');
const { chromium } = req('playwright');

const OUT = path.resolve(__dirname, '../out');
const FE = 'http://127.0.0.1:3000/';
const ISO = 'http://127.0.0.1:8799/api/v1/signals?scope=current&limit=500';

(async () => {
  // 1) 先取隔离环境的真实候选响应
  const r = await fetch(ISO);
  const real = await r.text();
  const parsed = JSON.parse(real);
  const ids = (parsed.items || []).map(x => x.signal_id);
  const symbols = (parsed.items || []).map(x => x.symbol);

  // 2) 打开生产页面，只改写 scope=current 这一个响应
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + String(e).slice(0, 200)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });

  let intercepted = 0;
  await page.route('**/api/v1/signals**', async route => {
    if (route.request().url().includes('scope=current')) {
      intercepted++;
      await route.fulfill({ status: 200, contentType: 'application/json', body: real });
    } else {
      await route.continue();
    }
  });

  await page.goto(FE, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(9000);

  const state = await page.evaluate(syms => {
    const txt = document.body.innerText;
    const hit = syms.map(s => txt.includes(s.replace(/USDT$/, '')) || txt.includes(s));
    return {
      count_line: (txt.match(/交易信号\s*\n?\s*(\d+)\s*笔/) || [])[0] || null,
      symbol_visible: hit,
      still_empty: txt.includes('暂无交易信号'),
    };
  }, symbols);

  await page.screenshot({ path: path.join(OUT, '40_render_with_real_candidate.png'),
    clip: { x: 0, y: 0, width: 340, height: 950 } });

  const res = {
    probe: 'SIGNAL-CHAIN-DIAG-10/render',
    generated_utc: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    '隔离候选来源': ISO,
    '隔离响应 total': parsed.total,
    '隔离候选 ID': ids,
    '隔离候选币种': symbols,
    '隔离 quality.status': parsed?.quality?.status,
    '拦截次数': intercepted,
    '页面信号计数行': state.count_line,
    '币种在页面上可见': state.symbol_visible,
    '页面仍是空态': state.still_empty,
    '控制台错误': errs,
    '结论': (!state.still_empty && state.symbol_visible.every(Boolean) && intercepted > 0)
      ? '通过：真实候选能在生产页面渲染出来'
      : '不通过：喂了真实候选页面仍不显示',
    '没证明什么': '这不等于「生产自己产生候选时也会显示」——那要等自然候选出现；这里只证明渲染链路本身通',
  };

  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, '40_render_with_real_candidate.json'),
    JSON.stringify(res, null, 1), 'utf8');
  console.log(JSON.stringify(res, null, 1));
  await browser.close();
})();
