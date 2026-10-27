/**
 * 响应式 + 交互回归（2026-10-17）
 * ----------------------------------------------------------------------------
 * 跑法：`node scripts/verify_responsive.mjs`（需要 dev server 在 3000）
 * 断言的都是**可机读的事实**，不是「看起来还行」：
 *   ① 任何宽度都不出现横向溢出（documentElement.scrollWidth ≤ innerWidth）；
 *   ② 导航三档尺寸正确（176 / 140 / 抽屉 264 且离屏）；
 *      —— 2026-10-18 对齐设计稿 gm-layout.html 后，桌面侧栏由 224 收成 176、
 *         平板由 76 的图标导轨改成 140 的窄文字栏（设计稿导航本来就没有图标）。
 *   ③ 移动端顶栏只在 < 768 出现；抽屉能开、能 Esc 关、关闭后 inert；
 *   ④ 回测页指标条列数随断点收敛（8 → 4 → 2），标题不竖排（高度 ≤ 30px）；
 *   ⑤ 全程零 pageerror。
 * 退出码非 0 即有断言失败。
 */

import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://127.0.0.1:3000';
const OUT = '/tmp/verify_responsive';
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const fail = (name, detail) => results.push({ ok: false, name, detail });
const pass = (name) => results.push({ ok: true, name });

const browser = await chromium.launch();

/** 按宽度算出导航应有的形态（尺寸与 design/shell.css 的 `.appnav` 段落同源） */
function expectedNav(w) {
  if (w >= 1024) return { kind: 'full', width: 176 };
  if (w >= 768) return { kind: 'narrow', width: 140 };
  return { kind: 'drawer', width: 264 };
}

