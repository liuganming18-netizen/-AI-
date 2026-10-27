/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 单策略工作台 · **资金分析视图**：策略资金曲线 vs 买入持有基准、回撤曲线、月度收益。
 *
 * 图形实现（2026-10-16 收敛）
 * ────────────────────────
 * 净值 / 基准 / 回撤**改用项目既有的 lightweight-charts**（官方 `LineSeries` / `AreaSeries`），
 * 时间轴缩放与平移走**官方原生**实现（滚轮缩放、拖动平移、`setVisibleRange` 定位）；
 * 不再自行实现一套交互坐标引擎（旧手写 SVG 视窗已移除）。
 *
 * 数据来源（全部真实，无合成）
 * ──────────────────────────
 * · 策略线：`GET /backtests/{id}/equity` → `items[].total_equity`（账本 equity 表）；
 * · 基准线：同响应的 `benchmark_equity[].equity`（**同成本**买入持有：手续费+滑点+真实资金费）；
 * · 回撤线：由**后台权益序列**按峰值口径展开，**初始峰值取 `/metrics.initial_equity`**；
 *   展开后的峰值必须与 `/metrics.max_drawdown_pct` 逐位一致，页面实时核对并给出徽标；
 * · 月度：`/metrics.monthly_returns.months[]`（账户月界净值口径，`partial_month` 明确区间收益），
 *   表格**复用旧热力图** `MonthlyReturnsHeatmap`。
 */

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  LineType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { AlertCircle, TrendingUp } from 'lucide-react';
import type { LabBenchmark, LabEquityPoint, LabMetrics, LabMonthlyReturns } from '../api/labBacktest';
import { MonthlyReturnsHeatmap } from './BacktestReviewView';
import { useThemeOptional } from '../theme/ThemeProvider';
import { readChartPalette, withAlpha } from '../design/chartTheme';
import { nearestByTime, type CrosshairBus } from '../utils/crosshairBus';
import { buildGridAnchor } from './LightweightChart';

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtPct = (v: number | null | undefined, digits = 4): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const fmtUtcDay = (ms: number): string => {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

interface MergedPoint {
  t: number;
  strat: number;
  bench: number | null;
  drawdownPct: number;
}

export interface LabFundingPanelProps {
  points: LabEquityPoint[] | null;
  benchmarkSeries: { t: number; equity: number }[] | null;
  benchmarkMeta: LabBenchmark | null;
  metrics: LabMetrics | null;
  monthly: LabMonthlyReturns | null;
  loading: boolean;
  error: string | null;
  /**
   * 没有净值序列时的说明文案。
   * 研究期登记的 run 是「按成交重建」，本来就没有引擎落盘的净值序列 ⇒
   * 传一句「未记录」比显示「取数错误」诚实，也不会让人以为数据坏了。
   */
  emptyHint?: string | null;
  /** 该月**平仓笔数**（按平仓时间，来自逐笔账本；仅用于复用旧热力图的补充事实列）。 */
  monthCloseCounts?: Record<string, number> | null;
  /** 所选 run 的真实窗口（UTC 毫秒）——用于判定每个自然月是否被区间截断。 */
  windowStartMs?: number | null;
  windowEndMs?: number | null;
  /**
   * 逐笔明细联动：选中某一笔时把面板切到「水下回撤」并把时间轴定位到该笔的 entry/exit 区间。
   * 只吃**真实**进出场时间戳（来自逐笔账本），不推断、不外推。
   */
  focusTrade?: { tradeId: string; entryMs: number; exitMs: number } | null;
  /**
   * 单图模式（设计稿把「净值曲线」「回撤曲线」拆成两张并排卡）：
   *   `equity`   → 只画策略净值 + 买入持有基准，标题「净值曲线」
   *   `drawdown` → 只画水下回撤，标题「回撤曲线」
   * 不传 = 旧行为（一张卡切页签），保证其它调用点零改动。
   */
  variant?: 'equity' | 'drawdown';
  /**
   * 跨图十字线总线：与 K 线 / 成交量 / 持仓量三张图共享。
   * 不传 ⇒ 行为完全不变（只在自身图内响应鼠标）。
   */
  crosshairBus?: CrosshairBus | null;
  /** 两图共享的真实时间点并集（UTC 秒）：本图补纯 whitespace 锚点补齐到它 */
  gridTimes?: number[];
}

/** 定位某笔时前后各留 1h 上下文（与执行视图 `PAD_MS` 同一口径）。 */
const FOCUS_PAD_MS = 3_600_000;

/** 某自然月（`YYYY-MM`）的 UTC 边界。 */
function monthBounds(ym: string): { startMs: number; endMs: number } | null {
  const [y, m] = ym.split('-').map(Number);
  if (!Number.isFinite(y) || !Number.isFinite(m) || m < 1 || m > 12) return null;
  const startMs = Date.UTC(y, m - 1, 1);
  const endMs = m === 12 ? Date.UTC(y + 1, 0, 1) : Date.UTC(y, m, 1);
  return { startMs, endMs };
}

export const LabFundingPanel: React.FC<LabFundingPanelProps> = ({
  points,
  benchmarkSeries,
  benchmarkMeta,
  metrics,
  monthly,
  loading,
  error,
  emptyHint = null,
  monthCloseCounts,
  windowStartMs = null,
  windowEndMs = null,
  focusTrade = null,
  variant,
  crosshairBus = null,
  gridTimes = null,
}) => {
  const [mode, setMode] = useState<'both' | 'underwater'>('both');
  /** 当前主题：图表画布必须跟着明暗走（lightweight-charts 的 layout 是 JS 选项，
   *  不吃 CSS 变量）⇒ 深色下不能留一块白底。
   *  颜色本身在 effect 里由 `readChartPalette()` 从 `--ds-chart-*` 读出；
   *  这里只把它当作「重建实例」的触发键。 */
  const theme = useThemeOptional()?.theme ?? 'light';
  /** 单图模式下的实际模式：variant 优先，其次才是页签状态。 */
  const effMode: 'both' | 'underwater' =
    variant === 'drawdown' ? 'underwater' : variant === 'equity' ? 'both' : mode;
  const [hover, setHover] = useState<MergedPoint | null>(null);
  const [viewLabel, setViewLabel] = useState<string | null>(null);
  /** 已定位的 trade_id（仅在联动生效后出现；未定位时为 null，不占位）。 */
  const [focusedTradeId, setFocusedTradeId] = useState<string | null>(null);
  const [crosshairTargetMs, setCrosshairTargetMs] = useState<number | null>(null);
  /** 待落实的定位请求：图表/数据尚未就绪时挂起，就绪后自动补做（避免静默失败）。 */
  const pendingFocusRef = useRef<{ tradeId: string; fromMs: number; toMs: number } | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const stratRef = useRef<ISeriesApi<'Line'> | null>(null);
  const benchRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ddRef = useRef<ISeriesApi<'Area'> | null>(null);
  const gridSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const byTimeRef = useRef<Map<number, MergedPoint>>(new Map());

  /* ── 跨图十字线联动 ────────────────────────────────────────────────────
   * 本面板是独立实例（自己 `createChart`），与 K 线区没有任何共享状态；
   * 通过总线按「时点」互通：别人悬停 ⇒ 本图把十字线落到最近的真实数据点。
   *
   * `busId` 用 `useId` 生成而不是写死 `lab-funding`：净值与回撤是**两个实例**，
   * 写死同一个 id 会让两者互相跳过对方的广播。 */
  const busId = `lab-funding-${useId()}`;
  /** 图表创建时的闭包要能读到最新总线，但不该因总线变化而重建实例 */
  const busRef = useRef<CrosshairBus | null>(crosshairBus);
  busRef.current = crosshairBus;
  const busIdRef = useRef(busId);
  busIdRef.current = busId;
  /** 静默期时间戳：接收外部程序化视窗写入时不反向回环广播 */
  const programmaticQuietUntilRef = useRef<number>(0);
  const targetViewportRef = useRef<{ fromMs: number; toMs: number } | null>(null);
  /** 用户手势交互防抖与防回环标记 */
  const isUserInteractingRef = useRef<boolean>(false);
  const userInteractingUntilRef = useRef<number>(0);

  /** 合并策略线 + 基准线（按真实时间戳对齐；基准缺失的时点保持 null，不插值）。 */
  const merged: MergedPoint[] = useMemo(() => {
    if (!points || points.length === 0) return [];
    const benchByT = new Map<number, number>();
    for (const b of benchmarkSeries ?? []) {
      if (Number.isFinite(b?.t) && Number.isFinite(b?.equity)) benchByT.set(b.t, b.equity);
    }
    const out: MergedPoint[] = [];
    // 初始峰值 = 后台 `/metrics.initial_equity`（真实初始权益），不是首点；缺失才退回首点。
    let peak: number | null =
      metrics?.initial_equity !== null && metrics?.initial_equity !== undefined && Number.isFinite(metrics.initial_equity)
        ? metrics.initial_equity
        : null;
    for (const p of points) {
      if (!Number.isFinite(p?.ts_ms) || !Number.isFinite(p?.total_equity)) continue;
      peak = peak === null ? p.total_equity : Math.max(peak, p.total_equity);
      const dd = peak && peak !== 0 ? ((peak - p.total_equity) / peak) * 100 : 0;
      out.push({
        t: p.ts_ms,
        strat: p.total_equity,
        bench: benchByT.has(p.ts_ms) ? (benchByT.get(p.ts_ms) as number) : null,
        drawdownPct: dd,
      });
    }
    return out;
  }, [points, benchmarkSeries, metrics?.initial_equity]);

  const hasBench = useMemo(() => merged.some((p) => p.bench !== null), [merged]);
  const benchComplete = useMemo(() => merged.length > 0 && merged.every((p) => p.bench !== null), [merged]);
  /** 有真实序列才渲染图表容器（无数据时走空态分支）。 */
  const hasData = merged.length > 0;

  /** 展开回撤曲线的峰值 vs 后台 `/metrics.max_drawdown_pct` —— 实时核对，不一致就说不一致。 */
  const ddCheck = useMemo(() => {
    if (merged.length === 0) return null;
    const seriesMax = Math.max(...merged.map((p) => p.drawdownPct));
    const backend = metrics?.max_drawdown_pct ?? null;
    if (backend === null) return { seriesMax, backend: null as number | null, match: null as boolean | null };
    return { seriesMax, backend, match: Math.abs(seriesMax - backend) < 1e-9 };
  }, [merged, metrics]);

  /** 末点 vs 汇总（最终权益）一致性。 */
  const tailCheck = useMemo(() => {
    if (merged.length === 0) return null;
    const last = merged[merged.length - 1];
    const stratOk =
      metrics?.final_equity === null || metrics?.final_equity === undefined
        ? null
        : Math.abs(last.strat - metrics.final_equity) < 1e-6;
    const benchOk =
      !benchComplete || benchmarkMeta?.final_equity === null || benchmarkMeta?.final_equity === undefined
        ? null
        : Math.abs((last.bench as number) - benchmarkMeta.final_equity) < 1e-6;
    return { last, stratOk, benchOk };
  }, [merged, metrics, benchmarkMeta, benchComplete]);

  /* ── 创建官方图表 ──
   * ⚠️ 依赖必须是 `hasData`，不能是 `[]`：无真实序列时组件走**空态分支**，图表容器
   * 不在 DOM 里（`containerRef.current === null`）。若写成 `[]`，首次挂载（数据未到）
   * 时容器不存在 ⇒ 直接 return ⇒ 数据到达后图表永远不会被创建（页面只剩一片空白）。
   * 用 `chartRef` 防重复创建；`hasData` 由 true→false 时清理并销毁实例。 */
  useEffect(() => {
    if (!hasData) return undefined;
    const container = containerRef.current;
    if (!container || chartRef.current) return undefined;

    /* 画布配色统一走 `--ds-chart-*` 令牌（由 chartTheme 读出）：
       网格是装饰（对底色 1.13–1.15:1），轴文字要能读（4.6–5.9:1），
       两者都不许落在 3–4:1 的「中间地带」。 */
    const palette = readChartPalette();
    const cBg = palette.background;
    const cText = palette.text;
    const cGridV = palette.gridVert;
    const cGridH = palette.gridHorz;
    const cAxis = palette.border;
    const cCross = palette.crosshair;

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: cBg },
        textColor: cText,
        // 轴文字 12px + 真实字体栈（canvas 不解析 CSS 变量）
        fontSize: 12,
        fontFamily: palette.font,
        attributionLogo: false,
      },
      grid: {
        // 净值曲线需要横线；竖线是纯噪声，一律关
        horzLines: { color: cGridH, style: LineStyle.Solid, visible: true },
        vertLines: { color: cGridV, style: LineStyle.Solid, visible: false },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: cCross,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: palette.crosshairLabel,
        },
        horzLine: {
          color: cCross,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: palette.crosshairLabel,
        },
      },
      rightPriceScale: {
        // 价格轴竖线是装饰，去掉后绘图区更宽
        borderVisible: false,
        borderColor: cAxis,
        minimumWidth: 78,
        // 顶/底角标不画被裁一半的文字
        entireTextOnly: true,
        // 刻度更稀一点
        tickMarkDensity: 2.8,
        // 曲线不贴边，末值标签不被裁
        scaleMargins: { top: 0.14, bottom: 0.06 },
      },
      /* `minBarSpacing` 默认 0.5px ⇒ 17376 个 15m 净值点在这块 ~510px 宽的面板里
         最多只能放下 ~1020 个 ⇒ `fitContent()` 被夹到「只剩最后约 10 天」，
         标着「净值对比」却看不到整段。这里是**线图**（不是 K 线柱），亚像素密度没问题，
         故把下限收到 0.01px，`fitContent()` 才能真的铺满所选 run 的完整窗口。
         ★ 这个值**必须保留**，删了整条曲线只剩末尾一小段。 */
      timeScale: {
        borderVisible: true,
        borderColor: cAxis,
        timeVisible: true,
        secondsVisible: false,
        // 最新一点右侧留 4 根空档：与主图 K 线保持完全一致，避免 fitContent 或两端视窗出现错位
        rightOffset: 4,
        // 不能拖到第一根左侧的空白；右侧保留呼吸
        fixLeftEdge: true,
        fixRightEdge: false,
        // 窗口尺寸变化时视窗不跳
        lockVisibleTimeRangeOnResize: true,
        minBarSpacing: 0.01,
      },
      handleScroll: true,
      handleScale: true,
    });

    const strat = chart.addSeries(LineSeries, {
      color: palette.up,
      lineWidth: 2,
      lineType: LineType.Simple,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '策略净值',
    });
    const bench = chart.addSeries(LineSeries, {
      color: palette.neutral,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lineType: LineType.Simple,
      priceLineVisible: false,
      /* 图上不再挂标签：价格轴右侧同时出现「策略净值 / 买入持有基准」两个色块会压住
         轴上的刻度数字。两条线在下方读数条里都有同色标记 + 末值，此处留白更干净。 */
      lastValueVisible: false,
      title: '',
    });
    // 回撤：负值 + 独立价格轴；`invertFilledArea` ⇒ 填充到 0 线一侧（经典水下图观感）
    const dd = chart.addSeries(AreaSeries, {
      priceScaleId: 'dd',
      lineColor: palette.down,
      topColor: withAlpha(palette.down, 0.22),
      bottomColor: withAlpha(palette.down, 0.02),
      invertFilledArea: true,
      lineWidth: 2,
      lineType: LineType.Simple,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '回撤 %',
    });
    chart.priceScale('dd').applyOptions({ visible: false, scaleMargins: { top: 0.08, bottom: 0.02 } });

    // 时间轴网格对齐锚点：不可见折线，用于注入与主图完全一致的 1h Whitespace 采样点，消除点集差异
    const gridSeries = chart.addSeries(LineSeries, {
      lineVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    stratRef.current = strat;
    benchRef.current = bench;
    ddRef.current = dd;
    gridSeriesRef.current = gridSeries;
    if (typeof window !== 'undefined') {
      if (variant === 'equity') {
        (window as any).__labEquityChart = chart;
      }
      (window as any).__getFundingTimeCoordinate = (timeSec: number) => {
        try {
          return chart.timeScale().timeToCoordinate(timeSec as Time);
        } catch {
          return null;
        }
      };
    }

    const handler = (param: MouseEventParams<Time>) => {
      const sec = typeof param.time === 'number' ? (param.time as number) : null;
      /* 先广播给其它图（总线统一用**毫秒**，与图表内部秒制在此处换算一次）。
         程序化 `setCrosshairPosition` 会让官方库再派发一次本回调，
         此时总线处于 emitting 状态，嵌套广播会被丢弃，不会回环。 */
      busRef.current?.emit(busIdRef.current, sec === null ? null : sec * 1000);
      if (sec === null) {
        setHover(null);
        setCrosshairTargetMs(null);
        return;
      }
      setCrosshairTargetMs(null);
      const p = byTimeRef.current.get(sec);
      setHover(p ?? null);
    };
    chart.subscribeCrosshairMove(handler);

    let reportTimer: number | null = null;
    const reportView = () => {
      const r = chart.timeScale().getVisibleRange();
      if (r && typeof r.from === 'number' && typeof r.to === 'number') {
        const fromMs = (r.from as number) * 1000;
        const toMs = (r.to as number) * 1000;
        setViewLabel(`${fmtUtcDay(fromMs)} → ${fmtUtcDay(toMs)}`);
        // 核心铁律：只有用户亲自在资金图上进行物理手势交互（拖拽、缩放）时，才允许向外广播给主图！
        // 彻底杜绝程序化写入、动画重算等非用户手势引起的反弹回环
        const isUserDriven = isUserInteractingRef.current || performance.now() < userInteractingUntilRef.current;
        if (!isUserDriven) return;
        if (performance.now() < programmaticQuietUntilRef.current) return;
        if (reportTimer !== null) window.clearTimeout(reportTimer);
        reportTimer = window.setTimeout(() => {
          reportTimer = null;
          const stillUserDriven = isUserInteractingRef.current || performance.now() < userInteractingUntilRef.current;
          if (!stillUserDriven) return;
          if (performance.now() < programmaticQuietUntilRef.current) return;
          busRef.current?.emitViewport(busIdRef.current, fromMs, toMs);
        }, 100);
      }
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(reportView);

    const handlePointerDown = () => {
      isUserInteractingRef.current = true;
      userInteractingUntilRef.current = performance.now() + 300;
    };
    const handlePointerUp = () => {
      userInteractingUntilRef.current = performance.now() + 200;
      setTimeout(() => {
        isUserInteractingRef.current = false;
      }, 200);
    };
    const handleWheel = (e: WheelEvent) => {
      // 必须是直接作用于资金图容器的滚轮才标记交互
      if (e.target && container.contains(e.target as Node)) {
        userInteractingUntilRef.current = performance.now() + 300;
      }
    };
    container.addEventListener('pointerdown', handlePointerDown, { passive: true });
    window.addEventListener('pointerup', handlePointerUp, { passive: true });
    container.addEventListener('wheel', handleWheel, { passive: true });

    return () => {
      container.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('pointerup', handlePointerUp);
      container.removeEventListener('wheel', handleWheel);
      if (reportTimer !== null) window.clearTimeout(reportTimer);
      chart.unsubscribeCrosshairMove(handler);
      chart.remove();
      chartRef.current = null;
      stratRef.current = null;
      benchRef.current = null;
      ddRef.current = null;
      gridSeriesRef.current = null;
    };
  }, [hasData, theme]);

  /* ── 落数据（真实序列；每次 run 切换整表替换后 fitContent）── */
  useEffect(() => {
    const strat = stratRef.current;
    const bench = benchRef.current;
    const dd = ddRef.current;
    const chart = chartRef.current;
    if (!strat || !bench || !dd || !chart) return;

    const byTime = new Map<number, MergedPoint>();
    const stratData: { time: UTCTimestamp; value: number }[] = [];
    const benchData: { time: UTCTimestamp; value: number }[] = [];
    const ddData: { time: UTCTimestamp; value: number }[] = [];
    for (const p of merged) {
      const sec = Math.floor(p.t / 1000) as UTCTimestamp;
      byTime.set(sec, p);
      stratData.push({ time: sec, value: p.strat });
      if (p.bench !== null) benchData.push({ time: sec, value: p.bench });
      ddData.push({ time: sec, value: -p.drawdownPct });
    }
    byTimeRef.current = byTime;

    strat.setData(stratData);
    bench.setData(benchData);
    dd.setData(ddData);

    const gridSeries = gridSeriesRef.current;
    if (gridSeries && gridTimes && gridTimes.length > 0) {
      const ownTimes = stratData.map((d) => Number(d.time));
      const gridData = buildGridAnchor(gridTimes, ownTimes);
      gridSeries.setData(gridData);
    }

    if (targetViewportRef.current && Number.isFinite(targetViewportRef.current.fromMs) && Number.isFinite(targetViewportRef.current.toMs)) {
      const from = Math.floor(targetViewportRef.current.fromMs / 1000);
      const to = Math.ceil(targetViewportRef.current.toMs / 1000);
      const ts = chart.timeScale();
      const fromIdx = ts.timeToIndex(from);
      const toIdx = ts.timeToIndex(to);
      if (typeof fromIdx === 'number' && typeof toIdx === 'number' && toIdx > fromIdx) {
        ts.setVisibleLogicalRange({ from: fromIdx, to: toIdx });
      } else {
        ts.setVisibleRange({ from: from as UTCTimestamp, to: to as UTCTimestamp });
      }
    } else if (stratData.length > 0) {
      chart.timeScale().fitContent();
    }
  }, [merged, gridTimes]);

  /* ── 补齐网格对齐锚点：单独监听 gridTimes 变化 ── */
  useEffect(() => {
    const gridSeries = gridSeriesRef.current;
    if (!gridSeries || !gridTimes || gridTimes.length === 0) return;
    const ownTimes = merged.map((p) => Math.floor(p.t / 1000));
    const gridData = buildGridAnchor(gridTimes, ownTimes);
    gridSeries.setData(gridData);
  }, [gridTimes, merged]);

  /* ── 模式切换（官方 visible + 价格轴可见性；不重建图表）── */
  useEffect(() => {
    const chart = chartRef.current;
    const strat = stratRef.current;
    const bench = benchRef.current;
    const dd = ddRef.current;
    if (!chart || !strat || !bench || !dd) return;
    const both = effMode === 'both';
    strat.applyOptions({ visible: both });
    bench.applyOptions({ visible: both && hasBench });
    dd.applyOptions({ visible: !both });
    chart.priceScale('right').applyOptions({ visible: both });
    chart.priceScale('dd').applyOptions({ visible: !both });
    // 依赖含 `hasData`：图表是在数据到达后才创建的，模式必须在其后重新应用一次，
    // 否则「净值对比」模式下回撤 Area 会沿用默认 visible=true 一起画出来。
  }, [effMode, hasBench, hasData]);

  /* ── 接收总线：别的图悬停 ⇒ 本图把十字线落到最近的真实数据点 ──
   * 净值的点密度远高于 K 线（1.7 万点 / 分钟级），容差给一天：
   * 即使 K 线是小时粒度、净值是分钟粒度，也不会错配到相差很远的位置。 */
  useEffect(() => {
    if (!crosshairBus) return undefined;
    return crosshairBus.register(busId, (timeMs) => {
      const chart = chartRef.current;
      if (!chart) return;
      if (timeMs === null) {
        setHover(null);
        setCrosshairTargetMs(null);
        try {
          chart.clearCrosshairPosition();
        } catch {
          /* 实例销毁竞态 */
        }
        return;
      }
      setCrosshairTargetMs(timeMs);
      const point = nearestByTime(merged, timeMs, (p) => p.t, 86_400_000);
      const series = effMode === 'underwater' ? ddRef.current : stratRef.current;
      if (!point || !series) {
        setHover(null);
        try {
          chart.clearCrosshairPosition();
        } catch {
          /* 同上 */
        }
        return;
      }
      /* 读数条必须在这里同步更新：官方库的 `setCrosshairPosition` 只画线，
         **不会**派发 crosshairMove 事件，指望图表回调来刷新读数会一直是末值。 */
      setHover(point);
      /* 回撤序列画的是负值（水下），定位时同样取负，否则十字线会跳到 0 轴上方 */
      const value = effMode === 'underwater' ? -point.drawdownPct : point.strat;
      try {
        // 十字线垂直虚线精确对齐广播的目标时点
        chart.setCrosshairPosition(value, Math.floor(timeMs / 1000) as UTCTimestamp, series);
      } catch {
        /* 时点落在视窗外或实例已销毁：静默跳过，不影响本图自身交互 */
      }
    });
  }, [crosshairBus, busId, merged, effMode]);

  /* ── 视窗联动：K 线区可见范围变化 ⇒ 本图跟到同一段真实时间 ──
   * 五张图的默认视窗并不相同（K 线只铺最近若干根，净值图铺满整个 run），
   * 不跟随的话十字线时点常年落在本图可见范围之外，联动等于失效。 */
  useEffect(() => {
    if (!crosshairBus) return undefined;
    return crosshairBus.registerViewport(busId, (fromMs, toMs) => {
      // 仅当用户正在本图进行手势物理交互（拖拽/滚轮）时，才跳过外部覆盖
      if (isUserInteractingRef.current || performance.now() < userInteractingUntilRef.current) {
        return;
      }
      const chart = chartRef.current;
      if (!chart) return;
      targetViewportRef.current = { fromMs, toMs };
      const from = Math.floor(fromMs / 1000);
      const to = Math.ceil(toMs / 1000);
      if (!(to > from)) return;
      try {
        // 静默 500ms：接收主图视窗广播落位期间不反向触发 reportView 回环
        programmaticQuietUntilRef.current = performance.now() + 500;
        const ts = chart.timeScale();
        const fromIdx = ts.timeToIndex(from);
        const toIdx = ts.timeToIndex(to);
        if (typeof fromIdx === 'number' && typeof toIdx === 'number' && toIdx > fromIdx) {
          ts.setVisibleLogicalRange({ from: fromIdx, to: toIdx });
        } else {
          chart.timeScale().setVisibleRange({
            from: from as UTCTimestamp,
            to: to as UTCTimestamp,
          });
        }
      } catch {
        /* 数据范围覆盖不到该窗口时官方库会抛错：保持原视窗即可 */
      }
    });
  }, [crosshairBus, busId]);

  /* ── 逐笔明细 → 本面板联动 ────────────────────────────────────────────────
   * 用户在「⑤ 逐笔明细」点中某一笔 ⇒ 这里做两件事：
   *   ① 视图切到「水下回撤」（那一笔的盈亏落在哪段回撤里，是这张图要回答的问题）；
   *   ② 时间轴定位到该笔的 entry/exit 区间（前后各留 1h）。
   * 只用**真实**进出场毫秒时间戳；区间会夹到本 run 权益序列的真实范围内，
   * 不越界、不外推。图表未就绪时挂起到 `pendingFocusRef`，就绪后补做。
   */
  const applyRangeMs = useCallback(
    (fromMs: number, toMs: number): boolean => {
      const chart = chartRef.current;
      if (!chart) return false;
      if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) return false;
      /* 优先用**索引区间**定位：直接落在真实权益点上，避免「时间 → 逻辑坐标」换算把区间放大。
         面板窄（约 590px），时间轴有最小 bar 间距，换算后的落点会比请求宽出一截。 */
      if (merged.length > 0) {
        let i0 = -1;
        let i1 = -1;
        for (let i = 0; i < merged.length; i++) {
          const t = merged[i].t;
          if (t >= fromMs && i0 < 0) i0 = i;
          if (t <= toMs) i1 = i;
        }
        if (i0 >= 0 && i1 > i0) {
          try {
            chart.timeScale().setVisibleLogicalRange({ from: i0 - 0.5, to: i1 + 0.5 });
            return true;
          } catch {
            /* 官方时间轴拒绝：落到下面的时间区间路径 */
          }
        }
      }
      const from = Math.floor(fromMs / 1000) as UTCTimestamp;
      const to = Math.floor(toMs / 1000) as UTCTimestamp;
      try {
        chart.timeScale().setVisibleRange({ from, to });
        return true;
      } catch {
        /* 官方时间轴拒绝该区间（越界/退化）：如实返回失败，由调用方决定是否重试 */
        return false;
      }
    },
    [merged],
  );

  useEffect(() => {
    if (!focusTrade) return;
    if (!Number.isFinite(focusTrade.entryMs) || !Number.isFinite(focusTrade.exitMs)) return;
    const first = merged.length > 0 ? merged[0].t : null;
    const last = merged.length > 0 ? merged[merged.length - 1].t : null;
    let fromMs = focusTrade.entryMs - FOCUS_PAD_MS;
    let toMs = focusTrade.exitMs + FOCUS_PAD_MS;
    if (first !== null && last !== null) {
      fromMs = Math.max(first, fromMs);
      toMs = Math.min(last, toMs);
    }
    setMode('underwater');
    setFocusedTradeId(focusTrade.tradeId);
    pendingFocusRef.current = { tradeId: focusTrade.tradeId, fromMs, toMs };
    // 立即尝试一次；失败则留待下方 effect 在图表/数据就绪后补做。
    if (applyRangeMs(fromMs, toMs)) pendingFocusRef.current = null;
  }, [focusTrade?.tradeId, focusTrade?.entryMs, focusTrade?.exitMs, merged, applyRangeMs]);

  /* 图表是数据到达后才创建的 ⇒ 挂起的定位请求要在这里补做，否则「点了没反应」。 */
  useEffect(() => {
    const p = pendingFocusRef.current;
    if (!p || !hasData) return;
    if (applyRangeMs(p.fromMs, p.toMs)) pendingFocusRef.current = null;
  }, [hasData, merged, applyRangeMs]);

  /* ── 快速区间：官方 setVisibleRange（原生时间轴）── */
  const applyQuickRange = useCallback(
    (bars: number | null) => {
      const chart = chartRef.current;
      if (!chart || merged.length === 0) return;
      setFocusedTradeId(null);
      pendingFocusRef.current = null;
      if (bars === null) {
        chart.timeScale().fitContent();
        return;
      }
      const last = merged[merged.length - 1].t;
      const first = merged[Math.max(0, merged.length - 1 - bars)].t;
      chart.timeScale().setVisibleRange({
        from: Math.floor(first / 1000) as UTCTimestamp,
        to: Math.floor(last / 1000) as UTCTimestamp,
      });
    },
    [merged],
  );

  /* ── 月度：把真实 `months[]` 适配成旧热力图入参（复用 `MonthlyReturnsHeatmap`）── */
  const heatmapData = useMemo(() => {
    const months = monthly?.months ?? [];
    if (months.length === 0) return null;
    const out: Record<string, Record<string, { pnl: number | null; pnl_usd: number | null; count: number; basis?: string; month_end_equity?: number | null }>> = {};
    for (const m of months) {
      const [yr, mm] = m.month.split('-');
      if (!yr || !mm) continue;
      out[yr] = out[yr] ?? {};
      const pnlUsd =
        m.month_end_equity !== null && m.basis_equity !== null ? m.month_end_equity - m.basis_equity : null;
      out[yr][mm] = {
        pnl: m.return_pct,
        pnl_usd: pnlUsd,
        count: monthCloseCounts?.[m.month] ?? 0,
        basis: m.basis_note ?? undefined,
        month_end_equity: m.month_end_equity,
      };
    }
    return out;
  }, [monthly, monthCloseCounts]);

  /**
   * 月度收益是**乘性**序列 ⇒ 区间累计必须**复合**，不能相加。
   * 这里复合各月收益并与 `/metrics.net_return_pct`（同区间真实收益）实时核对。
   */
  const compoundCheck = useMemo(() => {
    const ms = monthly?.months ?? [];
    if (ms.length === 0) return null;
    let prod = 1;
    let any = false;
    for (const m of ms) {
      if (m.return_pct === null || m.return_pct === undefined || !Number.isFinite(m.return_pct)) continue;
      prod *= 1 + m.return_pct / 100;
      any = true;
    }
    if (!any) return null;
    const compoundPct = (prod - 1) * 100;
    const real = metrics?.net_return_pct ?? null;
    return {
      compoundPct,
      real,
      match: real === null ? (null as boolean | null) : Math.abs(compoundPct - real) < 1e-9,
    };
  }, [monthly, metrics]);

  /**
   * 每个月的**真实完整性**：由所选 run 的窗口与自然月边界推出（不是只看后台 `partial_month`）。
   * 30 天窗口常常「首月被起点截断、末月被终点截断」，末月不能被标成完整月。
   */
  const monthRows = useMemo(() => {
    const ms = monthly?.months ?? [];
    return ms.map((m) => {
      const b = monthBounds(m.month);
      const effStart = b && windowStartMs !== null ? Math.max(b.startMs, windowStartMs) : b?.startMs ?? null;
      const effEnd = b && windowEndMs !== null ? Math.min(b.endMs, windowEndMs) : b?.endMs ?? null;
      const cutStart = !!(b && windowStartMs !== null && windowStartMs > b.startMs);
      const cutEnd = !!(b && windowEndMs !== null && windowEndMs < b.endMs);
      return {
        row: m,
        effStart,
        effEnd,
        cutStart,
        cutEnd,
        isFull: !!b && !cutStart && !cutEnd,
      };
    });
  }, [monthly, windowStartMs, windowEndMs]);

  const cur = hover ?? (merged.length > 0 ? merged[merged.length - 1] : null);
  const eqBase = metrics?.initial_equity ?? (merged.length > 0 ? merged[0].strat : null);
  const stratRet = cur && eqBase ? ((cur.strat - eqBase) / eqBase) * 100 : null;
  const benchRet = cur && eqBase && cur.bench !== null ? ((cur.bench - eqBase) / eqBase) * 100 : null;

  if (merged.length === 0) {
    return (
      <div
        className="bg-white border border-neutral-200 rounded-lg shadow-sm p-6 flex flex-col items-center justify-center gap-1 text-xs text-neutral-500"
        data-testid="lab-funding-panel"
      >
        <AlertCircle className="w-4 h-4 text-neutral-400" />
        <span>
          {loading
            ? '正在读取真实净值曲线…'
            : (emptyHint ?? '暂无真实净值曲线（后台未返回 equity / benchmark）')}
        </span>
        {error && !emptyHint && <span className="text-rose-600">取数错误：{error}</span>}
        <span className="text-[10px] text-neutral-400">不绘制任何合成 / 兜底曲线</span>
      </div>
    );
  }

  return (
    <div className="ds-card" data-testid="lab-funding-panel">
      <div className="ds-card__head">
        <span
          className="ds-card__title"
          title="已实现净值曲线：按逐笔平仓净额与初始资金累加展开，不含持仓期间的未实现浮盈浮亏；基准为同期买入持有"
        >
          {variant === 'drawdown' ? '回撤曲线' : variant === 'equity' ? '已实现净值曲线（平仓结算口径，不含在途浮盈浮亏）' : '净值 · 回撤'}
        </span>
        <div className="ds-card__hint" style={{ flex: '1 1 auto' }}>
          {variant === 'drawdown'
            ? '从最高点回落'
            : variant === 'equity'
              ? '绿：策略已实现净值（逐笔平仓） 灰虚线：买入持有基准'
              : `${merged.length} 点`}
        </div>
          {/* 状态类**只报异常**：一致时不再常驻「✓」徽章（口径原文在 title） */}
          {ddCheck && ddCheck.match !== true && (
            <span
              className={`px-1.5 py-0.5 rounded border text-[10px] font-mono ${
                ddCheck.match === false
                  ? 'bg-rose-50 text-rose-700 border-rose-200'
                  : 'bg-neutral-100 text-neutral-700 border-neutral-200'
              }`}
              title={`回撤核对：曲线由后台权益序列展开（初始峰值取 /metrics.initial_equity），峰值与 /metrics.max_drawdown_pct 实时核对 —— ${
                ddCheck.match === false
                  ? `不一致（曲线 ${ddCheck.seriesMax.toFixed(6)}% vs 后台 ${fmtNum(ddCheck.backend, 6)}%）`
                  : '后台未给 max_drawdown_pct'
              }`}
            >
              {ddCheck.match === false ? '回撤 ✗ 不一致' : '回撤 无后台值'}
            </span>
          )}
          {tailCheck && !(tailCheck.stratOk === true && (tailCheck.benchOk === null || tailCheck.benchOk === true)) && (
            <span
              className="px-1.5 py-0.5 rounded border text-[10px] font-mono bg-rose-50 text-rose-700 border-rose-200"
              title="末点核对：曲线末点必须等于 /metrics 的汇总值"
            >
              末点 {tailCheck.stratOk === true ? '✓' : '✗ 策略≠汇总'}
              {tailCheck.benchOk === null ? '' : tailCheck.benchOk ? '' : ' · 基准≠汇总'}
            </span>
          )}
        <div className="flex items-center gap-2 shrink-0">
          {!variant && (
          <div className="ds-tfgroup" role="group" aria-label="视图">
            <button
              type="button"
              onClick={() => setMode('both')}
              className={`ds-tfgroup__item${mode === 'both' ? ' is-on' : ''}`}
            >
              净值对比
            </button>
            <button
              type="button"
              onClick={() => setMode('underwater')}
              className={`ds-tfgroup__item${mode === 'underwater' ? ' is-on' : ''}`}
            >
              水下回撤
            </button>
          </div>
          )}
          {/* 联动定位徽章：只有真的从「单条记录回放」定位过某一笔才出现。
              只在回撤卡显示（水下回撤就是这张图）；净值卡不重复一遍。 */}
          {variant !== 'equity' && focusedTradeId && (
            <button
              onClick={() => applyQuickRange(null)}
              className="px-1.5 py-0.5 rounded border border-[var(--ds-accent)] bg-[var(--ds-accent-50)] text-[10px] font-mono text-[var(--ds-accent-text)] hover:brightness-95 whitespace-nowrap"
              title="已按逐笔明细选中的那一笔定位时间轴；点击恢复全区间"
              data-testid="lab-funding-focus"
            >
              已定位 {focusedTradeId.length > 16 ? `${focusedTradeId.slice(0, 8)}…${focusedTradeId.slice(-4)}` : focusedTradeId} ✕
            </button>
          )}
          {!variant && (
          <div className="ds-tfgroup" role="group" aria-label="时间范围">
            <button type="button" onClick={() => applyQuickRange(null)} className="ds-tfgroup__item">
              全部
            </button>
            <button
              type="button"
              onClick={() => applyQuickRange(96)}
              className="ds-tfgroup__item"
              title="最近 96 根 15m（约 1 天）"
            >
              近 1 天
            </button>
            <button
              type="button"
              onClick={() => applyQuickRange(288)}
              className="ds-tfgroup__item"
              title="最近 288 根 15m（约 3 天）"
            >
              近 3 天
            </button>
          </div>
          )}
        </div>
      </div>

      {/* 读数条：单图模式只留这张图自己的值（净值卡不报回撤、回撤卡不报净值），
          否则两行挤成三行，设计稿里这两张卡本来只有标题 + 曲线。 */}
      <div className="ds-hoverbar">
        <span className="text-neutral-500">
          {(() => {
            if (!cur) return '时刻 — UTC';
            if (crosshairTargetMs !== null) {
              const diffMs = Math.abs(cur.t - crosshairTargetMs);
              if (diffMs === 0) {
                return `时刻 ${fmtUtcDay(crosshairTargetMs)} UTC`;
              }
              if (diffMs <= 3_600_000) {
                return `时刻 ${fmtUtcDay(crosshairTargetMs)} UTC（采样 ${fmtUtcDay(cur.t)}）`;
              }
              return `时刻 ${fmtUtcDay(crosshairTargetMs)} UTC（无精确采样，最近采样 ${fmtUtcDay(cur.t)}）`;
            }
            return `时刻 ${fmtUtcDay(cur.t)} UTC`;
          })()}
        </span>
        {variant !== 'drawdown' && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />
            <span className="text-neutral-400">策略净值</span>
            <b className="text-neutral-100">{cur ? `$${fmtNum(cur.strat, 2)}` : '—'}</b>
            <span className={stratRet !== null && stratRet >= 0 ? 'text-emerald-600' : 'text-rose-600'}>({fmtPct(stratRet, 2)})</span>
          </span>
        )}
        {!variant && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-0.5 bg-neutral-400 inline-block" />
            <span className="text-neutral-500">买入持有基准</span>
            {cur?.bench === null || cur === null ? (
              <span className="text-neutral-400">该时点无基准数据</span>
            ) : (
              <>
                <span className="text-neutral-300">${fmtNum(cur.bench, 2)}</span>
                <span className={benchRet !== null && benchRet >= 0 ? 'text-neutral-400' : 'text-rose-500'}>({fmtPct(benchRet, 2)})</span>
              </>
            )}
          </span>
        )}
        {variant !== 'equity' && (
          <span className="flex items-center gap-1">
            <span className="text-neutral-500">水下回撤</span>
            <b className="text-rose-600">-{cur ? cur.drawdownPct.toFixed(3) : '—'}%</b>
          </span>
        )}
        {/* 联动定位时补一行「该笔区间」：面板窄、时间轴有最小 bar 间距，
            视图能放到的宽度有物理上限 ⇒ 精确区间在这里给出，不依赖图形缩放精度。
            只在回撤卡显示（两张卡各来一遍就是噪音）。 */}
        {variant !== 'equity' && focusedTradeId && focusTrade && (
          <span className="flex items-center gap-1 text-[var(--ds-accent-text)]">
            <span>该笔区间</span>
            <b>
              {fmtUtcDay(focusTrade.entryMs)} → {fmtUtcDay(focusTrade.exitMs)} UTC
            </b>
          </span>
        )}
      </div>

      <div
        ref={containerRef}
        className="w-full h-[195px]"
        style={{ background: 'var(--ds-n-900)' }}
        data-testid="lab-funding-chart"
      />

      {/* 口径 / 一致性说明：**异常默认可见，明细默认收起**（渐进披露）。
          单图模式下这两块不渲染 —— 设计稿的「净值曲线」「回撤曲线」只有标题 + 曲线。 */}
      {!variant && (
      <div className="px-3 py-1.5 border-t border-neutral-200 bg-neutral-50 text-[10px] text-neutral-400 space-y-0.5">
        {!hasBench && <div className="text-amber-700">后台未返回基准序列 ⇒ 只画策略线，不合成基准。</div>}
        {hasBench && !benchComplete && (
          <div className="text-amber-700">
            基准序列存在缺时点 ⇒ <b>不插值、不连线</b>，缺时点处基准为空。
          </div>
        )}
        <details className="ds-details" data-testid="lab-funding-basis-details">
          <summary>口径与一致性核对（基准 / 末点 / 回撤展开）</summary>
          <div className="ds-details__body space-y-0.5">
            <div>
              基准：{benchmarkMeta?.benchmark_kind ?? '—'} · 净收益 {fmtPct(benchmarkMeta?.net_return_pct, 4)} · 最终权益 $
              {fmtNum(benchmarkMeta?.final_equity, 2)} · 最大回撤 -{fmtNum(benchmarkMeta?.max_drawdown_pct, 4)}%
              {benchmarkMeta?.cost_model ? ` · 成本口径：${benchmarkMeta.cost_model}` : ''}
            </div>
            <div>
              策略末点 ${fmtNum(tailCheck?.last.strat, 2)}（/metrics.final_equity ${fmtNum(metrics?.final_equity, 2)}）· 回撤曲线峰值 -
              {fmtNum(ddCheck?.seriesMax, 6)}%（/metrics.max_drawdown_pct -{fmtNum(ddCheck?.backend, 6)}%）
            </div>
            <div className="text-neutral-400">
              回撤曲线由<b>后台权益序列</b>按峰值口径展开（初始峰值 = /metrics.initial_equity；不重算交易/止损）；
              图形与缩放/平移走项目既有 lightweight-charts 官方实现。颜色沿用本项目既有约定：盈/正为绿、亏/负为红。
            </div>
          </div>
        </details>
      </div>
      )}

      {/* 月度收益：复用旧热力图（**复合年累计口径**）+ 后台完整性/基准说明。
          一屏布局下默认收起，点开才是完整热力图与逐月明细。 */}
      {!variant && (
      <div className="px-3 py-1.5 border-t border-neutral-200">
        <details className="ds-details" data-testid="lab-monthly-panel">
          <summary>月度收益热力图与逐月完整性核对（{monthRows.length} 个月）</summary>
          <div className="ds-details__body space-y-2">
        {compoundCheck && (
          <div
            className={`rounded px-2 py-1 border text-[10px] font-mono flex flex-wrap items-center gap-2 ${
              compoundCheck.match === true
                ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                : compoundCheck.match === false
                  ? 'bg-rose-50 border-rose-200 text-rose-700'
                  : 'bg-neutral-50 border-neutral-200 text-neutral-400'
            }`}
            data-testid="lab-period-return"
          >
            <span className="font-bold">
              同区间累计（月度复合 Π(1+r)−1）：{fmtPct(compoundCheck.compoundPct, 6)}
            </span>
            <span>/metrics.net_return_pct：{fmtPct(compoundCheck.real, 6)}</span>
            <span>
              {compoundCheck.match === true
                ? '一致（月度收益是乘性序列，不能相加）'
                : compoundCheck.match === false
                  ? '不一致（见下）'
                  : '后台未给 net_return_pct'}
            </span>
            {compoundCheck.match === false && (
              <span className="text-neutral-400">
                差额 {fmtNum((compoundCheck.compoundPct - (compoundCheck.real as number)), 6)}%
                （月界切点与窗口端点不完全重合时会出现；以 /metrics 同区间真实收益为准）
              </span>
            )}
          </div>
        )}

        <MonthlyReturnsHeatmap
          monthly={heatmapData}
          ytdBasis="compound"
          basisNoteOverride="月度% = 该月**月界净值 / 上月月界净值 − 1**（后台月界口径，同一账户净值）；首月以**窗口起点初始权益**为基准并标 partial_month；YTD = 各月**复合** Π(1+r)−1（月界净值为乘性序列，相加会得出错误的年累计）。"
        />

        <details className="ds-details" data-testid="lab-monthly-details">
          <summary>月度明细与完整性核对（{monthRows.length} 个月）</summary>
          <div className="ds-details__body text-[10px] text-neutral-500 font-mono space-y-1">
            <div>来源 `/metrics.monthly_returns.months`：{monthly?.note ?? '—'}</div>
            {monthRows.map(({ row: m, effStart, effEnd, cutStart, cutEnd, isFull }) => (
              <div key={m.month} className="flex flex-wrap items-center gap-2">
                <span className="font-bold text-neutral-300">{m.month}</span>
                <span>区间收益 {fmtPct(m.return_pct, 6)}</span>
                <span
                  className={`px-1.5 py-0.5 rounded border ${
                    isFull ? 'bg-neutral-100 text-neutral-700 border-neutral-200' : 'bg-amber-50 text-amber-800 border-amber-200'
                  }`}
                >
                  {isFull
                    ? '完整自然月'
                    : `部分月：${cutStart ? '起点在月中（区间从 ' + fmtUtcDay(windowStartMs as number) + ' 开始）' : ''}${cutStart && cutEnd ? '；' : ''}${cutEnd ? '终点在月中（区间到 ' + fmtUtcDay(windowEndMs as number) + ' 结束）' : ''}`}
                </span>
                {effStart !== null && effEnd !== null && (
                  <span className="text-neutral-500">
                    实际覆盖 {fmtUtcDay(effStart)} → {fmtUtcDay(effEnd)} UTC
                  </span>
                )}
                <span className="text-neutral-400">后台 partial_month={String(m.partial_month)}</span>
                <span className="text-neutral-500">{m.basis_note ?? '—'}</span>
              </div>
            ))}
            {monthCloseCounts && Object.keys(monthCloseCounts).length > 0 && (
              <div className="text-neutral-400">
                热力图「笔数」= 该月<b>平仓笔数</b>（按平仓时间，来自逐笔账本）；净值变化额 = 期末净值 − 基准净值（均为后台字段）。
              </div>
            )}
            {monthRows.length > 0 && (
              <div className="text-neutral-400">
                完整性由所选 run 的<b>真实窗口</b>与自然月边界推出（不只信后台 partial_month）：30 天窗口通常首月被起点、末月被终点各截一次，末月不得标成完整月。
              </div>
            )}
          </div>
        </details>
          </div>
        </details>
      </div>
      )}
    </div>
  );
};

export default LabFundingPanel;
