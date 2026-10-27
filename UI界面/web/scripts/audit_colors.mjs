/**
 * 配色逐项核对：设计稿 `gm-layout.html`  vs  实际应用 `127.0.0.1:3000`
 * ----------------------------------------------------------------------------
 * 只读脚本：不改任何状态、不写任何 run 目录。
 * 用途：用户要求「页面与渲染颜色一模一样」⇒ 这里把两边的**计算后样式**逐项比对，
 * 输出「设计稿色值 / 实际色值 / 是否一致」三列。色值统一归一成 `#rrggbb`。
 *
 * 用法：node scripts/audit_colors.mjs
 */
import { chromium } from 'playwright';

const MOCK = process.env.MOCK_URL || 'file:///tmp/gm_files/gm-layout.html';
const APP = process.env.APP_URL || 'http://127.0.0.1:3000/';

/** 要核对的色位：同一语义在设计稿与真实应用里的选择器。 */
const CASES = [
  { k: '页面画布', mock: 'body', app: 'body', prop: 'backgroundColor' },
  { k: '正文颜色', mock: 'body', app: 'body', prop: 'color' },
  { k: '侧栏底', mock: '.side', app: '.appnav', prop: 'backgroundColor' },
  { k: '侧栏右边框', mock: '.side', app: '.appnav', prop: 'borderRightColor' },
  { k: '侧栏当前项底', mock: '.nav.on', app: '.appnav__item.is-active', prop: 'backgroundColor' },
  { k: '侧栏当前项字', mock: '.nav.on', app: '.appnav__item.is-active', prop: 'color' },
  { k: '顶栏底', mock: '.topbar', app: '.ds-topbar', prop: 'backgroundColor' },
  { k: '顶栏字', mock: '.topbar', app: '.ds-topbar', prop: 'color' },
  { k: '顶栏值字', mock: '.topbar b', app: '.ds-topbar b', prop: 'color' },
  { k: '页签条底', mock: '.tabs2', app: '.ds-seg', prop: 'backgroundColor' },
  { k: '页签选中底', mock: '.tabs2 b.on', app: '.ds-seg__item[aria-selected="true"]', prop: 'backgroundColor' },
  { k: '页签未选字', mock: '.tabs2 b', app: '.ds-seg__item', prop: 'color' },
  { k: '卡片底', mock: '.c', app: '.ds-card', prop: 'backgroundColor' },
  { k: '卡片描边', mock: '.c', app: '.ds-card', prop: 'borderTopColor' },
  { k: '卡片圆角', mock: '.c', app: '.ds-card', prop: 'borderTopLeftRadius' },
  { k: '卡片内边距', mock: '.c', app: '.ds-card', prop: 'paddingTop' },
  { k: '指标卡底', mock: '.k', app: '.ds-metric', prop: 'backgroundColor' },
  { k: '指标卡描边', mock: '.k', app: '.ds-metric', prop: 'borderTopColor' },
  { k: '指标卡标签字', mock: '.k .l', app: '.ds-metric__k', prop: 'color' },
  /* 数值字用第 5 格「成交笔数」：两边都是中性值，不会因为一边盈一边亏而误判 */
  { k: '指标卡数值字', mock: '.k:nth-child(5) .v', app: '.ds-metric:nth-child(5) .ds-metric__v', prop: 'color' },
  { k: '指标卡数值字号', mock: '.k:nth-child(5) .v', app: '.ds-metric:nth-child(5) .ds-metric__v', prop: 'fontSize' },
  { k: '指标卡副标字号', mock: '.k .s', app: '.ds-metric__note', prop: 'fontSize' },
  { k: '次级按钮描边', mock: '.btn', app: '.ds-btn--secondary', prop: 'borderTopColor' },
  { k: '涨色', mock: '.up', app: '.ds-topbar .up', prop: 'color' },
  { k: '跌色', mock: '.k .v.dn', app: '.ds-metric__v--neg', prop: 'color' },
  { k: '弱字', mock: '.m', app: '.ds-pagehead__sub', prop: 'color' },
  { k: '提示条底', mock: '.warn', app: '.ds-warnbar', prop: 'backgroundColor' },
  { k: '提示条边', mock: '.warn', app: '.ds-warnbar', prop: 'borderTopColor' },
  { k: '提示条字', mock: '.warn', app: '.ds-warnbar', prop: 'color' },
];

/** 归一：`rgb(23, 32, 28)` → `#17201c`；带透明度的补 `/ α0.32`；长度值原样。 */
function norm(v) {
  if (!v) return v;
  const m = String(v).match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
  if (!m) return String(v).replace(/px$/, 'px');
  const [r, g, b] = [+m[1], +m[2], +m[3]];
  const a = m[4] === undefined ? 1 : +m[4];
  const hex = '#' + [r, g, b].map((x) => Math.round(x).toString(16).padStart(2, '0')).join('');
  return a === 1 ? hex : `${hex} / α${a}`;
}

const read = (page, sel, prop) =>
  page.evaluate(
    ({ sel, prop }) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      return getComputedStyle(el)[prop] ?? null;
    },
    { sel, prop },
  );

const browser = await chromium.launch();

const mp = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
await mp.goto(MOCK);
await mp.waitForTimeout(600);
await mp.locator('.nav[data-pg="bt"]').click();
await mp.waitForTimeout(600);

const ap = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
await ap.goto(APP, { waitUntil: 'networkidle' });
await ap.getByRole('button', { name: '回测复盘' }).click();
await ap.waitForTimeout(5000);
/* 运行列表的行。⚠ 批次逐笔表里也有 `data-run-id`（跨 run 的成交行），
 * 且它现在收在折叠里 ⇒ 必须只取「可见」的那一行，否则点到隐藏元素超时。 */
const row = ap.locator('[data-run-id]:visible').first();
if (await row.count()) {
  await row.click();
  await ap.waitForTimeout(4000);
}

let pass = 0;
let fail = 0;
const rows = [];
for (const c of CASES) {
  const mv = await read(mp, c.mock, c.prop);
  const av = await read(ap, c.app, c.prop);
  const same = mv != null && av != null && norm(mv) === norm(av);
  if (same) pass++;
  else fail++;
  rows.push({
    色位: c.k,
    设计稿: norm(mv) ?? '元素缺失',
    实际: norm(av) ?? '元素缺失',
    结果: same ? 'OK' : 'DIFF',
  });
}

console.table(rows);
console.log(`\n一致 ${pass} / 不一致 ${fail} / 共 ${pass + fail}`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