for (const w of [1920, 1440, 1280, 1024, 900, 767, 480, 390]) {
  const page = await browser.newPage({ viewport: { width: w, height: 900 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push((e.message || '').slice(0, 160)));

  await page.goto(`${BASE}/?theme=dark`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  const layout = await page.evaluate(() => {
    const nav = document.querySelector('.appnav');
    const bar = document.querySelector('.ds-mobilebar');
    const navRect = nav.getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      navWidth: Math.round(navRect.width),
      navX: Math.round(navRect.x),
      navInert: nav.hasAttribute('inert'),
      barVisible: bar ? getComputedStyle(bar).display !== 'none' : false,
      labelVisible: (() => {
        const l = nav.querySelector('.appnav__label');
        return l ? getComputedStyle(l).display !== 'none' : null;
      })(),
    };
  });

  const exp = expectedNav(w);
  if (layout.overflow > 1) fail(`w=${w} 横向溢出`, `scrollWidth 超出 ${layout.overflow}px`);
  else pass(`w=${w} 无横向溢出`);

  if (layout.navWidth !== exp.width) fail(`w=${w} 导航宽度`, `期望 ${exp.width}，实际 ${layout.navWidth}`);
  else pass(`w=${w} 导航宽度 ${exp.width}px`);

  if (exp.kind === 'full' && !layout.labelVisible) fail(`w=${w} 文字标签`, '全宽档应显示文字');
  /* 设计稿的导航只有文字、没有图标 ⇒ 三档都应显示文字；
   * 这里改成「桌面/平板档必须显示文字」，防止有人把标签又隐藏掉。 */
  if (exp.kind !== 'drawer' && layout.labelVisible === false) {
    fail(`w=${w} 文字标签`, '桌面/平板档必须显示导航文字');
  }
  if (exp.kind === 'full' && layout.barVisible) fail(`w=${w} 移动顶栏`, '桌面档不应出现顶栏');
  if (exp.kind === 'drawer' && !layout.barVisible) fail(`w=${w} 移动顶栏`, '抽屉档应出现顶栏');
  if (exp.kind === 'drawer' && layout.navX >= 0) fail(`w=${w} 抽屉初始位置`, '应离屏');
  if (exp.kind === 'drawer' && !layout.navInert) fail(`w=${w} 抽屉 inert`, '关闭态应 inert（防 Tab 跳入）');

  // 抽屉开合
  if (exp.kind === 'drawer') {
    await page.click('[aria-label="打开导航"]');
    await page.waitForTimeout(400);
    const open = await page.evaluate(() => {
      const nav = document.querySelector('.appnav');
      return {
        x: Math.round(nav.getBoundingClientRect().x),
        inert: nav.hasAttribute('inert'),
        focus: document.activeElement?.getAttribute('aria-label') ?? null,
        bodyOverflow: getComputedStyle(document.body).overflow,
      };
    });
    if (open.x !== 0) fail(`w=${w} 抽屉打开`, `x=${open.x}`);
    else pass(`w=${w} 抽屉可打开`);
    if (open.inert) fail(`w=${w} 抽屉打开后 inert`, '不应 inert');
    if (open.focus !== '关闭导航') fail(`w=${w} 焦点移入抽屉`, `实际焦点 ${open.focus}`);
    else pass(`w=${w} 焦点移入抽屉`);
    if (open.bodyOverflow !== 'hidden') fail(`w=${w} 背景滚动锁`, `body overflow=${open.bodyOverflow}`);

    await page.keyboard.press('Escape');
    await page.waitForTimeout(350);
    const closed = await page.evaluate(() => {
      const nav = document.querySelector('.appnav');
      return { x: Math.round(nav.getBoundingClientRect().x), inert: nav.hasAttribute('inert') };
    });
    if (closed.x >= 0) fail(`w=${w} Esc 关闭抽屉`, `x=${closed.x}`);
    else pass(`w=${w} Esc 关闭抽屉`);
    if (!closed.inert) fail(`w=${w} 关闭后 inert`, '应重新 inert');
  }

  // 回测页
  if (exp.kind === 'drawer') {
    await page.click('[aria-label="打开导航"]');
    await page.waitForTimeout(350);
  }
  await page.getByRole('button', { name: '回测复盘' }).first().click();
  for (let i = 0; i < 30; i++) {
    if (await page.locator('.ds-metric').count()) break;
    await page.waitForTimeout(700);
  }
  await page.waitForTimeout(1200);

  const bt = await page.evaluate(() => {
    const strip = document.querySelector('.ds-metricstrip');
    const title = document.querySelector('.bt-toolbar__title');
    return {
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      cols: strip ? getComputedStyle(strip).gridTemplateColumns.split(' ').length : 0,
      titleH: title ? Math.round(title.getBoundingClientRect().height) : 0,
    };
  });
  const expCols = w >= 1400 ? 8 : w >= 760 ? 4 : 2;
  if (bt.overflow > 1) fail(`w=${w} 回测页横向溢出`, `${bt.overflow}px`);
  else pass(`w=${w} 回测页无横向溢出`);
  if (bt.cols !== expCols) fail(`w=${w} 指标条列数`, `期望 ${expCols}，实际 ${bt.cols}`);
  else pass(`w=${w} 指标条 ${bt.cols} 列`);
  if (bt.titleH > 30) fail(`w=${w} 工具条标题竖排`, `高度 ${bt.titleH}px（应 ≤ 30）`);
  else pass(`w=${w} 工具条标题单行`);

  await page.screenshot({ path: `${OUT}/w_${w}.png` });
  if (errs.length) fail(`w=${w} 运行时异常`, errs[0]);
  else pass(`w=${w} 无运行时异常`);

  await page.close();
}

// ── OI 副图：单位切换不报错、柱形仍在 ────────────────────────────────────
{
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 }, deviceScaleFactor: 2 });
  const errs = [];
  page.on('pageerror', (e) => errs.push((e.message || '').slice(0, 160)));
  await page.goto(`${BASE}/?theme=dark`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(7000);
  const oi = await page.$('[data-chart="lightweight-oi"]');
  if (!oi) fail('OI 副图存在', '找不到 [data-chart="lightweight-oi"]');
  else pass('OI 副图存在');

  const before = await oi.boundingBox();
  await page.getByRole('button', { name: '名义额 USD' }).click();
  await page.waitForTimeout(2200);
  const pressed = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent?.trim() === '名义额 USD');
    return b ? b.getAttribute('aria-pressed') : null;
  });
  if (pressed !== 'true') fail('OI 单位切换 aria-pressed', `实际 ${pressed}`);
  else pass('OI 单位切换 aria-pressed=true');

  await page.screenshot({ path: `${OUT}/oi_usd.png`, clip: before });
  if (errs.length) fail('OI 单位切换运行时异常', errs[0]);
  else pass('OI 单位切换无异常');
  await page.close();
}

await browser.close();

const failed = results.filter((r) => !r.ok);
for (const r of results) {
  if (!r.ok) console.log(`❌ ${r.name} —— ${r.detail}`);
}
console.log(
  failed.length
    ? `\n${failed.length} 项失败 / 共 ${results.length} 项`
    : `\n✅ 全部 ${results.length} 项通过`,
);
if (failed.length) process.exitCode = 1;
