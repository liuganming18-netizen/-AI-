/**
 * 抓「数据链路画布」七段真实判定（只读；不改页面、不写任何业务状态）。
 *
 * 为什么要用真浏览器：判定逻辑在页面 JS 里，只有让它自己跑完，
 * 拿到的 state / blocker 才是页面真正展示给用户的东西。
 * 用接口拼凑出来的是「我以为的判据」，不是「页面实际判的」。
 */
import { createRequire } from 'node:module';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '../../..');
const WEB = resolve(ROOT, 'UI界面/web');
const OUT = resolve(ROOT, 'evidence/DB-PERF-01/out');
mkdirSync(OUT, { recursive: true });

const require = createRequire(resolve(WEB, 'package.json'));
const { chromium } = require('playwright');

const URL_ = process.env.CANVAS_URL || 'http://127.0.0.1:3000/system-flow.html';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });

const netLog = [];
page.on('response', (r) => {
  const u = r.url();
  if (u.includes('/api/v1/')) netLog.push({ url: u.replace(/^https?:\/\/[^/]+/, ''), status: r.status() });
});

await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 60000 });

// 页面首轮探测是并发的；等 #verdict 的 data-state 从 unv 变成最终态，
// 再额外等一轮，避免抓到「第一段已出、后面还没出」的中间态。
// 注意：waitForFunction 第二个位置参数是「传给页面函数的 arg」，不是 options。
// 以前把 {timeout} 当 arg 传了，实际一直用默认 30s，页慢一点就假超时。
await page.waitForFunction(() => {
  const t = document.getElementById('verdict-title');
  const chips = document.querySelectorAll('#flow button.chip');
  return t && chips.length >= 7 && !/正在读取/.test(t.textContent || '');
}, null, { timeout: 120000 });

await page.waitForTimeout(5000);

const result = await page.evaluate(() => {
  const out = { verdict: {}, chips: [], nodes: [], build_id: '', meta: '' };
  const v = document.getElementById('verdict');
  out.verdict.state = v.dataset.state;
  out.verdict.title = document.getElementById('verdict-title').textContent.trim();
  out.verdict.sub = document.getElementById('verdict-sub').textContent.trim();
  out.meta = (document.getElementById('verdict-meta').innerText || '').replace(/\s+/g, ' ').trim();
  out.build_id = (document.getElementById('build-id') || {}).textContent || '';

  document.querySelectorAll('#flow button.chip').forEach((c) => {
    out.chips.push({
      no_name: (c.querySelector('.cn') || {}).textContent || '',
      state: c.dataset.state || '',
      label: (c.querySelector('.cs') || {}).textContent || '',
    });
  });

  document.querySelectorAll('#chain article.chain-node').forEach((n) => {
    const item = {
      key: n.dataset.node || '',
      name: ((n.querySelector('.nm') || {}).textContent || '').trim(),
      state: n.dataset.state || '',
      pill: ((n.querySelector('[data-pill]') || {}).textContent || '').trim(),
      headline: ((n.querySelector('[data-headline]') || {}).innerText || '').replace(/\s+/g, ' ').trim(),
      facts: ((n.querySelector('[data-facts]') || {}).innerText || '').replace(/\s+/g, ' ').trim(),
      blocker: ((n.querySelector('[data-blocker]') || {}).innerText || '').replace(/\s+/g, ' ').trim(),
    };
    out.nodes.push(item);
  });
  return out;
});

result.net = netLog;
result.captured_at_utc = new Date().toISOString();
writeFileSync(resolve(OUT, 'canvas_state.json'), JSON.stringify(result, null, 2));

await page.screenshot({ path: resolve(OUT, 'canvas_full.png'), fullPage: true });
await browser.close();

console.log(JSON.stringify({
  verdict: result.verdict,
  build: result.build_id,
  chips: result.chips,
  nodes: result.nodes.map((n) => ({ key: n.key, name: n.name, state: n.state, pill: n.pill, blocker: n.blocker.slice(0, 200) })),
}, null, 2));
