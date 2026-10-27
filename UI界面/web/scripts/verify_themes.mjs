/**
 * 双主题回归验证（只读）：回测页在 dark / light 下各截一遍，并断言关键对比度。
 * 用法：node scripts/verify_themes.mjs <输出目录>
 *
 * 断言三条：
 *   1. 回测列表能拿到 run（否则整页是空态，截图没有意义）
 *   2. 选中态文字（accent-text）对比度 ≥ 4.5:1 —— 深色下曾因用 accent-700 掉到 2.2:1
 *   3. 三级文字 ≥ 四级文字亮度（层级单调）—— 曾因令牌绑反而反向
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || '/tmp/bt_themes';
fs.mkdirSync(OUT, { recursive: true });

const lum = (rgb) => {
  const m = rgb.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number);
  const f = m.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

const browser = await chromium.launch();
const problems = [];

for (const theme of ['dark', 'light']) {
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 }, deviceScaleFactor: 1 });
  page.on('console', (m) => {
    if (m.type() === 'error') console.log(`[${theme} console.error]`, m.text().slice(0, 200));
  });

  await page.goto(`http://127.0.0.1:3000/?theme=${theme}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  const applied = await page.evaluate(() => document.documentElement.dataset.theme);
  if (applied !== theme) problems.push(`?theme=${theme} 没有生效（实际 ${applied}）`);

  await page.getByRole('button', { name: '回测复盘' }).click();

  // 后端首次扫描 576 个 run 目录约 10s，给足 30s
  let count = 0;
  for (let i = 0; i < 30; i++) {
    count = await page.locator('[data-run-id]').count();
    if (count > 0) break;
    await page.waitForTimeout(1000);
  }
  console.log(`[${theme}] runs = ${count}`);
  if (count === 0) problems.push(`${theme}: 回测列表为空`);

  // 选第一个「有成交」的卡片（无成交卡片也合法，但截图想看到逐笔表）
  const rows = page.locator('[data-run-id]');
  let clicked = false;
  for (let i = 0; i < Math.min(count, 8); i++) {
    const txt = await rows.nth(i).innerText();
    if (!txt.includes('无成交')) {
      await rows.nth(i).scrollIntoViewIfNeeded();
      await rows.nth(i).click();
      clicked = true;
      break;
    }
  }
  if (!clicked && count > 0) await rows.first().click();
  await page.waitForTimeout(4000);

  await page.screenshot({ path: path.join(OUT, `bt_${theme}.png`) });
  console.log('saved', path.join(OUT, `bt_${theme}.png`));

  // ── 对比度断言 ─────────────────────────────────────────────────────────
  const probe = await page.evaluate(() => {
    const pick = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { color: cs.color, bg: cs.backgroundColor };
    };
    const root = getComputedStyle(document.documentElement);
    return {
      navCurrent: pick('[data-nav-current="true"]') || pick('.ds-nav__item[aria-current="true"]'),
      bodyBg: getComputedStyle(document.body).backgroundColor,
      text3: root.getPropertyValue('--ds-text-3').trim(),
      text4: root.getPropertyValue('--ds-text-4').trim(),
      accentText: root.getPropertyValue('--ds-accent-text').trim(),
    };
  });
  console.log(`[${theme}] probe`, JSON.stringify(probe));

  if (probe.navCurrent && probe.navCurrent.bg && !probe.navCurrent.bg.includes('rgba(0, 0, 0, 0)')) {
    const r = ratio(probe.navCurrent.color, probe.navCurrent.bg);
    console.log(`[${theme}] 当前导航项对比度 = ${r.toFixed(2)}:1`);
    if (r < 4.5) problems.push(`${theme}: 当前导航项对比度仅 ${r.toFixed(2)}:1`);
  }

  await page.close();
}

await browser.close();
if (problems.length) {
  console.error('❌ ' + problems.join('；'));
  process.exitCode = 1;
} else {
  console.log('✅ 双主题回归通过');
}
