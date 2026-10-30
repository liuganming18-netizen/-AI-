/**
 * BACKTEST-CHART-JITTER-FIX-01 · 探针 1：拖动 / 滚轮 / 十字光标期间的布局与重绘计数
 *
 * 只读探针：不改业务代码，只在页面里挂观测器（MutationObserver / ResizeObserver /
 * 方法包裹），把「谁在动、动了多少次」量化出来。
 *
 * 输出：evidence/BACKTEST-CHART-JITTER-FIX-01/probe/out/<label>_*.json
 */
import fs from 'node:fs';

const { chromium } = await import(
  'file:///Users/boos/Desktop/威科夫因子共振趋势/UI%E7%95%8C%E9%9D%A2/web/node_modules/playwright/index.mjs'
);

const RUN = process.env.RUN_ID || 'run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06';
const BASE = process.env.BASE || 'http://127.0.0.1:3000';
const LABEL = process.env.LABEL || 'baseline';
const OUT = '/Users/boos/Desktop/威科夫因子共振趋势/evidence/BACKTEST-CHART-JITTER-FIX-01/probe/out';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 300)));

await page.goto(`${BASE}/?view=backtest&run=${encodeURIComponent(RUN)}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('[data-chart="lightweight-candles"]', { timeout: 30000 });
await page.waitForTimeout(3000);

/* ── 安装观测器 ─────────────────────────────────────────────────────────── */
await page.evaluate(() => {
  const w = window;
  const host = document.querySelector('[data-chart="lightweight-candles"]');
  const box = host.parentElement;                       // 图表外层（h-[280px] 或 h-[460px]）
  const card = document.querySelector('[data-testid="lab-chart-panel"]');
  const hover = document.querySelector('.ds-hoverbar');
  const scroll = document.querySelector('[data-backtest-scroll]');

  const s = (w.__jitter = {
    frames: 0,
    container: [],          // {t,w,h,x,y} 每帧
    boxH: [],               // 图表外层高度变化
    hoverH: [],             // 读数条高度变化
    cardY: [],              // 卡片纵向位置变化
    scrollH: [],            // 页面滚动高度变化
    docW: [],               // 文档客户区宽度变化（滚动条出现/消失）
    roContainer: 0,         // 图表容器尺寸变化次数（自挂 ResizeObserver）
    roBox: 0,
    rangeEvents: 0,         // 官方时间轴可见范围事件次数
    setVisibleRange: 0,     // 应用层 setVisibleRange 调用次数
    setVisibleLogicalRange: 0,
    tsApplyOptions: 0,
    chartApplyOptions: 0,
    scrollToRealTime: 0,
    fitContent: 0,
    hoverMutations: 0,      // 读数条 DOM 变更次数
    legendMutations: 0,
    phase: 'idle',
    phases: {},             // 每个阶段的重置点
  });

  const push = (arr, v) => {
    const last = arr[arr.length - 1];
    if (!last || last.v !== v) arr.push({ t: Math.round(performance.now()), v });
  };

  // 包裹应用层的时间轴方法（应用调用都走 chart.timeScale()）
  const h = w.__labChartHandle;
  if (h?.chart) {
    const ts = h.chart.timeScale();
    for (const m of ['setVisibleRange', 'setVisibleLogicalRange', 'scrollToRealTime', 'fitContent', 'applyOptions']) {
      const key =
        m === 'setVisibleRange' ? 'setVisibleRange'
        : m === 'setVisibleLogicalRange' ? 'setVisibleLogicalRange'
        : m === 'scrollToRealTime' ? 'scrollToRealTime'
        : m === 'fitContent' ? 'fitContent'
        : 'tsApplyOptions';
      const orig = ts[m]?.bind(ts);
      if (!orig) continue;
      ts[m] = (...args) => { s[key] += 1; return orig(...args); };
    }
    ts.subscribeVisibleLogicalRangeChange(() => { s.rangeEvents += 1; });
    const co = h.chart.applyOptions.bind(h.chart);
    h.chart.applyOptions = (...a) => { s.chartApplyOptions += 1; return co(...a); };
  }

  // 尺寸观测
  const ro1 = new ResizeObserver(() => { s.roContainer += 1; });
  ro1.observe(host);
  const ro2 = new ResizeObserver(() => { s.roBox += 1; });
  if (box) ro2.observe(box);

  // 读数条 DOM 变更
  if (hover) {
    new MutationObserver((list) => { s.hoverMutations += list.length; })
      .observe(hover, { childList: true, subtree: true, characterData: true });
  }
  const legend = document.querySelector('.ds-chartlegend');
  if (legend) {
    new MutationObserver((list) => { s.legendMutations += list.length; })
      .observe(legend, { childList: true, subtree: true, characterData: true });
  }

  // 每帧采样
  const tick = () => {
    s.frames += 1;
    const r = host.getBoundingClientRect();
    push(s.container, Math.round(r.width) * 10000 + Math.round(r.height)); // 宽高联合
    if (box) push(s.boxH, Math.round(box.getBoundingClientRect().height));
    if (hover) push(s.hoverH, Math.round(hover.getBoundingClientRect().height));
    if (card) push(s.cardY, Math.round(card.getBoundingClientRect().top));
    push(s.scrollH, scroll ? Math.round(scroll.scrollHeight) : 0);
    push(s.docW, document.documentElement.clientWidth);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const resetPhase = async (name) => {
  await page.evaluate((n) => {
    const s = window.__jitter;
    s.phase = n;
    s.phases[n] = {
      rangeEvents: s.rangeEvents,
      setVisibleRange: s.setVisibleRange,
      setVisibleLogicalRange: s.setVisibleLogicalRange,
      tsApplyOptions: s.tsApplyOptions,
      chartApplyOptions: s.chartApplyOptions,
      scrollToRealTime: s.scrollToRealTime,
      fitContent: s.fitContent,
      roContainer: s.roContainer,
      roBox: s.roBox,
      hoverMutations: s.hoverMutations,
      frames: s.frames,
      mark: {
        container: s.container.length,
        boxH: s.boxH.length,
        hoverH: s.hoverH.length,
        cardY: s.cardY.length,
        scrollH: s.scrollH.length,
        docW: s.docW.length,
      },
    };
  }, name);
};

const box = await page.locator('[data-chart="lightweight-candles"]').first().boundingBox();
const cx = box.x + box.width * 0.6;
const cy = box.y + box.height * 0.5;
console.log('chart box =', JSON.stringify(box));

/* ── 阶段 1：十字光标横向扫过（不按键） ───────────────────────────────── */
await resetPhase('crosshair');
await page.mouse.move(cx - 300, cy);
for (let i = 0; i < 60; i += 1) {
  await page.mouse.move(cx - 300 + i * 10, cy, { steps: 1 });
  await page.waitForTimeout(16);
}
await page.waitForTimeout(600);

/* ── 阶段 2：左键横向拖动（3 秒，慢速） ───────────────────────────────── */
await resetPhase('drag');
await page.mouse.move(cx, cy);
await page.mouse.down();
for (let i = 0; i < 90; i += 1) {
  await page.mouse.move(cx + Math.sin(i / 12) * 160, cy, { steps: 1 });
  await page.waitForTimeout(33);
}
await page.mouse.up();
await page.waitForTimeout(800);

/* ── 阶段 3：滚轮缩放 ─────────────────────────────────────────────────── */
await resetPhase('wheel');
await page.mouse.move(cx, cy);
for (let i = 0; i < 6; i += 1) {
  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(120);
}
for (let i = 0; i < 4; i += 1) {
  await page.mouse.wheel(0, 120);
  await page.waitForTimeout(120);
}
await page.waitForTimeout(600);

const dump = await page.evaluate(() => {
  const s = window.__jitter;
  // 每阶段增量
  const names = Object.keys(s.phases);
  const deltas = {};
  for (let i = 0; i < names.length; i += 1) {
    const cur = s.phases[names[i]];
    const prev = i > 0 ? s.phases[names[i - 1]] : null;
    const mk = (k) => (prev ? cur[k] - prev[k] : cur[k]);
    deltas[names[i]] = {
      rangeEvents: mk('rangeEvents'),
      setVisibleRange: mk('setVisibleRange'),
      setVisibleLogicalRange: mk('setVisibleLogicalRange'),
      tsApplyOptions: mk('tsApplyOptions'),
      chartApplyOptions: mk('chartApplyOptions'),
      scrollToRealTime: mk('scrollToRealTime'),
      fitContent: mk('fitContent'),
      roContainer: mk('roContainer'),
      roBox: mk('roBox'),
      hoverMutations: mk('hoverMutations'),
      frames: mk('frames'),
    };
    const arr = (k) => s[k].slice(cur.mark[k]);
    deltas[names[i]].containerChanges = arr('container').length - 1;
    deltas[names[i]].boxHChanges = arr('boxH').length - 1;
    deltas[names[i]].hoverHChanges = arr('hoverH').length - 1;
    deltas[names[i]].cardYChanges = arr('cardY').length - 1;
    deltas[names[i]].scrollHChanges = arr('scrollH').length - 1;
    deltas[names[i]].docWChanges = arr('docW').length - 1;
    deltas[names[i]].hoverHValues = [...new Set(arr('hoverH').map((x) => x.v))];
    deltas[names[i]].cardYValues = [...new Set(arr('cardY').map((x) => x.v))];
    deltas[names[i]].scrollHValues = [...new Set(arr('scrollH').map((x) => x.v))];
    deltas[names[i]].docWValues = [...new Set(arr('docW').map((x) => x.v))];
    deltas[names[i]].containerValues = [...new Set(arr('container').map((x) => x.v))];
  }
  return {
    deltas,
    totals: {
      frames: s.frames,
      rangeEvents: s.rangeEvents,
      setVisibleRange: s.setVisibleRange,
      setVisibleLogicalRange: s.setVisibleLogicalRange,
      tsApplyOptions: s.tsApplyOptions,
      chartApplyOptions: s.chartApplyOptions,
      roContainer: s.roContainer,
      roBox: s.roBox,
      hoverMutations: s.hoverMutations,
      legendMutations: s.legendMutations,
    },
  };
});

fs.writeFileSync(`${OUT}/${LABEL}_probe1.json`, JSON.stringify({ run: RUN, errors, ...dump }, null, 2));
console.log(JSON.stringify(dump.deltas, null, 2));
console.log('totals =', JSON.stringify(dump.totals));
if (errors.length) console.log('pageerrors =', errors.slice(0, 5));

await page.screenshot({ path: `${OUT}/${LABEL}_after.png` });
await browser.close();
