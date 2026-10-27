/**
 * 图表主题调色板
 * ----------------------------------------------------------------------------
 * lightweight-charts 只吃 JS 字面量颜色，读不到 CSS 变量。本文件是**唯一**的桥：
 * 把 tokens.css 里的 `--ds-chart-*` 读出来，转成图表能用的调色板对象。
 *
 * 三条纪律：
 *   1. 新增图表颜色先加 `--ds-chart-*` 令牌，再在这里加字段，不许在组件里写字面量。
 *   2. `LIGHT_PALETTE` / `DARK_PALETTE` 只是**兜底**（读不到 CSS 变量时用，
 *      例如单测环境），正常运行时以 CSS 变量为准。
 *   3. 主题变化必须触发 `useChartPalette` 重算 —— 它订阅了 ThemeProvider 的
 *      theme，因此 setTheme 之后图表会拿到新调色板并 applyOptions 热切换。
 */

import { useMemo } from 'react';
import { useThemeOptional } from '../theme/ThemeProvider';

export interface ChartPalette {
  /** 涨（阳线 / 盈利） */
  up: string;
  /** 跌（阴线 / 亏损） */
  down: string;
  /** 真实入场标记（买入 / 开仓）：独立鲜亮天蓝色，不混同阳线绿 */
  entry: string;
  /** 真实平仓标记（卖出 / 止盈止损）：独立鲜艳暖金橙，不混同阴线红 */
  exit: string;
  /** 未收盘临时层 */
  forming: string;
  /** 中性灰（当前无引用，保留语义位） */
  neutral: string;
  /** 信号触发标记（候选方块） */
  signalMarker: string;
  /** 委托圆点（订单 phase 着色）：与候选方块分开一档，避免同色叠在一起 */
  order: string;
  /** 确认圆点（FT-REPLAY-01）：信号→确认→成交 三段在图上各自一色，不许同色叠 */
  confirmMarker: string;
  gridHorz: string;
  gridVert: string;
  text: string;
  border: string;
  /** OI 柱形（统一单色，不承载方向语义） */
  oiBar: string;
  background: string;
  crosshair: string;
  crosshairLabel: string;
  /** 基准曲线（收益曲线对比用） */
  benchmark: string;
  /** 快线（MA20） */
  maFast: string;
  /** 慢线（MA60） */
  maSlow: string;
  /**
   * 图表文字字体栈。
   *
   * `layout.fontFamily` 是图表配置里**唯一必须由 CSS 侧提供**的字符串值：
   * canvas 不解析 CSS 变量，所以这里读的是 `--ds-font` 的**字面量**声明文本。
   * ⚠ 因此 tokens.css 里的 `--ds-font` 必须保持字面量写法，不能嵌套成
   *   `var(--ds-font-base)` —— 那样读到的会是字符串 "var(--x)"，
   *   canvas 直接回退成默认衬线体，轴上的中文（万 / 亿）会突然变字形。
   */
  font: string;
}

/** 与 tokens.css 的 `--ds-font` **逐字一致**（读不到 CSS 变量时的兜底）。 */
const FONT_STACK =
  '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif';

/** 浅色兜底：与改造前 `COLOR` 常量逐值一致，保证浅色零回归。 */
export const LIGHT_PALETTE: ChartPalette = {
  up: '#0b8a5e',
  down: '#d92d48',
  entry: '#0284c7',
  exit: '#ea580c',
  forming: '#ad6800',
  neutral: '#8491a5',
  signalMarker: '#4f5bd5',
  order: '#5b3fa8',
  confirmMarker: '#8a5200',
  gridHorz: '#e7ecf3',
  gridVert: '#eef2f8',
  text: '#586880',
  border: '#d4dbe6',
  oiBar: '#0b8a5e',
  background: '#ffffff',
  crosshair: '#94a3b8',
  crosshairLabel: '#334155',
  benchmark: '#eab308',
  maFast: '#2f6bff',
  maSlow: '#c17600',
  font: FONT_STACK,
};

/** 深色兜底：与 tokens.css `:root` 的 `--ds-chart-*` 同值。 */
export const DARK_PALETTE: ChartPalette = {
  up: '#3dd68c',
  down: '#f2707a',
  entry: '#38bdf8',
  exit: '#fb923c',
  forming: '#e8b54a',
  neutral: '#8c939e',
  signalMarker: '#7ea6ff',
  order: '#b39dff',
  confirmMarker: '#e0a33a',
  gridHorz: '#1d222a',
  gridVert: '#191e25',
  text: '#8c939e',
  border: '#262c35',
  oiBar: '#2fbe83',
  background: '#12151a',
  crosshair: '#4e5764',
  crosshairLabel: '#4a5462',
  benchmark: '#d9a520',
  maFast: '#6f9dff',
  maSlow: '#d9a520',
  font: FONT_STACK,
};

const VAR_MAP: Record<keyof ChartPalette, string> = {
  up: '--ds-chart-up',
  down: '--ds-chart-down',
  entry: '--ds-chart-entry',
  exit: '--ds-chart-exit',
  forming: '--ds-chart-forming',
  neutral: '--ds-chart-text',
  signalMarker: '--ds-chart-signal',
  confirmMarker: '--ds-chart-confirm',
  order: '--ds-chart-order',
  gridHorz: '--ds-chart-grid',
  gridVert: '--ds-chart-grid-v',
  text: '--ds-chart-text',
  border: '--ds-chart-border',
  oiBar: '--ds-chart-oi',
  background: '--ds-chart-bg',
  crosshair: '--ds-chart-crosshair',
  crosshairLabel: '--ds-chart-crosshair-label',
  benchmark: '--ds-chart-benchmark',
  maFast: '--ds-chart-ma-fast',
  maSlow: '--ds-chart-ma-slow',
  // 注意读的是 --ds-font（不是 --ds-chart-*）：字体栈是全局令牌
  font: '--ds-font',
};

/**
 * hex → rgba。成交量柱需要「同色但半透明」，而 lightweight-charts 只认具体色值。
 * 非 hex 输入（rgb() / var() / color-mix()）原样返回，交给浏览器解析 —— 不猜。
 */
export function withAlpha(color: string, alpha: number): string {
  const c = (color || '').trim();
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
  if (!m) return c;
  let hex = m[1];
  if (hex.length === 3) {
    hex = hex
      .split('')
      .map((x) => x + x)
      .join('');
  }
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 从 `:root` 读一次全部图表令牌；缺哪个就用兜底里对应的值。 */export function readChartPalette(): ChartPalette {
  const fallback =
    typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
      ? LIGHT_PALETTE
      : DARK_PALETTE;
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback;

  let computed: CSSStyleDeclaration;
  try {
    computed = getComputedStyle(document.documentElement);
  } catch {
    return fallback;
  }

  const out = { ...fallback };
  (Object.keys(VAR_MAP) as (keyof ChartPalette)[]).forEach((key) => {
    const raw = computed.getPropertyValue(VAR_MAP[key]).trim();
    if (raw) out[key] = raw;
  });
  return out;
}

/**
 * 订阅主题的调色板。theme 变化 ⇒ 重新读变量 ⇒ 依赖它的图表 effect 重新 applyOptions。
 * 在 ThemeProvider 之外使用时退回 `readChartPalette()`（按 DOM 属性判断），不抛错。
 */
export function useChartPalette(): ChartPalette {
  const theme = useThemeOptional()?.theme ?? null;
  return useMemo(() => readChartPalette(), [theme]);
}
