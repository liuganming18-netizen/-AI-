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
  /** 未收盘临时层 */
  forming: string;
  /** 中性灰（当前无引用，保留语义位） */
  neutral: string;
  /** 信号触发标记 */
  signalMarker: string;
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
}

/** 浅色兜底：与改造前 `COLOR` 常量逐值一致，保证浅色零回归。 */
export const LIGHT_PALETTE: ChartPalette = {
  up: '#0b8a5e',
  down: '#d92d48',
  forming: '#ad6800',
  neutral: '#8491a5',
  signalMarker: '#4f5bd5',
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
};

/** 深色兜底：与 tokens.css `:root` 的 `--ds-chart-*` 同值。 */
export const DARK_PALETTE: ChartPalette = {
  up: '#22c55e',
  down: '#ef4444',
  forming: '#f59e0b',
  neutral: '#6f7d94',
  signalMarker: '#6c8cff',
  gridHorz: '#1a2438',
  gridVert: '#131d31',
  text: '#6f7d94',
  border: '#24314a',
  oiBar: '#22c55e',
  background: '#0f1728',
  crosshair: '#64748b',
  crosshairLabel: '#334155',
  benchmark: '#eab308',
  maFast: '#4f8dff',
  maSlow: '#eab308',
};

const VAR_MAP: Record<keyof ChartPalette, string> = {
  up: '--ds-chart-up',
  down: '--ds-chart-down',
  forming: '--ds-chart-forming',
  neutral: '--ds-chart-text',
  signalMarker: '--ds-chart-signal',
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
