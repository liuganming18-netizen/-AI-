/**
 * PR2-WO-01 · 前端显示语义最小修复 —— DOM 行为测试
 *
 * 跑法：`node tests/display-semantics.dom.test.mjs`
 *
 * 做法：用 rolldown（仓库自带，vite 8 同款打包器）把夹具入口（真实组件 + 夹具 props）
 * 打成单文件，注入 Chromium，断言**页面上真正显示出来的文字**。不启服务、不连后端、
 * 不写仓库目录（产物落在系统临时目录），因此不会与任何运行中的实例互相干扰。
 *
 * 为什么不用现成的 dev server：本工单要求「所有测试必须在隔离环境完成」，
 * 起 3000 端口会和生产 dev 实例抢同一份缓存与端口。
 * 为什么不用 esbuild：本机 node_modules 缺 `@esbuild/darwin-arm64` 原生二进制，跑不起来。
 */
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const outfile = path.join(os.tmpdir(), `pr2-wo-01-dom-${process.pid}.js`);

const failures = [];
let total = 0;
const check = (name, cond, detail = '') => {
  total += 1;
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`);
  }
};

execFileSync(
  path.join(webRoot, 'node_modules', '.bin', 'rolldown'),
  [
    path.join(here, 'display-semantics.dom.harness.tsx'),
    '-o',
    outfile,
    '-f',
    'iife',
    '-p',
    'browser',
    '--transform.jsx',
    'react-jsx',
    '--transform.define',
    'process.env.NODE_ENV:"production"',
  ],
  { cwd: webRoot, stdio: ['ignore', 'ignore', 'pipe'] },
);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 900, height: 1400 } });
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push((e.message || '').slice(0, 200)));

await page.setContent('<!doctype html><html><body><div id="root"></div></body></html>');
await page.addScriptTag({ path: outfile });
await page.waitForSelector('[data-testid="observe-panel"]', { timeout: 15000 });

const rowSel = (sym) => `[data-testid="observe-row"][data-symbol="${sym}"]`;
const priceOf = async (sym) => {
  const row = page.locator(rowSel(sym));
  return {
    label: (await row.locator('[data-testid="observe-price-label"]').innerText()).trim(),
    source: await row.locator('[data-testid="observe-price-label"]').getAttribute('data-price-source'),
    value: (await row.locator('[data-testid="observe-price-value"]').innerText()).trim(),
  };
};

/* ───── 修复 C：价格标签按来源给 ───── */

const candle = await priceOf('CANDLEUSDT');
check('C1 只有 last_price + last_bar_utc ⇒ 界面显示「K线收盘价」', candle.label === 'K线收盘价', `实得「${candle.label}」`);
check('C1b data-price-source 标为 candle_close', candle.source === 'candle_close', `实得 ${candle.source}`);
check('C1c 数值照常显示（68123.45）', candle.value === '68123.45', `实得 ${candle.value}`);

const rt = await priceOf('REALTIMEUSDT');
check('C2 载荷显式带实时报价 ⇒ 界面显示「最新价」', rt.label === '最新价', `实得「${rt.label}」`);
check('C2b 取实时报价值而非 bar 收盘价（68450.12）', rt.value === '68450.12', `实得 ${rt.value}`);

const unsourced = await priceOf('UNSOURCEDUSDT');
check('C3 有数值但来源不可确认 ⇒ 不标实时价，标「来源未确认」', unsourced.label === '来源未确认', `实得「${unsourced.label}」`);

const noPrice = await priceOf('NOPRICEUSDT');
check('C4 没有价格 ⇒ 「价格未知」且值为占位符', noPrice.label === '价格未知' && noPrice.value === '—', `实得「${noPrice.label}」/${noPrice.value}`);

const panelText = await page.locator('[data-testid="observe-panel"]').innerText();
check('C5 面板上不再出现旧的「现价」标签', !panelText.includes('现价'), '仍含「现价」');

/* ───── 修复 B：别名按显式映射 ───── */

await page.locator(`${rowSel('CANDLEUSDT')} [data-testid="observe-row-toggle"]`).click();
const detail = page.locator(`${rowSel('CANDLEUSDT')} [data-testid="observe-evidence-detail"]`);
await detail.waitFor({ timeout: 5000 });

const rowText = async (key) =>
  (await detail.locator('div', { hasText: key }).first().innerText()).trim();

const aliasRow = await rowText('count_long_short_ratio');
check('B1 已声明成对且同值 ⇒ 标注为别名', aliasRow.includes('同源且同值＝别名'), `实得「${aliasRow}」`);

const independentRow = await rowText('count_toptrader_long_short_ratio');
check(
  'B2 未声明的指标即使数值相同，也**不得**被标成别名',
  !independentRow.includes('别名'),
  `实得「${independentRow}」`,
);

const detailText = await detail.innerText();
check(
  'B3 三列多空比在详情里逐列给全（一行都没被吞掉）',
  ['retail_account_ls_ratio', 'count_long_short_ratio', 'count_toptrader_long_short_ratio'].every((k) =>
    detailText.includes(k),
  ),
);

/* ───── 回归面 ───── */

check('R1 渲染过程无未捕获异常', pageErrors.length === 0, pageErrors.join(' | '));

await browser.close();

console.log(`\nDOM 行为测试：${total - failures.length}/${total} 通过`);
if (failures.length) {
  console.log('\n失败项：');
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
