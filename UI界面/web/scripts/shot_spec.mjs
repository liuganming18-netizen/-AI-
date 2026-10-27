/**
 * 设计规范页截图（本地 file:// 打开，验证 tokens.css + components.css + backtest.css 渲染正常）。
 * 用法：node scripts/shot_spec.mjs <输出目录>
 *
 * 2026-10-17 起同时验证双主题：同一页在 dark / light 下各截一遍，
 * 并断言「两块 data-theme 面板读到的是各自的值」—— 否则双主题面板只是装饰。
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || '/tmp/ds_spec';
fs.mkdirSync(OUT, { recursive: true });
const url = 'file://' + path.resolve(process.cwd(), '设计规范.html');

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(url, { waitUntil: 'load' });
await page.waitForTimeout(600);

const probe = await page.evaluate(() => {
  const cs = getComputedStyle(document.documentElement);
  const card = document.querySelector('.ds-card');
  const btn = document.querySelector('.ds-btn--primary');
  const darkPanel = document.querySelector('.themepanel[data-theme="dark"]');
  const lightPanel = document.querySelector('.themepanel[data-theme="light"]');
  const read = (el, tok) => (el ? getComputedStyle(el).getPropertyValue(tok).trim() : null);
  return {
    theme: document.documentElement.dataset.theme,
    accent: cs.getPropertyValue('--ds-accent').trim(),
    text: cs.getPropertyValue('--ds-text').trim(),
    text3: cs.getPropertyValue('--ds-text-3').trim(),
    text4: cs.getPropertyValue('--ds-text-4').trim(),
    cardBg: card ? getComputedStyle(card).backgroundColor : null,
    cardBorder: card ? getComputedStyle(card).borderTopColor : null,
    btnBg: btn ? getComputedStyle(btn).backgroundColor : null,
    bodyBg: getComputedStyle(document.body).backgroundColor,
    swatchCount: document.querySelectorAll('.sw').length,
    sectionCount: document.querySelectorAll('section').length,
    // 双主题面板必须解析出不同的 n-900
    panelDarkN900: read(darkPanel, '--ds-n-900'),
    panelLightN900: read(lightPanel, '--ds-n-900'),
    // data-token 是否被脚本填上（不能为空、不能是 fallback）
    filledTokens: Array.from(document.querySelectorAll('[data-token]')).filter(
      (n) => n.textContent.trim().length > 0
    ).length,
    tokenTotal: document.querySelectorAll('[data-token]').length,
  };
});
console.log('令牌探测:', JSON.stringify(probe, null, 2));

const fail = [];
if (probe.panelDarkN900 === probe.panelLightN900) fail.push('双主题面板读到了同一个值，data-theme 没生效');
if (probe.filledTokens !== probe.tokenTotal) fail.push(`data-token 未全部填充 ${probe.filledTokens}/${probe.tokenTotal}`);
if (probe.text3 === probe.text4) fail.push('text-3 / text-4 同值，层级没有区分');
if (fail.length) {
  console.error('❌ ' + fail.join('；'));
  process.exitCode = 1;
}

await page.screenshot({ path: path.join(OUT, 'spec_01_top.png') });
await page.screenshot({ path: path.join(OUT, 'spec_02_full.png'), fullPage: true });

for (const [name, sel] of [
  ['color', '#color'],
  ['theme', '#theme'],
  ['type', '#type'],
  ['stat', '#stat'],
  ['table', '#table'],
  ['chart', '#chart'],
  ['backtest', '#backtest'],
  ['usage', '#usage'],
]) {
  await page.evaluate((s) => document.querySelector(s)?.scrollIntoView(), sel);
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, `spec_${name}.png`) });
  console.log('saved', `spec_${name}.png`);
}

// ── 切到浅色再截一遍关键区块 ─────────────────────────────────────────────
await page.click('#themeToggle');
await page.waitForTimeout(400);
const after = await page.evaluate(() => {
  const read = (el, tok) => (el ? getComputedStyle(el).getPropertyValue(tok).trim() : null);
  const darkPanel = document.querySelector('.themepanel[data-theme="dark"]');
  const lightPanel = document.querySelector('.themepanel[data-theme="light"]');
  return {
    theme: document.documentElement.dataset.theme,
    stored: localStorage.getItem('wk-theme'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    panelDarkN900: read(darkPanel, '--ds-n-900'),
    panelLightN900: read(lightPanel, '--ds-n-900'),
    accentText: getComputedStyle(document.documentElement).getPropertyValue('--ds-accent-text').trim(),
  };
});
console.log('切换后:', JSON.stringify(after));
if (after.theme !== 'light') {
  console.error('❌ 主题开关没有切到 light');
  process.exitCode = 1;
}
// 关键：切到浅色后，左侧「深色面板」必须仍然解析出深色值。
// 只在 :root 上声明深色阶梯时这里会失败 —— 深色面板会跟着整页变浅。
if (after.panelDarkN900 === after.panelLightN900) {
  console.error('❌ 浅色主题下深色面板被整页污染，data-theme="dark" 没有独立作用域');
  process.exitCode = 1;
}

for (const [name, sel] of [
  ['theme', '#theme'],
  ['backtest', '#backtest'],
  ['color', '#color'],
]) {
  await page.evaluate((s) => document.querySelector(s)?.scrollIntoView(), sel);
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, `spec_light_${name}.png`) });
  console.log('saved', `spec_light_${name}.png`);
}

await browser.close();
console.log('done');
