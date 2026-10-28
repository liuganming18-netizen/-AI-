/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * TradingView Lightweight Charts™ 官方 5.x 实现的**薄封装**。
 *
 * 定位：只做「容器生命周期 + 真实数据映射 + 视窗保持 + 事件转发」，
 * 蜡烛绘制、价格轴/时间轴、十字线、滚轮缩放、拖动平移**全部**由官方库原生提供，
 * 本文件不重写任何图表引擎逻辑。
 *
 * 官方文档：
 *  - https://tradingview.github.io/lightweight-charts/docs
 *  - https://tradingview.github.io/lightweight-charts/docs/panes
 *  - https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5
 *
 * 数据纪律（与项目既有约定一致）：
 *  - 时间戳毫秒 → 官方要求的 **UTC 秒**（`UTCTimestamp`），排序 + 去重；
 *  - 缺任一 OHLC / 成交量 / OI 值 ⇒ 该点**留空**，**绝不补 0、不合成、不插值**；
 *  - 未收盘临时层只作**单根独立配色 + 明确「未收盘」文字**临时展示，
 *    不并入闭合历史序列（闭合序列由调用方 `candles` 单独维护，缺口/指标统计只认它）。
 *
 * 署名：`layout.attributionLogo = false`（2026-10-12 按用户要求关闭图内 TradingView 图标）；
 * 署名合规由页面渲染的 `LIGHTWEIGHT_CHARTS_ATTRIBUTION` **可见文本链接**承担
 * （指向 tradingview.com，满足库 README 的链接要求）。
 *
 * ---------------------------------------------------------------------------
 * 四处关键实现依据（均读官方 5.2.1 源码 / 类型定义核实，不靠猜测）
 * ---------------------------------------------------------------------------
 * 1) `TimeScale._internal_logicalRangeForTimeRange()` → `_internal_timeToIndex()`：
 *    仅当该图时间轴 `points.length < 1`（**完全没有时间点**）时返回 `null`，
 *    再经 `ensureNotNull` 抛 `Value is null`；范围落在数据域**之外**不会抛错，
 *    而是被夹到最近的时间点。⇒ 安全判据是「该图有 ≥1 个时间点」，不是「范围必须落在域内」。
 * 2) `DataLayer._internal_setSeriesData()` 对**每一条**数据（含 whitespace）都建
 *    `pointDataByTimePoint` 并 `isTimeScaleAffected = true` ⇒ **whitespace 点会进入时间轴点集**；
 *    而 `LinePaneViewBase` 只遍历有效点、`walkLine` 对相邻有效点持续 `lineTo`
 *    ⇒ **whitespace 不能断线**，断线必须用多条 `LineSeries` 分段。
 * 3) `TimeScale._private__setVisibleRange()` 仅在逻辑范围**确实变化**时才
 *    `logicalRangeChanged._internal_fire()` ⇒ 按真实时间同步不会形成回环。
 * 4) 官方时间轴是**离散索引**（`barSpacing` 均匀、与时间点集密度无关）：
 *    两图若时间点集不同，即使可见 UTC 窗口一致，同一时间点也会落在不同 x 上。
 *    故两图必须使用**同一个真实时间点并集**（各自补纯 whitespace 锚点，真实值不变），
 *    并且价格轴宽度必须一致（`PriceScaleOptions.minimumWidth`，官方文档即为此场景）。
 */

import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type ChartOptions,
  type DeepPartial,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type IPriceScaleApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type Logical,
  type LogicalRange,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from 'lightweight-charts';
import type { Candle, OIPoint, Timeframe } from '../types/crypto';
import { formatWanYi } from '../utils/formatters';
import {
  LIGHT_PALETTE,
  useChartPalette,
  withAlpha,
  type ChartPalette,
} from '../design/chartTheme';

/** TradingView Lightweight Charts™ 署名（Apache-2.0）。 */
export const LIGHTWEIGHT_CHARTS_ATTRIBUTION = {
  text: 'Charts by TradingView Lightweight Charts™',
  url: 'https://www.tradingview.com/lightweight-charts/',
  license: 'Apache-2.0',
} as const;

export const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  '5m': 300,
  '15m': 900,
  '30m': 1_800,
  '1h': 3_600,
  '4h': 14_400,
  '8h': 28_800,
  '12h': 43_200,
  '1d': 86_400,
};

/**
 * 图表调色板（2026-10-17 双主题改造）
 * ----------------------------------------------------------------------------
 * 颜色不再写在本文件里：全部来自 `--ds-chart-*` 令牌，由 `design/chartTheme.ts`
 * 读出后经 `useChartPalette()` 注入。主题切换时该 hook 会重算，组件用
 * `applyOptions` 热切换 —— **不重建图表实例**，缩放位置、十字线、已加载数据全部保留。
 *
 * `LIGHT_PALETTE` 只作为「读不到 CSS 变量」时的兜底（单测 / 无 DOM 环境），
 * 逐值与改造前一致，因此浅色主题零回归。
 *
 * 口径未变：涨绿跌红（阳线绿 / 阴线红）；未收盘临时层用琥珀色单根标识；
 * OI 柱形统一单色（见 `buildOiBars`），颜色不承载方向或增减语义。
 */

/**
 * OI 柱形的单一柱色，供面板标题前的图例色块复用（避免图例与柱色对不上）。
 * 语义见上方 `COLOR.oiBar`：柱色不承载方向 / 增减。
 */
export const OI_BAR_COLOR = 'var(--ds-chart-oi)';

/** 价格轴起始最小宽度：最终由 `alignPriceScaleWidths` 按两图实际需要对齐。 */
const PRICE_SCALE_MIN_WIDTH = 72;

/**
 * OI 柱形基线的下缘余量（占**可视窗内真实区间**的比例）。
 *
 * 0.12 ⇒ 窗内最低柱仍有 12% 柱高、最高柱占满绘图区，高低差一眼可辨。
 * 见 `OiBarChartInner` 的 `recomputeOiBase`：`base = 窗内最小值 - 区间 × 本余量`。
 */
const OI_BASE_MARGIN = 0.12;

/** 四类图的网格策略（见 `baseOptions`）。 */
export type ChartKind = 'candle' | 'volume' | 'oi' | 'equity';

/**
 * 四类图的网格可见性。
 *
 * 竖线一律关：竖线在 K 线上与蜡烛影线重叠、在柱状图上与柱子边缘重叠，
 * 视觉上等于「加密了的噪声」。时间位置靠时间轴刻度 + 十字线就够了。
 * 横线只有「读高度对应数值」的图保留：K 线与净值曲线。柱状图（成交量 / OI）
 * 的高度由柱子本身表达，再加横线只会把柱群切碎。
 */
const GRID_VISIBILITY: Record<ChartKind, { horz: boolean; vert: boolean }> = {
  candle: { horz: true, vert: false },
  equity: { horz: true, vert: false },
  volume: { horz: false, vert: false },
  oi: { horz: false, vert: false },
};

/**
 * 右价格轴上下余量。
 *
 * 只给「价格 / 净值」类图设置：库默认 `{top:0.2, bottom:0.1}` 会让蜡烛偏矮，
 * 0.12/0.08 让蜡烛变高又不贴边（最高价不贴边 ⇒ 末值标签不被裁）。
 * **成交量 / OI 不在此列**：它们的柱形余量由各自序列级 `applyOptions` 精确控制
 * （OI 必须 `{0,0}`，否则库选不出刻度、轴上一个标签都没有；成交量要留底部 12%
 * 才不会被裁掉「0」这个刻度）。这里若统一覆盖，会在主题切换时把那两处
 * 实测调好的余量冲掉。
 */
const PRICE_MARGINS: Partial<Record<ChartKind, { top: number; bottom: number }>> = {
  candle: { top: 0.12, bottom: 0.08 },
  equity: { top: 0.14, bottom: 0.06 },
};

/** 由调色板生成图表基础配置。每次主题变化都会重新生成并 applyOptions。 */
function baseOptions(
  p: ChartPalette,
  opts?: { hideTimeAxis?: boolean; kind?: ChartKind },
): DeepPartial<ChartOptions> {
  const kind: ChartKind = opts?.kind ?? 'candle';
  const g = GRID_VISIBILITY[kind];
  const margins = PRICE_MARGINS[kind];

  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: p.background },
      textColor: p.text,
      /* 轴文字 12px：与正文同级偏小一档，能读但不抢。
         11px 在 125% 缩放的显示器上会糊；13px 会让四位数刻度互相挤压。 */
      fontSize: 12,
      /* ★ 必须是真实字体栈字符串（canvas 不解析 CSS 变量），
         否则回退成库默认字体，轴上的中文（万 / 亿）会变形。 */
      fontFamily: p.font,
      // 图内 TradingView 图标（`a#tv-attr-logo`，35×19px SVG）**已按用户要求关闭**
      // （2026-10-12：用户反馈「鼠标停留几秒会弹出图标/说明，删掉」）。
      //
      // 署名合规性未受影响：库 README 的要求是「在页面上放一个指向
      // https://www.tradingview.com/ 的链接」，并说明 `attributionLogo` 只是**其中一种**
      // 满足方式。本页在展开图表工具栏里始终渲染可见文本链接
      // `LIGHTWEIGHT_CHARTS_ATTRIBUTION`（→ tradingview.com/lightweight-charts/），
      // 因此关掉图内图标后，链接署名依然在页面上存在。
      attributionLogo: false,
    },
    grid: {
      // 网格是装饰，不该有存在感：颜色取 --ds-chart-grid（对底色 1.13–1.15:1）
      horzLines: { color: p.gridHorz, style: LineStyle.Solid, visible: g.horz },
      vertLines: { color: p.gridVert, style: LineStyle.Solid, visible: g.vert },
    },
    crosshair: {
      // 保持 Normal：Magnet 会把十字线吸附到最近的 OHLC，横向移动时纵向跳，
      // 读图时「手在抖」。OHLC 精确值由图上方的读数条承担。
      mode: CrosshairMode.Normal,
      vertLine: {
        color: p.crosshair,
        width: 1,
        style: LineStyle.Dashed,
        labelBackgroundColor: p.crosshairLabel,
      },
      horzLine: {
        color: p.crosshair,
        width: 1,
        style: LineStyle.Dashed,
        labelBackgroundColor: p.crosshairLabel,
      },
    },
    rightPriceScale: {
      // 去掉价格轴竖线：它是装饰，去掉后绘图区更「宽」，也不会与卡片右内边距形成双线
      borderVisible: false,
      borderColor: p.border,
      minimumWidth: PRICE_SCALE_MIN_WIDTH,
      // 刻度文字只画完整的那一条，避免顶/底角标出现被裁掉一半的字
      entireTextOnly: true,
      // 默认 2.5，调到 2.8 刻度少一格左右，轴更干净（不要超过 3.5，会稀到读不出）
      tickMarkDensity: 2.8,
      ...(margins ? { scaleMargins: margins } : {}),
    },
    timeScale: {
      /**
       * 多图叠放（K 线 / 成交量 / 持仓量）时，只有**最下面那张**显示时间轴，
       * 否则每张图各来一行日期，同一屏出现两三排刻度（设计稿只有最底一行）。
       */
      visible: !opts?.hideTimeAxis,
      // 时间轴保留一条分界线（叠放时只有最底那条会显示）
      borderVisible: true,
      borderColor: p.border,
      timeVisible: true,
      secondsVisible: false,
      /** 默认每根蜡烛间距 9px（标准交易所饱满视觉，8~10px） */
      barSpacing: 9,
      /** 最小蜡烛间距 1.5px，防止极限缩放变成 0.1px 发丝 */
      minBarSpacing: 1.5,
      /**
       * 最新一根右侧留 4 根空档（交易所标准呼吸位）。
       * **同组所有图必须同值**，否则 fitContent 后各图窗口不同。
       */
      rightOffset: RIGHT_OFFSET_BARS,
      // 不能拖到第一根左侧的空白（纯粹的挫败感来源）；右侧保留 rightOffset 的呼吸，不锁死
      fixLeftEdge: true,
      fixRightEdge: false,
      /**
       * `true` = 尺寸变化时**锁定可见时间范围**，按宽度比例重算 `barSpacing`。
       *
       * 官方源码（`lightweight-charts.development.mjs` 的 `_private__setWidth`）：
       * ```js
       * if (options.lockVisibleTimeRangeOnResize && oldWidth !== 0) {
       *     barSpacing = barSpacing * newWidth / oldWidth;   // 重算 barSpacing
       * }
       * ```
       * 即：锁定的是**时间窗**（barSpacing 跟着变），不是「保留 barSpacing」。
       * 好处是缩放级别随宽度自适应、不会「跳一下」；代价是容器**变宽**时若数据本身
       * 比一屏窄，右侧会重新长出空白 —— 由挂载 effect 里的 `ResizeObserver` 收口
       * （见「容器宽度变化后的视窗收口」）。
       */
      lockVisibleTimeRangeOnResize: true,
      /**
       * 横轴刻度**显式按 UTC 渲染**（BT-UI-01）。
       *
       * 为什么必须显式：官方库默认格式化器按**浏览器本地时区**取时分
       * ⇒ 同一根 K 线在表里写「08:00 UTC」、在轴上画「16:00」（北京）——
       * 用户按轴上的时间对账必然错位 8 小时。
       * 这里统一用 `getUTC*`，与表格列头「(UTC)」同口径。
       */
      tickMarkFormatter: ((time: unknown) => {
        let ms: number | null = null;
        if (typeof time === 'number') {
          ms = time * 1000;
        } else if (time && typeof time === 'object' && 'year' in (time as Record<string, unknown>)) {
          const d = time as { year: number; month: number; day: number };
          ms = Date.UTC(d.year, d.month - 1, d.day);
        }
        if (ms === null || !Number.isFinite(ms)) return '';
        const dt = new Date(ms);
        const p = (n: number) => String(n).padStart(2, '0');
        const intraday = dt.getUTCHours() !== 0 || dt.getUTCMinutes() !== 0;
        return intraday
          ? `${p(dt.getUTCMonth() + 1)}-${p(dt.getUTCDate())} ${p(dt.getUTCHours())}:${p(dt.getUTCMinutes())}`
          : `${p(dt.getUTCMonth() + 1)}-${p(dt.getUTCDate())}`;
      }) as never,
    },
    // 滚轮缩放 / 拖动平移 / 捏合缩放全部走库原生实现
    handleScroll: true,
    handleScale: true,
  };
}

/**
 * 毫秒 → 官方库要求的 **UTC 秒**（`UTCTimestamp`，必须为有限非负整数）。
 *
 * 非有限 / 非正输入一律回 `0`：官方库对 `NaN`/`Infinity` 时间会直接拒绝或抛出，
 * 绝不能让脏时间透传进图表（脏值一律回 `null`/缺省，不透传 —— 项目既有数据纪律）。
 */
export function toChartSeconds(ms: number): UTCTimestamp {
  if (!Number.isFinite(ms) || ms <= 0) return 0 as UTCTimestamp;
  return Math.floor(ms / 1000) as UTCTimestamp;
}

/** 排序 + 按「秒」去重（同秒后写覆盖），保证官方库要求的严格递增唯一时间。 */
function sortUniqueByTime<T extends { timestamp: number }>(items: T[]): T[] {
  const bySec = new Map<number, T>();
  for (const item of items) {
    if (!Number.isFinite(item.timestamp) || item.timestamp <= 0) continue;
    bySec.set(toChartSeconds(item.timestamp), item);
  }
  return [...bySec.entries()].sort((a, b) => a[0] - b[0]).map(([, value]) => value);
}

const hasOhlc = (candle: Candle): boolean =>
  [candle.open, candle.high, candle.low, candle.close].every((v) => typeof v === 'number' && Number.isFinite(v));

const isFiniteNumber = (v: number | null | undefined): v is number =>
  typeof v === 'number' && Number.isFinite(v);

/** 真实 K 线 → 官方 CandlestickData（缺 OHLC 的根直接跳过 = 留空）。 */
export function buildCandleData(bars: Candle[]): CandlestickData[] {
  const out: CandlestickData[] = [];
  for (const candle of sortUniqueByTime(bars)) {
    if (!hasOhlc(candle)) continue; // 留空：不补 0、不合成
    const item: CandlestickData = {
      time: toChartSeconds(candle.timestamp),
      open: candle.open as number,
      high: candle.high as number,
      low: candle.low as number,
      close: candle.close as number,
    };
    out.push(item);
  }
  return out;
}

/**
 * K 线图真实时间点集（UTC 秒）——与 `buildCandleData` 的时间点集**逐点一致**
 * （同样跳过缺 OHLC 的根），用于构造两图共享的时间网格。
 */
export function buildCandleGrid(bars: Candle[]): number[] {
  const out: number[] = [];
  for (const candle of sortUniqueByTime(bars)) {
    if (!hasOhlc(candle)) continue;
    out.push(toChartSeconds(candle.timestamp));
  }
  return out;
}

/**
 * OI 真实样本时点（UTC 秒，升序去重）。
 *
 * 已经过 `alignOiToTimeframe` 周期对齐的样本，按 `alignedTimestamp` 落位 —— 这样本图的时间点集
 * 才与 K 线图**完全重合**（并集网格退化为 K 线网格），两图逐点对齐且蜡烛等距铺满。
 * 未对齐的样本仍用真实 `timestamp`（行为与修复前逐字一致）。
 */
export function oiSampleTimes(points: OIPoint[]): number[] {
  const bySec = new Set<number>();
  for (const point of points) {
    const ms = point.alignedTimestamp ?? point.timestamp;
    if (!Number.isFinite(ms) || ms <= 0) continue;
    bySec.add(toChartSeconds(ms));
  }
  return [...bySec].sort((a, b) => a - b);
}

/** 两个已升序去重的秒数组 → 并集（升序去重）。 */
export function mergeSortedUnique(left: number[], right: number[]): number[] {
  const out: number[] = [];
  let i = 0;
  let j = 0;
  let last = Number.NaN;
  while (i < left.length || j < right.length) {
    const a = i < left.length ? left[i] : Number.POSITIVE_INFINITY;
    const b = j < right.length ? right[j] : Number.POSITIVE_INFINITY;
    const next = a <= b ? left[i++] : right[j++];
    if (!Number.isFinite(next)) continue;
    if (next !== last) {
      out.push(next);
      last = next;
    }
  }
  return out;
}

/** 真实成交量 → 官方 HistogramData（缺成交量留空；颜色沿用涨绿跌红）。 */
export function buildVolumeData(bars: Candle[], p: ChartPalette = LIGHT_PALETTE): HistogramData[] {
  const out: HistogramData[] = [];
  for (const candle of sortUniqueByTime(bars)) {
    if (!isFiniteNumber(candle.volume)) continue; // 留空
    let color: string;
    if (!candle.isClosed) color = withAlpha(p.forming, 0.45);
    else if (!hasOhlc(candle)) color = withAlpha(p.neutral, 0.4);
    else color = (candle.close as number) >= (candle.open as number)
      ? withAlpha(p.up, 0.45)
      : withAlpha(p.down, 0.45);
    out.push({ time: toChartSeconds(candle.timestamp), value: candle.volume, color });
  }
  return out;
}

/**
 * 原手写 SVG 的「信号触发标记」（`candle.signalTag` 处的 #6366f1 圆点 + 竖线）
 * → 官方 `createSeriesMarkers` 的 markers。
 *
 * 只对**有 signalTag 且 OHLC 完整**的根出标记（与旧组件同一判据）；
 * `position: 'aboveBar'` 对应旧的「画在该根最高价上方」语义。
 * 后端 `annotations` 当前恒为空（`adaptCandles` 里 `signalTag: undefined`），
 * 因此实际不产生标记；一旦后端提供标注即自动生效，不伪造、不推断。
 */
export function buildSignalMarkers(bars: Candle[], p: ChartPalette = LIGHT_PALETTE): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const candle of sortUniqueByTime(bars)) {
    if (!candle.signalTag || !hasOhlc(candle)) continue;
    out.push({
      time: toChartSeconds(candle.timestamp),
      position: 'aboveBar',
      shape: 'circle',
      color: p.signalMarker,
    });
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * OI 总量柱形图：真实柱高 + 单一柱色（颜色不承载语义）
 * ------------------------------------------------------------------ */

export interface OiBarData {
  /** 每根柱：真实样本时点 + 该时点总量 + 统一柱色（不表达方向 / 增减） */
  bars: HistogramData[];
  /** 全部真实样本时点（UTC 秒，升序去重），用于网格去重与间隔统计 */
  sampleTimes: number[];
}

/**
 * 真实 OI 序列 → 官方 HistogramSeries 柱形数据（**柱高 = 该时点总持仓量**）。
 *
 * 与旧折线版语义一致，但改用柱形：
 *  - 该时点真实值为 `null` ⇒ **不出柱**。柱形天然「断柱」：没有点就没有柱，
 *    不存在折线那种「跨缺口把两侧连起来」的问题，故无需多段序列；
 *    空值与跨缺口（`point.hasGap`）都靠**柱间空档**在几何上呈现。
 *  - **所有柱用同一个颜色 `COLOR.oiBar`（2026-10-12 起为绿色），颜色不承载任何语义。**
 *    是**统一单色**而非按涨跌/方向着色（后者不可取）：
 *      · 后端只给**总持仓量**（数量 + 名义额），**没有多空拆分**；OI 是净额、
 *        多空恒等，OI 增加 = 新开合约（新多 + 新空同时进场），OI 减少 = 合约平掉，
 *        与「多头 / 空头」无关 ⇒ 用红/绿**两色**会**被读成多空**，是误读。
 *      · 即便改成按「环比增减」着色，同一屏里的 K 线蜡烛也用红绿表示**价格**涨跌，
 *        同色不同义必然混淆 ⇒ 干脆不用颜色表达第二个维度。
 *    柱高只表达「总量」这一个量；增减请从柱高变化、或 tooltip 的环比数值读取。
 *  - 真实样本一律原样保留：不聚合、不改写、不重采样、不伪造时间。
 */
export function buildOiBars(
  points: OIPoint[],
  unit: 'amount' | 'usd',
  p: ChartPalette = LIGHT_PALETTE,
): OiBarData {
  const bars: HistogramData[] = [];
  const sampleTimes: number[] = [];
  for (const point of sortUniqueByTime(points)) {
    const value = unit === 'usd' ? point.oiUsd : point.oiAmount;
    if (!isFiniteNumber(value)) continue; // 该时点无真实值：不出柱
    /**
     * 已做周期对齐的样本按 `alignedTimestamp` 落位（与共享网格、K 线时点重合）；
     * 未对齐时用真实时点。数值仍是真实快照，只是横坐标对齐到周期边界。
     */
    const time = toChartSeconds(point.alignedTimestamp ?? point.timestamp);
    bars.push({ time, value, color: p.oiBar });
    sampleTimes.push(time);
  }
  return { bars, sampleTimes };
}

/* ------------------------------------------------------------------ *
 * 共享时间网格（两图并集）
 * ------------------------------------------------------------------ */

/**
 * 本图缺失的网格时点 → 纯 whitespace 锚点（只含时间、**不含任何数值**）。
 *
 * 这些点：
 *  - 不参与画线（官方 `LinePaneViewBase` 只遍历有效点）；
 *  - 不参与价格轴（无值即不贡献 autoscale）；
 *  - 唯一作用是让本图时间轴点集**补齐到两图共享的并集**，
 *    使两图拥有完全相同的离散时间点集 ⇒ 相同索引 ⇒ 相同横坐标映射
 *    ⇒ 同一 UTC 时间窗下，两图同一时间点落在同一 x（刻度不再错位）。
 *
 * 这不是「造数据」：锚点不携带任何数值，时间点全部来自真实数据；
 * 真实 K 线 / 真实 OI 样本既未聚合也未重采样、未改时间戳。
 */
export function buildGridAnchor(gridTimes: number[], ownTimes: number[]): WhitespaceData[] {
  const occupied = new Set<number>(ownTimes);
  const seen = new Set<number>();
  const out: WhitespaceData[] = [];
  for (const raw of gridTimes) {
    if (!Number.isFinite(raw)) continue;
    const sec = Math.floor(raw);
    if (sec <= 0 || occupied.has(sec) || seen.has(sec)) continue;
    seen.add(sec);
    out.push({ time: sec as UTCTimestamp });
  }
  out.sort((a, b) => (a.time as number) - (b.time as number));
  return out;
}

/* ------------------------------------------------------------------ *
 * 增量更新：只在「末尾追加 / 末根变化」时用官方 `update()`，
 * 历史重置（切周期、切币、回填更早历史、缺口变化、结构变化）才用 `setData()`。
 * 目的：实时刷新不整表重设，避免官方库在 setData 时重置可见范围。
 * ------------------------------------------------------------------ */

const DATA_KEYS = ['time', 'open', 'high', 'low', 'close', 'value', 'color', 'borderColor', 'wickColor'] as const;

function sameDataPoint(a: unknown, b: unknown): boolean {
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  for (const key of DATA_KEYS) {
    if (left[key] !== right[key]) return false;
  }
  return true;
}

function sameDataArray(a: unknown[], b: unknown[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (!sameDataPoint(a[i], b[i])) return false;
  }
  return true;
}

function timeOf(item: unknown): number | null {
  const value = (item as { time?: unknown } | null | undefined)?.time;
  return typeof value === 'number' ? value : null;
}

/**
 * 判断 `next` 是否只是 `prev` 的「末根变化 / 追加一根」。
 *
 * 除最后一根外的前缀必须逐字段一致（前缀变化 = 历史被重置，走 setData）；
 * 且 `next` 中与 `prev` 末根**对齐位置**的那一根，时间**不得向前移动**
 * —— 否则说明是切币/切周期/回填更早历史造成的整表替换，必须走 setData。
 *
 * 注意：本函数**不保证** `prev` 的末根在 `next` 中没变（追加新根时旧末根可能
 * 同时从 forming 变为 closed）。因此调用方必须另行核对/更新旧末根，见 `applySeriesUpdate`。
 *
 * 导出仅为可执行验证（纯函数，无副作用）。
 */
export function isIncrementalTail(prev: unknown[], next: unknown[]): boolean {
  if (prev.length === 0) return false;
  if (next.length < prev.length) return false; // 变短（未收盘根消失等）→ 重置
  if (next.length - prev.length > 1) return false; // 一次补多根 → 重置
  for (let i = 0; i < prev.length - 1; i += 1) {
    if (!sameDataPoint(prev[i], next[i])) return false;
  }
  const prevLastTime = timeOf(prev[prev.length - 1]);
  const alignedTime = timeOf(next[prev.length - 1]);
  if (prevLastTime !== null && alignedTime !== null && alignedTime < prevLastTime) return false;
  return true;
}

/** 官方序列写入接口的结构子集（`ISeriesApi` 天然满足）。 */
interface SeriesWriter<TData> {
  setData(data: TData[]): void;
  update(bar: TData, historicalUpdate?: boolean): void;
}

/**
 * 把新数据下发到官方序列，并尽量走增量 `update()`。
 *
 * - 前缀/结构变化或显式重置 ⇒ `setData()`；
 * - 同长度（末根变化）⇒ 只 `update()` 末根；
 * - 追加一根 ⇒ **先核对并更新旧末根**（它可能刚从 forming 变为最终闭合，
 *   OHLC/成交量被上游修正），再 `update()` 新根。若只 update 新根，
 *   旧末根会停留在过期值。
 *
 * 导出仅为可执行验证（纯函数，无副作用）。
 */
export function applySeriesUpdate<TData>(
  series: SeriesWriter<TData>,
  prev: TData[],
  next: TData[],
  forceReset: boolean,
): void {
  if (forceReset || !isIncrementalTail(prev, next)) {
    series.setData(next);
    return;
  }
  if (next.length === prev.length) {
    series.update(next[next.length - 1]);
    return;
  }
  const prevLast = prev[prev.length - 1];
  const nextAlignedLast = next[next.length - 2];
  if (!sameDataPoint(prevLast, nextAlignedLast)) series.update(nextAlignedLast);
  series.update(next[next.length - 1]);
}

/**
 * 价格精度推断：真实池价格可低至 1.25e-05，官方默认精度 2 位会把价格抹平。
 * 只按数量级选择**展示精度**，不改动任何数值。
 *
 * 用 `reduce` 而非 `Math.min(...array)`：后者受引擎**实参个数上限**限制，
 * 拖入长历史时会直接抛 `RangeError`。
 */
function inferPriceFormat(data: CandlestickData[]): { precision: number; minMove: number } {
  const min = data.reduce<number>((acc, item) => {
    let next = acc;
    for (const value of [item.open, item.high, item.low, item.close]) {
      if (Number.isFinite(value) && value > 0 && value < next) next = value;
    }
    return next;
  }, Number.POSITIVE_INFINITY);
  if (!Number.isFinite(min)) return { precision: 2, minMove: 0.01 };
  let precision: number;
  if (min >= 1000) precision = 2;
  else if (min >= 1) precision = 4;
  else if (min >= 0.01) precision = 5;
  else if (min >= 0.0001) precision = 6;
  else precision = 8;
  return { precision, minMove: Math.pow(10, -precision) };
}

/**
 * 大数值（OI 数量 / 名义额）刻度精度：只影响刻度显示位数。
 * 同样用 `reduce`，避免实参个数上限。
 */
function inferValueFormat(values: number[]): { precision: number; minMove: number } {
  const max = values.reduce<number>((acc, value) => {
    if (!Number.isFinite(value)) return acc;
    const abs = Math.abs(value);
    return abs > acc ? abs : acc;
  }, Number.NEGATIVE_INFINITY);
  if (!Number.isFinite(max)) return { precision: 2, minMove: 0.01 };
  let precision: number;
  if (max >= 1000) precision = 0;
  else if (max >= 1) precision = 2;
  else precision = 4;
  return { precision, minMove: Math.pow(10, -precision) };
}

/* ------------------------------------------------------------------ *
 * 视窗与跨图同步
 * ------------------------------------------------------------------ */

export interface ChartViewport {
  fromMs: number;
  toMs: number;
  /** true = 跟随最新（视窗右缘贴住最新根）；false = 用户已拖到历史视窗 */
  followLatest: boolean;
}

/**
 * 最新一根右侧留几根空档（`rightOffset`）。
 * 交易所标准通常留 4 根呼吸位。
 */
const RIGHT_OFFSET_BARS = 4;

/**
 * 「数据太窄，不给右侧呼吸位」的根数阈值。
 *
 * `rightOffset` 的单位是**根**，不是像素：2 根空档放在 5,000 根的数据上几乎看不见，
 * 放在 18 根（一个 1h 回测窗口）上就是 2/18 ≈ 11% 的右侧空白 —— 视觉上就是「没铺满」。
 * 实测用户截图那一轮 ACHUSDT 1h 只有 18 根，右侧空 12%（= 2 根 + 取整误差）。
 * 因此：数据少于一屏（本阈值的量级）时把呼吸位压到 0，优先「铺满」。
 */
const NARROW_DATA_BARS = 50;

/**
 * 同组图表同步成员。
 * 同步按**真实时间范围**（不是逻辑索引）——K 线与 OI 的样本数不同，
 * 按逻辑索引同步会导致两者时间窗错位。
 */
export interface ChartSyncMember {
  chart: IChartApi;
  /** 该图真实数据域（UTC 秒）；无任何时间点为 `null` */
  domain: { from: number; to: number } | null;
  /**
   * 本图**真实数据点数**（蜡烛根数 / 网格点数）。
   *
   * 只用于「尺寸变化后数据还装不装得下一屏」的判断（见 `ResizeObserver` 收口）：
   * 装得下就按数据域铺满，装不下就只贴右缘、不动缩放级别。
   */
  points: number;
  /** 已销毁：销毁后一律不再调用该图的任何官方 API */
  disposed: boolean;
  /**
   * 时间窗**主图 / 跟随图**角色。
   *
   * 主图（K 线）首次落位只认自己的「恢复视窗 / 全部已加载数据」，
   * **绝不复制跟随图（OI）的当前视窗** —— 跟随图可能在自身数据到达后
   * 先做了一次 fitContent，若主图去复制它，用户视窗就会被那个默认窗口顶掉（实测缺陷）。
   */
  role: 'leader' | 'follower';
}

export interface ChartSyncHub {
  members: ChartSyncMember[];
  /** 程序化同步期间置位，避免同一次同步内的双向回环 */
  syncing: boolean;
  /**
   * 「用户直接操作」窗口的截止时刻（`performance.now()` 基准）。
   *
   * 时间窗同步是**单向**的：只有 `leader`（K 线）驱动 `follower`（OI）。
   * 否则 OI 图在自身数据/单位变化时产生的范围事件会**反向覆盖**主图视窗
   * （实测：切周期重置期间 OI 旧窗口把主图恢复后的视窗顶掉）。
   * 但用户**主动**在 OI 图上滚轮/拖动时，仍应联动主图 —— 由调用方在交互时
   * 把本字段推到「当前 + 500ms」，该窗口内允许 `follower` 反向同步。
   */
  userDrivenUntil: number;
}

export function createChartSyncHub(): ChartSyncHub {
  return { members: [], syncing: false, userDrivenUntil: 0 };
}

/** 用户交互后允许 `follower` 反向同步的时长（毫秒）。 */
export const USER_DRIVEN_SYNC_MS = 500;

/**
 * 程序化写入（`setData` / `update` / `setVisibleRange` / `fitContent` / `scrollToRealTime`）
 * 之后的时间静默窗：窗口内由官方库派发的可见范围事件**不得**触发视窗上报，
 * 否则重置期间的中间范围会污染当前 viewKey 的视窗缓存。
 */
const PROGRAMMATIC_QUIET_MS = 400;

/** 官方 API 兜底：任何图表调用异常都不得冒泡打断页面渲染。 */
function safeTimeScaleCall(fn: () => void): void {
  try {
    fn();
  } catch {
    // 官方库在空数据/销毁竞态下会抛错；此处静默降级，保持页面可用
  }
}

function safeCall<T>(fn: () => T): T | null {
  try {
    return fn();
  } catch {
    return null;
  }
}

/**
 * 叠放图「隐藏本图时间轴」的**真实**实现（`hideTimeAxis`）。
 *
 * **为什么不能用官方 `timeScale.visible: false`**（2026-10-18 实证）：
 * v5.2.1 的 `HorzScaleOptions` 确实声明了 `visible`（默认 `true`，见
 * `dist/typings.d.ts:1423`），`timeScaleOptionsDefaults` 里也带了 `visible: true`，
 * 但 dist 产物里**没有任何读取点** —— `grep "options.visible"` 零命中。
 * 传 `false` 后时轴 canvas 依旧存在：探针实测 K 线图与 OI 图各有一条 28px 的
 * 时轴画布（`top:677` / `top:769`），同一屏出现两排日期刻度，与设计稿（只有最底一行）不符。
 *
 * **做法**：让图表容器比外层多出一个「时轴高度」，外层 `overflow:hidden` 裁掉多出的那一条。
 * 高度不写死 —— 创建后读 `chart.timeScale().height()` 的**实测值**写进外层
 * CSS 变量 `--tz-axis-h`，字体/主题变化时随之更新；容器 CSS 用
 * `height: calc(100% + var(--tz-axis-h))`，缺省回退 28px（实测值）。
 */
function applyTimeAxisClip(chart: IChartApi, container: HTMLElement): void {
  const host = container.parentElement;
  if (!host) return;
  const sync = () => {
    const height = safeCall(() => chart.timeScale().height()) ?? 0;
    if (height <= 0) return;
    const next = `${Math.round(height)}px`;
    if (host.style.getPropertyValue('--tz-axis-h') !== next) {
      host.style.setProperty('--tz-axis-h', next);
    }
  };
  sync();
  // 首帧后官方库才把时轴量出来（字体度量依赖样式落地），补一次
  requestAnimationFrame(sync);
}

/**
 * 两图价格轴宽度对齐。
 *
 * 官方价格轴宽度按**标签内容**自适应：K 线标签（如 `0.00001234`）与 OI 标签
 * （如 `24,774,762`）宽度不同 ⇒ 绘图区宽度不同 ⇒ 即使两图时间点集完全一致，
 * 同一时间点仍会落在不同 x 上（刻度错位）。官方为此提供 `PriceScaleOptions.minimumWidth`：
 * 读取同组各图右价格轴的真实宽度，取最大值回写 `minimumWidth`，使两图同宽。
 * 该操作幂等（第二次读到的即同一最大值），不会震荡。
 */
function alignPriceScaleWidths(hub: ChartSyncHub): void {
  const scales: { scale: IPriceScaleApi }[] = [];
  for (const member of hub.members) {
    if (member.disposed) continue;
    const scale = safeCall(() => member.chart.priceScale('right'));
    if (scale) scales.push({ scale });
  }
  if (scales.length < 2) return;
  let maxWidth = 0;
  for (const item of scales) {
    const width = safeCall(() => item.scale.width()) ?? 0;
    if (width > maxWidth) maxWidth = width;
  }
  if (maxWidth <= 0) return;
  for (const item of scales) {
    safeTimeScaleCall(() => item.scale.applyOptions({ minimumWidth: maxWidth }));
  }
}

/**
 * 把**同一条真实 UTC 时间范围**应用到同组其它图表。
 *
 * **必须严格守卫**：官方 `TimeScale._internal_timeToIndex()` 只在该图
 * `points.length < 1` 时返回 `null`，再经 `ensureNotNull` 抛 `Value is null`
 * （异常沿 `rangeHandler` 冒泡会打挂整个 React 渲染，实测白屏）。
 *
 * 因此这里逐条守卫：
 *  1. 目标已销毁 → 跳过；
 *  2. 目标**没有任何时间点** → 跳过（此时任何 setVisibleRange 都是非法的）；
 *  3. 仍以防万一包一层 try/catch —— 同步是增强能力，失败绝不允许影响页面。
 *
 * **不把范围夹到目标自身的数据域**：夹取会让两图显示不同时间窗
 * （历史窗落在 OI 域外时会保留旧窗口）。这里原样下发同一条 UTC 窗口，
 * 目标图样本域外自然显示为空白。
 */
function applyTimeRangeTo(member: ChartSyncMember, fromSec: number, toSec: number): boolean {
  if (member.disposed || !member.domain) return false;
  if (!(toSec > fromSec)) return false;
  try {
    member.chart.timeScale().setVisibleRange({
      from: fromSec as UTCTimestamp,
      to: toSec as UTCTimestamp,
    });
    return true;
  } catch {
    return false;
  }
}

/** 该图当前是否具备可安全调用 `setVisibleRange` 的条件（有真实时间点 + 未销毁）。 */
function canSetVisibleRange(member: ChartSyncMember): boolean {
  return !member.disposed && member.domain !== null && member.domain.to > member.domain.from;
}

/**
 * 该图当前可见的**逻辑范围**（已销毁 / 无时间点 / 区间非法 → `null`，绝不抛错）。
 *
 * 为什么跨图同步用它、而不是真实时间范围：
 * 两图的时间点集被构造成**同一个共享并集网格**（各自只补纯 whitespace 锚点，
 * 真实 K 线值 / 真实 OI 值一律不变），因此同一个「逻辑索引」在两张图上指同一时间点。
 * 而 `getVisibleRange()` 只精确到**整秒**：把它回写成 `setVisibleRange` 后，
 * 目标图的逻辑边界会被吸附（实测主图 441.5239 → 跟随图 441），`barSpacing` 随之不等
 * （实测 3.937282… vs 3.930070…），同一 UTC 时间点横坐标最多差 5.4px。
 * 主图边界本身是分数索引时误差还会更大。改用逻辑范围后两图逐点一致（实测 0px）。
 */
function getVisibleLogicalRange(member: ChartSyncMember): LogicalRange | null {
  if (member.disposed) return null;
  const range = safeCall(() => member.chart.timeScale().getVisibleLogicalRange());
  if (!range) return null;
  if (typeof range.from !== 'number' || typeof range.to !== 'number') return null;
  if (!Number.isFinite(range.from) || !Number.isFinite(range.to)) return null;
  if (!(range.to > range.from)) return null;
  return range;
}

/**
 * 把同一条**可见逻辑范围**应用到目标图。
 * 与 `applyTimeRangeTo` 同一套守卫：已销毁 / 无任何时间点 / 区间非法 → `false`，绝不抛错。
 */
function applyLogicalRangeTo(member: ChartSyncMember, range: LogicalRange | null): boolean {
  if (member.disposed || !member.domain || !range) return false;
  try {
    member.chart.timeScale().setVisibleLogicalRange({ from: range.from, to: range.to });
    return true;
  } catch {
    return false;
  }
}

/**
 * 把**主图（K 线）**当前的可见逻辑范围应用到本图（时间窗跟随图专用）。
 *
 * 用于跟随图自身数据变化之后重新对齐 —— 跟随图的 `setData` 会让官方库按新点集
 * 重算它自己的范围，若此时不去对齐主图，两图就会显示不同时间窗
 * （实测切周期/补历史后 1m 上出现 1576px 的横向错位）。
 * 找不到主图 / 主图暂无范围 → `false`，调用方再决定回落策略。
 */
function alignToLeader(hub: ChartSyncHub, member: ChartSyncMember): boolean {
  const leader = hub.members.find(
    (item) => item !== member && !item.disposed && item.role === 'leader',
  );
  if (!leader) return false;
  const range = getVisibleLogicalRange(leader);
  if (!range) return false;
  hub.syncing = true;
  try {
    return applyLogicalRangeTo(member, range);
  } finally {
    hub.syncing = false;
  }
}

/** 图表实例句柄：供调用方做跨图十字线联动与视窗落实。 */
export interface ChartHandle {
  chart: IChartApi;
  /** 时点不在本图可见范围时只清除十字线，不越界定位 */
  crosshairAt(timeSec: UTCTimestamp, price: number | null): void;
  clearCrosshair(): void;
  /**
   * 按**真实 UTC 时间**落位可见范围（入参毫秒，内部转官方 UTC 秒）。
   *
   * 供调用方在「更早历史补齐之后」重新落实用户视窗；与其它时间轴调用同样守卫：
   * 已销毁 / 该图无任何时间点 / 区间非法 → 返回 `false`，绝不抛错打断渲染。
   */
  setViewport(fromMs: number, toMs: number): boolean;
  /**
   * 读取当前可见范围（毫秒）；已销毁 / 无任何时间点 → `null`，绝不抛错。
   *
   * 调用方（父组件）在用户交互时用它与 `setViewport` 配合固化视窗 ——
   * 切周期会**原生卸载**旧实例，父组件持有的旧句柄此时已失效，
   * 因此这里必须自带守卫而不是让父组件去猜实例是否还有效。
   */
  visibleRange(): { fromMs: number; toMs: number } | null;
}

/** 十字线联动前先确认时点落在对方可见范围内。 */
function isTimeVisible(chart: IChartApi, timeSec: number): boolean {
  const visible = chart.timeScale().getVisibleRange();
  if (!visible || typeof visible.from !== 'number' || typeof visible.to !== 'number') return true;
  return timeSec >= (visible.from as number) && timeSec <= (visible.to as number);
}

export interface ChartHover {
  /** 触发悬停的图表来源 */
  source: 'candle' | 'oi';
  /** 悬停时点（毫秒，来自官方十字线） */
  timeMs: number;
  /** 命中真实 K 线时给出该根原始对象（含 volumeUsd / isClosed / signalTag） */
  candle: Candle | null;
  /** 命中真实 OI 行时给出该行原始对象（含原单位数量与名义额） */
  oiPoint?: OIPoint | null;
}

interface SharedChartProps {
  viewKey: string;
  /** 该 viewKey 首次挂载时的恢复视窗；null = 显示全部已加载数据 */
  initialViewport: ChartViewport | null;
  /**
   * 视窗上报。**必须携带所属 viewKey** —— 该键在**调度时刻**捕获，
   * 调用方据此拒绝「上一周期的迟到报告」，避免用旧周期的范围污染新周期的视窗缓存。
   */
  onViewportChange: (viewport: ChartViewport, viewKey: string) => void;
  onHover: (hover: ChartHover | null) => void;
  onReady: (handle: ChartHandle) => void;
  syncHub: ChartSyncHub;
  /** 两图共享的真实时间点并集（UTC 秒）：本图补纯 whitespace 锚点补齐到它 */
  gridTimes: number[];
  /**
   * 叠放多张图时隐藏本图的时间轴（只让最下面那张显示）。
   * 缺省 `false` ⇒ 行为与既有调用完全一致。
   */
  hideTimeAxis?: boolean;
}

/** 复用的图表生命周期骨架：创建 / 销毁 / 十字线 / 时间轴同步 / 视窗上报。 */
function useChartLifecycle(
  build: (chart: IChartApi) => {
    /** 该图的时间基准序列（用于十字线定位） */
    series: ISeriesApi<'Candlestick'> | ISeriesApi<'Line'>;
    onCrosshair: (param: MouseEventParams<Time>) => void;
    /** 销毁前清理（如 marker 插件 detach）；在 `chart.remove()` 之前调用 */
    onDispose?: () => void;
  },
  followToleranceSec: React.MutableRefObject<number>,
  syncHub: ChartSyncHub,
  onHover: SharedChartProps['onHover'],
  onReady: SharedChartProps['onReady'],
  onViewportChange: SharedChartProps['onViewportChange'],
  /**
   * 本图在时间窗同步中的角色：
   *  - `leader`（K 线）：首次落位只认「恢复视窗 / 全部已加载数据」；
   *  - `follower`（OI）：首次落位优先复制 `leader` 的当前时间窗。
   * 见 `applyInitialViewport` 与 `ChartSyncMember.role`。
   */
  role: ChartSyncMember['role'],
  /** 本图当前所属视图键（`tokenId:timeframe`）；视窗上报**按调度时刻**绑定它 */
  viewKey: string,
  /** 叠放时隐藏本图的时间轴（只让最下面那张显示，避免一屏两三排日期刻度） */
  hideTimeAxis = false,
  /** 本图类型：决定网格方向与右轴余量（见 `GRID_VISIBILITY` / `PRICE_MARGINS`） */
  kind: ChartKind = 'candle',
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | null>(null);
  const memberRef = useRef<ChartSyncMember | null>(null);
  const lastBarSecRef = useRef<number | null>(null);
  const followRef = useRef(true);
  const initRef = useRef(false);
  /** 数据 effect 内用于判定「视图键是否变化」的已应用键 */
  const appliedViewKeyRef = useRef<string | null>(null);
  /** 每次渲染都同步为最新视图键：供**上报归属**使用（调度时刻取值） */
  const renderViewKeyRef = useRef(viewKey);
  renderViewKeyRef.current = viewKey;
  const reportTimerRef = useRef<number | null>(null);
  /** 程序化写入静默窗截止时刻（`performance.now()` 基准） */
  const quietUntilRef = useRef(0);

  const onHoverRef = useRef(onHover);
  onHoverRef.current = onHover;

  /**
   * 图表调色板：随主题重算。`build` 只在挂载时跑一次，因此这里用 ref 取当前值，
   * 不把 palette 放进 build 的依赖 —— 否则每次切主题都会重建图表实例、丢掉缩放。
   */
  const palette = useChartPalette();
  const paletteRef = useRef(palette);
  paletteRef.current = palette;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;

  /**
   * 标记「刚发生程序化写入」：此后 `PROGRAMMATIC_QUIET_MS` 内由官方库派发的
   * 可见范围事件不触发上报。重置/落位/整表替换都会调用它。
   */
  const markProgrammatic = useCallback(() => {
    quietUntilRef.current = performance.now() + PROGRAMMATIC_QUIET_MS;
  }, []);

  /** 立即取消待发的上报（切周期/切币时调用，避免旧周期的范围落到新视图键上）。 */
  const cancelPendingReport = useCallback(() => {
    if (reportTimerRef.current !== null) {
      window.clearTimeout(reportTimerRef.current);
      reportTimerRef.current = null;
    }
  }, []);

  /**
   * 调度一次视窗上报（250ms 去抖）。
   *
   * **归属绑定**：`viewKey` 在**调度时刻**捕获，而不是在定时器触发时读取 ——
   * 否则「切周期期间调度的报告」会在触发时被记到新视图键上，
   * 用旧周期的范围污染新周期的视窗缓存（实测缺陷）。
   */
  const scheduleReport = useCallback(() => {
    if (reportTimerRef.current !== null) return;
    const key = renderViewKeyRef.current;
    reportTimerRef.current = window.setTimeout(() => {
      reportTimerRef.current = null;
      // 视窗上报的同时顺带对齐价格轴宽度（窗口尺寸变化也会走到这里）
      alignPriceScaleWidths(syncHub);
      // 程序化写入静默窗内：本次范围来自库的重算，不是用户视窗，丢弃
      if (performance.now() < quietUntilRef.current) return;
      const chart = chartRef.current;
      if (!chart) return;
      const range = chart.timeScale().getVisibleRange();
      if (!range || typeof range.from !== 'number' || typeof range.to !== 'number') return;
      onViewportChangeRef.current({
        fromMs: (range.from as number) * 1000,
        toMs: (range.to as number) * 1000,
        followLatest: followRef.current,
      }, key);
    }, 250);
  }, [syncHub]);

  /**
   * 结束程序化阶段：清除静默窗并取消当前待发上报。
   *
   * 官方库的落位（`setVisibleRange` / `fitContent`）是**异步生效**的 ——
   * 调用返回后立刻 `getVisibleRange()` 读到的是**落位前**的旧范围
   * （实测：状态位显示 10-05T20:37–10-08T13:00，而画布刻度已是 10-04～10-07）。
   * 因此这里**不读取**任何范围，只把「可以上报了」这件事放开；
   * 真正的上报由**原生可见范围事件**驱动去抖回调在 250ms 后读取 ——
   * 那时范围已落实，读到的才是与画布一致的值。
   */
  const endProgrammatic = useCallback(() => {
    quietUntilRef.current = 0;
    cancelPendingReport();
  }, [cancelPendingReport]);

  /** 视图键变化：取消待发上报并进入静默窗（新周期从「干净」状态开始）。 */
  useEffect(() => {
    cancelPendingReport();
    markProgrammatic();
  }, [viewKey, cancelPendingReport, markProgrammatic]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const chart = createChart(container, baseOptions(paletteRef.current, { hideTimeAxis, kind }));
    // 叠放图（K 线 / 成交量）裁掉自己的时间轴：只留最下面那张的日期行
    if (hideTimeAxis) applyTimeAxisClip(chart, container);
    const built = build(chart);
    chartRef.current = chart;
    seriesRef.current = built.series;
    const member: ChartSyncMember = { chart, domain: null, points: 0, disposed: false, role };
    memberRef.current = member;
    syncHub.members.push(member);

    const crosshairHandler = (param: MouseEventParams<Time>) => built.onCrosshair(param);
    chart.subscribeCrosshairMove(crosshairHandler);

    const rangeHandler = (_range: LogicalRange | null) => {
      const visible = chart.timeScale().getVisibleRange();
      if (visible && typeof visible.from === 'number' && typeof visible.to === 'number') {
        /**
         * 时间窗同步**单向**：只有 `leader`（K 线）驱动 `follower`（OI）。
         * `follower` 的范围变化只有两种来源 —— ①被主图同步（`syncing` 已置位）；
         * ②自身数据/单位变化引起的库重算 —— 两者都**不得**反向覆盖主图视窗
         * （实测：切周期重置期间 OI 旧窗口把主图恢复后的视窗顶掉）。
         * 例外：用户**主动**在 OI 图上滚轮/拖动（调用方把 `userDrivenUntil` 推到未来 500ms）。
         */
        const mayDrive = member.role === 'leader' || performance.now() < syncHub.userDrivenUntil;
        if (!syncHub.syncing && mayDrive) {
          /**
           * 按**可见逻辑范围**同步给同组其它图表（不再按真实时间范围）。
           *
           * 两图共用同一个时间点并集网格 ⇒ 逻辑索引在两张图上指同一时间点，
           * 逻辑范围可以直接互用；而按时间范围同步会因整秒取整把目标图的逻辑边界
           * 吸附成整数、`barSpacing` 与主图不等 ⇒ 同一时间点横坐标差最多 5.4px（实测）。
           * 官方库仅在逻辑范围**确实变化**时才派发事件，故按逻辑范围同步同样不会回环。
           */
          const logical = getVisibleLogicalRange(member);
          if (logical) {
            syncHub.syncing = true;
            try {
              for (const other of syncHub.members) {
                if (other.chart !== chart) {
                  applyLogicalRangeTo(other, logical);
                }
              }
            } finally {
              syncHub.syncing = false;
            }
          }
        }
        const last = lastBarSecRef.current;
        if (last !== null) {
          followRef.current = (visible.to as number) >= last - followToleranceSec.current;
        }
      }
      scheduleReport();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(rangeHandler);

    // 窗口尺寸变化会改变价格轴所需宽度 → 重新对齐两图价格轴
    const resizeHandler = () => alignPriceScaleWidths(syncHub);
    window.addEventListener('resize', resizeHandler);

    /**
     * 容器**宽度变化**后的视窗收口（BT-KLINE-CENTER-01）。
     *
     * 现象（用户截图）：ACHUSDT 1h 只有 18 根，右侧却空出一大片，看起来像「K 线没导全 / 没居中」。
     * 复现（视口 1668 → 1200 → 1668）：只剩 10 根可见、右侧空白 370/820 = 45%。
     *
     * 根因**不是**数据、也不是「恢复了旧视窗」，而是官方库在变宽时把 `rightOffset` **顶大**：
     *  `lockVisibleTimeRangeOnResize: true` 让 `barSpacing` 按绘图区宽度比例缩放（时间窗不变，
     *  见 `baseOptions` 注释与官方 `_private__setWidth`），随后 `fixLeftEdge: true` 的补偿分支
     *  `rightOffset -= Math.round((oldWidth - newWidth) / barSpacing) + 1` 在**变宽**时变成
     *  「加」，实测把 `rightOffset` 从 2 顶到 10 ⇒ 右侧凭空多出 8 根空档（8/18 ≈ 44%）。
     *  同一轮实测的中间态：绘图区 402px、`barSpacing` 22.33（= 402/18，18 根刚好铺满）；
     *  变宽到 820px 后 `barSpacing` 45.55（= 820/18，比例正确），**多出来的只有 rightOffset**。
     *
     * 处理规则（**只在「跟随最新」时动**；用户已拖到历史视窗则一律不碰）：
     *   · 先按「数据够不够宽」复位 `rightOffset`（见 `NARROW_DATA_BARS`）；
     *   · 当前缩放级别下**全部数据装得下** ⇒ 按真实数据域重新落位（与首屏
     *     `applyInitialViewport` 同一套逻辑）⇒ 数据重新铺满绘图区；
     *   · 用户已放大到局部（数据比一屏多）⇒ **不动缩放、不动平移**，
     *     只纠正被顶歪的 `rightOffset`（否则「我放大看某根、动一下窗口就弹回去」）。
     * 跟随图（OI / 成交量）不自己判方向，一律以主图为准重新对齐 —— 主图重排后
     * 会通过可见逻辑范围事件同步下来，这里补一次覆盖「范围没变、只有宽度变了」的情形。
     */
    let lastW = 0;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect?.width ?? 0);
      if (!w || w === lastW) return;
      const prevW = lastW;
      lastW = w;
      if (!prevW || member.disposed) return;   // 首帧只记宽度：首屏落位另有其路

      if (member.role === 'follower') {
        alignToLeader(syncHub, member);
        return;
      }
      if (!followRef.current || !member.domain || !member.points) return;
      const domain = member.domain;

      const ts = chart.timeScale();
      const barSpacing = ts.options().barSpacing;
      const plotW = safeCall(() => ts.width()) ?? 0;
      const slots = barSpacing > 0 ? plotW / barSpacing : 0;
      /**
       * 复位 `rightOffset`：`fixLeftEdge` 的官方补偿会把它**顶大**。
       *
       * 官方 `_private__setWidth` 里，当视窗贴住左缘（`visibleRange.from <= 0`）时
       * `rightOffset -= Math.round((oldWidth - newWidth) / barSpacing) + 1`；
       * 变宽时这一项为负 ⇒ `rightOffset` 反而**增加**，实测 18 根数据上从 2 涨到 10，
       * 于是右侧凭空多出 8 根空档（364/820 = 44% 空白，就是用户截图那一幕）。
       * 这里每次宽度变化后按「数据够不够宽」复位。
       */
      const desiredOffset = member.points <= NARROW_DATA_BARS ? 0 : RIGHT_OFFSET_BARS;
      const curOffset = ts.options().rightOffset;
      const allFit = slots > 0 && member.points + desiredOffset <= slots + 0.5 && member.points <= NARROW_DATA_BARS;

      if (!allFit) {
        /**
         * 用户已放大到局部（数据比一屏多）⇒ **不动缩放、不动平移**。
         *
         * 官方 `lockVisibleTimeRangeOnResize` 已经把时间窗锁住了，这里再调
         * `scrollToPosition` 会把人甩到最右边 —— 实测「放大看某根 → 拖一下窗口」
         * 视窗直接跳到最新根，等于把用户的操作抹掉。
         * 唯一需要纠正的是被 `fixLeftEdge` 顶歪的 `rightOffset`：先记住当前可见范围，
         * 复位后再按原范围落回，保证「只有宽度变了、时间窗一根不差」。
         */
        if (curOffset !== desiredOffset) {
          const keep = safeCall(() => ts.getVisibleRange());
          safeTimeScaleCall(() => ts.applyOptions({ rightOffset: desiredOffset }));
          if (keep && Number(keep.to) > Number(keep.from)) {
            applyTimeRangeTo(member, Number(keep.from), Number(keep.to));
          }
        }
        return;
      }

      // 当前缩放级别下**全部数据都装得下** ⇒ 按数据域重新铺满（与首屏落位同一套逻辑）
      if (curOffset !== desiredOffset) {
        safeTimeScaleCall(() => ts.applyOptions({ rightOffset: desiredOffset }));
      }
      applyTimeRangeTo(member, domain.from, domain.to);
    });
    ro.observe(container);

    onReadyRef.current({
      chart,
      crosshairAt: (timeSec, price) => {
        if (price === null || !Number.isFinite(price) || member.disposed) {
          safeTimeScaleCall(() => chart.clearCrosshairPosition());
          return;
        }
        safeTimeScaleCall(() => {
          if (!isTimeVisible(chart, timeSec)) {
            chart.clearCrosshairPosition();
            return;
          }
          chart.setCrosshairPosition(price, timeSec, built.series);
        });
      },
      clearCrosshair: () => safeTimeScaleCall(() => chart.clearCrosshairPosition()),
      /**
       * 按**真实 UTC 时间**重新落实可见范围（入参毫秒）。
       *
       * 用途：调用方在「更早历史补齐之后」把用户视窗原样落实回来 ——
       * 首屏只有一页数据时官方库会把窗口夹到已有数据，若不重新落实，
       * 用户视窗就会被那一页的默认窗口顶掉（实测「收起→再展开只剩 60 根」缺陷）。
       *
       * 与其它时间轴调用同一套守卫：已销毁 / 无任何时间点 / 区间非法 → `false`，绝不抛错。
       */
      setViewport: (fromMs, toMs) => {
        if (member.disposed || !member.domain) return false;
        followRef.current = false;
        const fromSec = toChartSeconds(fromMs);
        const toSec = toChartSeconds(toMs);
        if (!(toSec > fromSec)) return false;
        const ts = chart.timeScale();
        const fromIdx = ts.timeToIndex(fromSec);
        const toIdx = ts.timeToIndex(toSec);
        if (typeof fromIdx === 'number' && typeof toIdx === 'number' && toIdx > fromIdx) {
          return applyLogicalRangeTo(member, { from: fromIdx as unknown as Logical, to: toIdx as unknown as Logical });
        }
        return applyTimeRangeTo(member, fromSec, toSec);
      },
      visibleRange: () => {
        if (member.disposed || !member.domain) return null;
        const range = safeCall(() => chart.timeScale().getVisibleRange());
        if (!range || typeof range.from !== 'number' || typeof range.to !== 'number') return null;
        const fromMs = (range.from as number) * 1000;
        const toMs = (range.to as number) * 1000;
        // 蜡烛柱时间轴最右端 open_time 对应的物理右闭合时刻（加上当前柱体跨度 1h）
        const logicalToMs = toMs + 3600000;
        return { fromMs, toMs, logicalToMs };
      },
    });

    return () => {
      if (reportTimerRef.current !== null) {
        window.clearTimeout(reportTimerRef.current);
        reportTimerRef.current = null;
      }
      window.removeEventListener('resize', resizeHandler);
      ro.disconnect();
      chart.unsubscribeCrosshairMove(crosshairHandler);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(rangeHandler);
      // 先摘出同步组并标记已销毁，避免销毁过程中仍被同步调用
      member.disposed = true;
      syncHub.members = syncHub.members.filter((item) => item !== member);
      // marker 插件先 detach，再销毁图表
      safeTimeScaleCall(() => built.onDispose?.());
      // 官方销毁：移除 canvas 与全部内部观察器/订阅，避免残留与重复 canvas
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      memberRef.current = null;
      lastBarSecRef.current = null;
      initRef.current = false;
      appliedViewKeyRef.current = null;
    };
    // build / syncHub / role / viewKey 均为调用方稳定引用（build 由 useCallback 固定）
  }, [build, scheduleReport, syncHub, followToleranceSec, role, markProgrammatic]);

  /**
   * 主题热切换（chart 级）：底色 / 网格 / 坐标轴 / 十字线。
   * series 级颜色（蜡烛、成交量柱、标记、OI 柱）由各 inner 组件自己重设 ——
   * 那些是**数据级**颜色，applyOptions 管不到。
   */
  useEffect(() => {
    /* 主题热切换必须带上 `hideTimeAxis` / `kind`：否则 applyOptions 会把
       叠放图被隐藏的时间轴又打开（`baseOptions` 缺省 visible=true），
       并把柱状图的横线 / 右轴余量按 K 线口径覆盖掉。 */
    chartRef.current?.applyOptions(baseOptions(palette, { hideTimeAxis, kind }));
  }, [palette, hideTimeAxis, kind]);

  return useMemo(
    () => ({
      containerRef,
      chartRef,
      seriesRef,
      memberRef,
      lastBarSecRef,
      followRef,
      initRef,
      appliedViewKeyRef,
      scheduleReport,
      endProgrammatic,
      markProgrammatic,
      alignPriceScales: () => alignPriceScaleWidths(syncHub),
    }),
    [scheduleReport, endProgrammatic, markProgrammatic, syncHub],
  );
}

/** 首次视窗结果：是否已按显式视窗落位、是否应跟随最新、是否需要调用方补更早历史。 */
interface InitialViewportResult {
  applied: boolean;
  followLatest: boolean;
  /**
   * 恢复视窗的起点**早于**本图当前已加载域 ⇒ 调用方应继续加载更早历史，
   * 并在每页到达后用 `ChartHandle.setViewport` 把用户视窗原样落实回来。
   */
  needsEarlierHistory: boolean;
}

/**
 * 首次视窗（官方库原生 `setVisibleRange` / `fitContent`，不重写任何坐标引擎）：
 *
 *  1. **跟随图**（OI）：优先复制**主图**（K 线）的当前时间窗（按真实 UTC 时间，不按逻辑索引）。
 *  2. **主图**（K 线）：**绝不**复制跟随图 —— 跟随图可能在自身数据到达后先做了一次
 *     `fitContent`，主图若去复制它，用户视窗就会被那个默认窗口顶掉（实测缺陷）。
 *     主图只认自己的「恢复视窗」，其次才是 `fitContent`。
 *  3. 恢复判据：**区间合法即可原样落位**，不夹到本图数据域、不自造范围。
 *     官方 `_internal_logicalRangeForTimeRange` 只在「本图完全没有时间点」时抛错，
 *     范围落在域外只是被夹到最近点 ⇒ 域外部分自然显示**空白**（缺数据就是缺数据，不假装有）。
 *     旧实现在此要求「恢复范围完全落在域内」，首屏只有一页数据时必然被拒 → 退回 `fitContent`
 *     → 用户视窗被首屏默认窗口顶掉（实测「收起→再展开」缺陷根因）。
 */
function applyInitialViewport(
  chart: IChartApi,
  syncHub: ChartSyncHub,
  member: ChartSyncMember,
  domain: { from: number; to: number } | null,
  restore: ChartViewport | null,
): InitialViewportResult {
  if (member.role === 'follower') {
    /**
     * 跟随图首屏**复制主图的可见逻辑范围**（不是真实时间范围）。
     * 两图共用同一个时间点并集网格 ⇒ 逻辑索引一一对应；按时间范围复制会因整秒取整
     * 把逻辑边界吸附成整数、barSpacing 与主图不等（实测同一时间点差最多 5.4px）。
     */
    if (alignToLeader(syncHub, member)) {
      return { applied: true, followLatest: true, needsEarlierHistory: false };
    }
  }

  if (restore) {
    const fromSec = toChartSeconds(restore.fromMs);
    const toSec = toChartSeconds(restore.toMs);
    if (toSec > fromSec) {
      syncHub.syncing = true;
      let applied = false;
      try {
        const ts = member.chart.timeScale();
        const fromIdx = ts.timeToIndex(fromSec);
        const toIdx = ts.timeToIndex(toSec);
        if (typeof fromIdx === 'number' && typeof toIdx === 'number' && toIdx > fromIdx) {
          applied = applyLogicalRangeTo(member, { from: fromIdx as unknown as Logical, to: toIdx as unknown as Logical });
        } else {
          applied = applyTimeRangeTo(member, fromSec, toSec);
        }
      } finally {
        syncHub.syncing = false;
      }
      if (applied) {
        return {
          applied: true,
          followLatest: restore.followLatest,
          needsEarlierHistory: !!domain && fromSec < domain.from,
        };
      }
    }
  }

  syncHub.syncing = true;
  try {
    /**
     * 首屏落位：**主图按自身真实数据域落位**，而不是 `fitContent()`。
     *
     * 为什么不能直接 `fitContent()`：两图共用同一套「时间点并集网格」，K 线图上会被
     * 补进大量**只有 OI 有、K 线没有**的纯 whitespace 锚点（锚点不带任何数值，只用于
     * 对齐逻辑索引）。官方库的时间轴范围取**全图所有序列**的并集，于是 K 线图的时间域
     * 被这些远端锚点撑宽 —— 实测 BTC 1m：K 线一页 500 根 ≈ 8.3 小时，OI 快照却有 4.6 天，
     * 并集 4.6 天，`fitContent()` 便按 4.6 天落位，蜡烛被压到最右侧约 4% 宽度、
     * 左侧 96% 全空（「K 线没有完整导入」的实际观感；数据本身一根不缺）。
     *
     * 修法：主图落位用**自己的 K 线域**，蜡烛铺满绘图区；时间轴点集仍是共享并集，
     * 逻辑索引对齐与跨图十字线联动全部不受影响。跟随图（OI）本就以主图为准
     * （每次数据更新都会 `alignToLeader`），两图时间窗依旧严格一致。
     */
    let landedOnOwnDomain = false;
    if (member.role === 'leader' && domain) {
      const totalPoints = member.points;
      const targetBars = 90;
      if (totalPoints > targetBars) {
        // 数据总根数较多（超 90 根）：采用交易所标准首屏落位最新 90 根，保证蜡烛饱满（约 8-10px 黄金柱宽）
        landedOnOwnDomain = applyLogicalRangeTo(member, {
          from: (totalPoints - targetBars) as Logical,
          to: (totalPoints + RIGHT_OFFSET_BARS) as Logical,
        });
      } else {
        landedOnOwnDomain = applyTimeRangeTo(member, domain.from, domain.to);
      }
    }
    if (!landedOnOwnDomain) safeTimeScaleCall(() => chart.timeScale().fitContent());
  } finally {
    syncHub.syncing = false;
  }
  return { applied: false, followLatest: true, needsEarlierHistory: false };
}

/* ------------------------------------------------------------------ *
 * K 线 + 成交量
 * ------------------------------------------------------------------ */

export interface ChartPriceLine {
  price: number;
  title: string;
  color: string;
  lineStyle?: LineStyle;
  lineWidth?: 1 | 2 | 3 | 4;
}

export interface CandleVolumeChartProps extends SharedChartProps {
  bars: Candle[];
  timeframe: Timeframe;
  priceLines?: ChartPriceLine[];
  /**
   * **可选**额外标记：与 `signalTag` 派生的标记**合并**后一起下发给官方 markers 插件。
   *
   * 为什么加（必要性）：回测工作台需要把「真实开仓 / 平仓」以及「选中那笔交易的
   * 高亮」画在同一张 K 线上，而 `signalTag` 只能表达单一圆点标记。标记数据由调用方
   * 从 `GET /backtests/{id}/chart` 的**真实** trades 构造，本组件只负责渲染。
   *
   * **默认行为不变**：不传（`undefined`）时 marker 集与扩展前**逐字一致**
   * （仅 `buildSignalMarkers(bars)`）；交易工作台等既有调用方不传该 prop。
   */
  extraMarkers?: SeriesMarker<Time>[];
  /**
   * 是否在本图内叠加成交量柱（默认 `true`，既有调用行为不变）。
   *
   * 传 `false` 用于「成交量独立成条」的版面（回测复盘页 = 设计稿的三段竖排：
   * K 线 / 成交量 / 持仓量）：此时本组件只画蜡烛，成交量由 `VolumeBarChart`
   * 在单独一条 84px 面板里画，各自有自己的右轴刻度与「成交量」标签。
   */
  showVolume?: boolean;
}

const CandleVolumeChartInner: React.FC<CandleVolumeChartProps> = ({
  bars,
  timeframe,
  priceLines,
  extraMarkers,
  gridTimes,
  viewKey,
  initialViewport,
  onViewportChange,
  onHover,
  onReady,
  syncHub,
  hideTimeAxis,
  showVolume = true,
}) => {
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  /** `build` 只在挂载时跑一次，用 ref 取最新值，避免因开关变化重建图表实例 */
  const showVolumeRef = useRef(showVolume);
  showVolumeRef.current = showVolume;
  /** 共享网格锚点序列（只含时间、不含数值；不画线、不参与价格轴） */
  const gridSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  /** 秒 → 原始 Candle，供十字线还原真实字段（不做任何推算） */
  const candleBySecRef = useRef<Map<number, Candle>>(new Map());
  /** 上一轮已下发给官方库的数据，用于判定「末根变化 / 追加一根」走 update() 而非 setData() */
  const lastCandleDataRef = useRef<CandlestickData[]>([]);
  const lastVolumeDataRef = useRef<HistogramData[]>([]);
  const lastGridRef = useRef<WhitespaceData[]>([]);
  const priceFormatRef = useRef<string>('');
  const followToleranceRef = useRef(30);
  const timeframeRef = useRef(timeframe);
  timeframeRef.current = timeframe;
  const onHoverRef = useRef(onHover);
  onHoverRef.current = onHover;

  /** 本组件的调色板（series 级颜色用；chart 级由 useChartLifecycle 统一处理） */
  const palette = useChartPalette();
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  const build = useCallback(
    (chart: IChartApi) => {
      const p = paletteRef.current;
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: p.up,
        downColor: p.down,
        borderUpColor: p.up,
        borderDownColor: p.down,
        wickUpColor: p.up,
        wickDownColor: p.down,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      // 成交量：官方 HistogramSeries，叠加在价格 pane 底部（独立 priceScale，不挤压价格轴）
      // `showVolume === false` ⇒ 本条版面由独立的 `VolumeBarChart` 承担，本图不建这条序列
      const volumeSeries = showVolumeRef.current
        ? chart.addSeries(HistogramSeries, {
            priceScaleId: 'volume',
            priceFormat: { type: 'volume' },
            priceLineVisible: false,
            lastValueVisible: false,
          })
        : null;
      if (volumeSeries) {
        volumeSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.78, bottom: 0 },
          visible: false,
        });
      }
      /**
       * 共享网格锚点：补齐到两图并集（OI 样本时点里 K 线没有的那些）。
       * 只含时间、不含数值 ⇒ 不画线、不参与价格轴，仅让两图时间点集一致。
       */
      const gridSeries = chart.addSeries(LineSeries, {
        lineVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      // 原手写 SVG 的 signalTag 圆点标记 → 官方 markers 插件
      const markers = createSeriesMarkers(candleSeries, []);
      volumeRef.current = volumeSeries;
      gridSeriesRef.current = gridSeries;
      markersRef.current = markers;
      return {
        series: candleSeries,
        onDispose: () => markers.detach(),
        onCrosshair: (param: MouseEventParams<Time>) => {
          if (!param.point || typeof param.time !== 'number') {
            onHoverRef.current(null);
            return;
          }
          const candle = candleBySecRef.current.get(param.time) ?? null;
          onHoverRef.current({ source: 'candle', timeMs: param.time * 1000, candle });
        },
      };
    },
    [],
  );

  const lifecycle = useChartLifecycle(
    build,
    followToleranceRef,
    syncHub,
    onHover,
    onReady,
    onViewportChange,
    // K 线是时间窗**主图**：首次落位只认恢复视窗/已加载数据，绝不复制 OI 的默认窗口
    'leader',
    viewKey,
    hideTimeAxis,
    'candle',
  );

  const createdPriceLinesRef = useRef<IPriceLine[]>([]);

  // 价格线绘制与生命周期联动
  useEffect(() => {
    const candleSeries = lifecycle.seriesRef.current as ISeriesApi<'Candlestick'> | null;
    if (!candleSeries) return;

    for (const pl of createdPriceLinesRef.current) {
      try {
        candleSeries.removePriceLine(pl);
      } catch {}
    }
    createdPriceLinesRef.current = [];

    if (priceLines && priceLines.length > 0) {
      for (const line of priceLines) {
        if (typeof line.price === 'number' && Number.isFinite(line.price) && line.price > 0) {
          try {
            const pl = candleSeries.createPriceLine({
              price: line.price,
              color: line.color,
              lineWidth: (line.lineWidth ?? 1) as any,
              lineStyle: line.lineStyle ?? LineStyle.Dashed,
              axisLabelVisible: true,
              title: line.title,
            });
            createdPriceLinesRef.current.push(pl);
          } catch {}
        }
      }
    }

    return () => {
      for (const pl of createdPriceLinesRef.current) {
        try {
          candleSeries.removePriceLine(pl);
        } catch {}
      }
      createdPriceLinesRef.current = [];
    };
  }, [priceLines, lifecycle.seriesRef.current, viewKey]);

  /**
   * 主题热切换：只改样式，**不重建实例**。
   *
   * 三件事必须都做，少一件就会出现「底色变了但蜡烛还是上一套颜色」：
   *   ① chart.applyOptions —— 底/网格/坐标轴/十字线；
   *   ② candleSeries.applyOptions —— 蜡烛与影线四色；
   *   ③ volumeSeries.setData + markers.setMarkers —— 柱色与标记色是**数据级**
   *      颜色，不走 applyOptions，必须重新下发。
   */
  useEffect(() => {
    const chart = lifecycle.chartRef.current;
    if (!chart) return;
    const p = palette;
    chart.applyOptions(baseOptions(p));

    const candleSeries = lifecycle.seriesRef.current as ISeriesApi<'Candlestick'> | null;
    if (candleSeries) {
      candleSeries.applyOptions({
        upColor: p.up,
        downColor: p.down,
        borderUpColor: p.up,
        borderDownColor: p.down,
        wickUpColor: p.up,
        wickDownColor: p.down,
      });
    }

    const volumeSeries = volumeRef.current;
    if (volumeSeries) {
      const volumeData = buildVolumeData(bars, p);
      volumeSeries.setData(volumeData);
      lastVolumeDataRef.current = volumeData;
    }

    const markerData = extraMarkers && extraMarkers.length > 0
      ? [...buildSignalMarkers(bars, p), ...extraMarkers].sort(
          (a, b) => Number(a.time) - Number(b.time),
        )
      : buildSignalMarkers(bars, p);
    safeTimeScaleCall(() => markersRef.current?.setMarkers(markerData));
    // 只在主题变化时跑；bars / extraMarkers 的更新由下一个 effect 负责
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette]);

  // 数据更新：不重建图表、不强制 fitContent（除首次 / 切周期外）
  useEffect(() => {
    const chart = lifecycle.chartRef.current;
    const candleSeries = lifecycle.seriesRef.current as ISeriesApi<'Candlestick'> | null;
    const volumeSeries = volumeRef.current;
    const gridSeries = gridSeriesRef.current;
    if (!chart || !candleSeries || !gridSeries) return;

    const candleData = buildCandleData(bars);
    const volumeData = buildVolumeData(bars, paletteRef.current);
    const gridData = buildGridAnchor(gridTimes, buildCandleGrid(bars));
    // 标记 = signalTag 派生的真实标记（默认唯一来源）+ 调用方传入的**可选**额外标记。
    // 两段各自升序；合并后按时间升序（官方 markers 插件要求时间升序）。
    const markerData = extraMarkers && extraMarkers.length > 0
      ? [...buildSignalMarkers(bars, paletteRef.current), ...extraMarkers].sort(
          (a, b) => Number(a.time) - Number(b.time),
        )
      : buildSignalMarkers(bars, paletteRef.current);
    const bySec = new Map<number, Candle>();
    for (const candle of bars) {
      if (Number.isFinite(candle.timestamp) && candle.timestamp > 0) {
        bySec.set(toChartSeconds(candle.timestamp), candle);
      }
    }
    candleBySecRef.current = bySec;
    const first = candleData.length ? (candleData[0].time as number) : null;
    const last = candleData.length ? (candleData[candleData.length - 1].time as number) : null;
    lifecycle.lastBarSecRef.current = last;
    if (lifecycle.memberRef.current) {
      lifecycle.memberRef.current.domain = first !== null && last !== null ? { from: first, to: last } : null;
      lifecycle.memberRef.current.points = candleData.length;
    }
    followToleranceRef.current = Math.max(30, TIMEFRAME_SECONDS[timeframeRef.current] / 2);

    // 先记录「本次更新前」的跟随意图与视窗：setData/update 会触发可见范围事件，
    // 若不先快照，followLatest 会被事件改写，实时刷新就会错误地跳回最新或错误地停在历史。
    const followBefore = lifecycle.followRef.current;
    const previousRange = chart.timeScale().getVisibleRange();

    /**
     * 切币 / 切周期：整表替换，强制走 setData（并保留既有视窗恢复逻辑）。
     *
     * 这类「重置」会让官方库派发一串中间可见范围事件 —— 进入静默窗，
     * 避免它们被当成用户视窗上报（切周期时会把旧范围写到新视图键上）。
     * 同周期内的**实时增量刷新**不进入静默窗：那只是末根 update，
     * 视窗读数应继续正常上报（否则页面上的「视窗 …–…」会停更）。
     */
    const viewChanged = lifecycle.appliedViewKeyRef.current !== viewKey;
    if (viewChanged) {
      lifecycle.appliedViewKeyRef.current = viewKey;
      lifecycle.initRef.current = false;
      lifecycle.markProgrammatic();
    }

    const format = inferPriceFormat(candleData);
    const formatKey = `${format.precision}:${format.minMove}`;
    if (priceFormatRef.current !== formatKey) {
      priceFormatRef.current = formatKey;
      candleSeries.applyOptions({ priceFormat: { type: 'price', ...format } });
    }

    // 网格锚点先落位，再落真实序列：保证同一轮里两图时间点集一致
    applySeriesUpdate<WhitespaceData>(gridSeries, lastGridRef.current, gridData, viewChanged);
    lastGridRef.current = gridData;

    // 增量更新：末根变化 / 追加一根 → update()（官方库不会重置可见范围）；
    // 历史重置（切周期、回填更早历史、未收盘根消失、前缀变化）→ setData()。
    applySeriesUpdate<CandlestickData | WhitespaceData>(candleSeries, lastCandleDataRef.current, candleData, viewChanged);
    // `showVolume === false` 时本图没有成交量序列，只在存在时下发（独立成交量面板自行负责）
    if (volumeSeries) {
      applySeriesUpdate<HistogramData | WhitespaceData>(volumeSeries, lastVolumeDataRef.current, volumeData, viewChanged);
      lastVolumeDataRef.current = volumeData;
    }
    lastCandleDataRef.current = candleData;

    // signalTag 官方 marker：只保留真实存在的标注根，不推断、不伪造
    safeTimeScaleCall(() => markersRef.current?.setMarkers(markerData));

    // 首次落位必须等到「本图确有真实时间点」：空数据时官方时间轴无点可映射，
    // 任何 `setVisibleRange` / `fitContent` 都非法（实测抛 `Value is null` 打挂渲染），
    // 而且会把唯一一次「按恢复视窗落位」的机会白白消耗掉。
    if (!lifecycle.initRef.current) {
      if (first === null || last === null) return;
      lifecycle.initRef.current = true;
      const initial = applyInitialViewport(
        chart,
        syncHub,
        lifecycle.memberRef.current as ChartSyncMember,
        { from: first, to: last },
        initialViewport,
      );
      lifecycle.followRef.current = initial.followLatest;
      lifecycle.alignPriceScales();
      /**
       * 结束程序化阶段，但**不在这里读取范围**：官方库的落位异步生效，
       * 立即读取会拿到落位前的旧范围，导致状态位与画布刻度互相矛盾（实测缺陷）。
       * 改为放开静默窗后交给去抖上报 —— 由原生可见范围事件驱动，
       * 250ms 后读到的已是落实后的范围。
       */
      lifecycle.endProgrammatic();
      lifecycle.scheduleReport();
      return;
    }

    // 只在「本图确有真实时间点」时才调用时间轴 API —— 空数据时官方库会抛
    // `Value is null`（时间轴无点可映射），必须避免。
    const currentMember = lifecycle.memberRef.current;
    if (currentMember && canSetVisibleRange(currentMember)) {
      if (followBefore) {
        // 仍在跟随最新：只把右缘对齐到最新根，不改变缩放级别（绝不 fitContent）
        safeTimeScaleCall(() => chart.timeScale().scrollToRealTime());
      } else if (previousRange) {
        // 用户已拖到历史视窗 / 正在加载更早历史：原样保持当前视窗
        safeTimeScaleCall(() => chart.timeScale().setVisibleRange(previousRange));
      }
    }
    // 以本次更新前的意图为准写回，避免被 setData/update 期间的范围事件污染
    lifecycle.followRef.current = followBefore;
    lifecycle.alignPriceScales();
    // 实时刷新/追加历史：仍走静默窗内的去抖上报（程序化范围变化不写缓存）
    lifecycle.scheduleReport();
  }, [bars, timeframe, gridTimes, viewKey, initialViewport, syncHub, lifecycle, extraMarkers]);

  return (
    <div className="relative w-full h-full overflow-hidden">
      {/*
        `hideTimeAxis` ⇒ 容器比外层高出一个时轴高度，由外层 overflow-hidden 裁掉
        （官方 `timeScale.visible` 在本版本是空选项，见 `applyTimeAxisClip`）。
        非叠放场景（缺省）仍是 `h-full`，行为与既有调用完全一致。
      */}
      <div
        ref={lifecycle.containerRef}
        className="w-full"
        style={{ height: hideTimeAxis ? 'calc(100% + var(--tz-axis-h, 28px))' : '100%' }}
        data-chart="lightweight-candles"
      />
    </div>
  );
};

/**
 * memo 化：调用方的视窗上报会触发父组件重渲染，这里必须**零重渲染**，
 * 否则会形成「上报 → 重渲染 → 再上报」的重绘循环。
 */
export const CandleVolumeChart = React.memo(CandleVolumeChartInner);
CandleVolumeChart.displayName = 'CandleVolumeChart';

/* ------------------------------------------------------------------ *
 * OI 总量柱形图（官方 HistogramSeries：真实柱高 + 共享时间网格）
 * ------------------------------------------------------------------ */

interface OiBarChartProps extends SharedChartProps {
  points: OIPoint[];
  unit: 'amount' | 'usd';
  /** 无数据时的空态文案（沿用原版措辞，不用实时值伪造历史） */
  emptyText: string;
}

const OiBarChartInner: React.FC<OiBarChartProps> = ({
  points,
  unit,
  gridTimes,
  emptyText,
  viewKey,
  initialViewport,
  onViewportChange,
  onHover,
  onReady,
  syncHub,
}) => {
  const oiBySecRef = useRef<Map<number, OIPoint>>(new Map());
  /** 上一轮已下发的柱数据，用于判定增量更新 */
  const lastBarsRef = useRef<HistogramData[]>([]);
  /** 上一轮已下发的网格锚点数据 */
  const lastGridRef = useRef<WhitespaceData[]>([]);
  /** 当前图表实例上的柱序列（随图表重挂载而重建） */
  const barSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const boundChartRef = useRef<IChartApi | null>(null);
  /** 上一轮已下发的价格格式档位（精度:最小变动），避免每轮重复 applyOptions */
  const lastFormatKeyRef = useRef<string>('');
  /** 相邻快照的真实间隔（秒，取中位数），用于判定「是否仍在跟随最新」 */
  const stepSecRef = useRef<number>(60);
  const followToleranceRef = useRef(30);
  const unitRef = useRef(unit);
  unitRef.current = unit;
  const onHoverRef = useRef(onHover);
  onHoverRef.current = onHover;

  /** 调色板（OI 柱色是数据级颜色，主题变化需重新下发 setData） */
  const palette = useChartPalette();
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  const build = useCallback(
    (chart: IChartApi) => {
      /**
       * 网格锚点序列：只含时间、不含数值（whitespace）。
       * 官方 `LinePaneViewBase` 只遍历有效点，故它**不画任何线**；
       * 但它会让本图时间轴点集补齐到两图共享并集，使两图横坐标映射一致。
       */
      const gridSeries = chart.addSeries(LineSeries, {
        lineVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      return {
        series: gridSeries,
        onCrosshair: (param: MouseEventParams<Time>) => {
          if (!param.point || typeof param.time !== 'number') {
            onHoverRef.current(null);
            return;
          }
          const point = oiBySecRef.current.get(param.time) ?? null;
          onHoverRef.current({ source: 'oi', timeMs: param.time * 1000, candle: null, oiPoint: point });
        },
      };
    },
    [],
  );

  /**
   * 主题热切换：OI 柱色写在**数据**里（buildOiBars 逐点给色），
   * applyOptions 改不到，必须整表重下。数值未变，只是颜色换了。
   */
  useEffect(() => {
    const barSeries = barSeriesRef.current;
    if (!barSeries) return;
    const { bars } = buildOiBars(points, unit, palette);
    barSeries.setData(bars);
    lastBarsRef.current = bars;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette]);

  const lifecycle = useChartLifecycle(
    build,
    followToleranceRef,
    syncHub,
    onHover,
    onReady,
    onViewportChange,
    // OI 是时间窗**跟随图**：首次落位优先复制主图（K 线）的当前时间窗
    'follower',
    viewKey,
    // OI 面板显示自己的时间轴（它是叠放三段里最底的那一条）
    false,
    'oi',
  );

  /**
   * 柱形基线（`HistogramSeries.base`）——修复「OI 柱形没有高低波浪，看不出量」。
   *
   * **缺陷**：官方 `HistogramSeries` 的 `base` **默认 0**，柱从 0 画到该时点总量。
   * 而 OI 是**绝对存量**（BTC 91.6k~98.8k），可视窗内的相对波动本来就极小 ——
   * 实测当前 3 小时窗只有 11 个样本、区间 585（**0.64%**）。以 0 为基线时柱高落在
   * 92.7%~100%，差异仅 **7.3 个百分点**，视觉上就是一排等高栅栏，量级完全读不出来。
   *
   * **修法**：`base` 取**当前可视窗内真实柱值**的下缘（再低 12% 余量），使窗内波动铺满
   * 绘图区。轴刻度仍是**真实总量**（不缩放、不改数值、不聚合、不重采样、不伪造时点），
   * 只是把「柱的起点」从 0 换成窗内下缘 —— 与「OI 看图看的是变化」的读图目的相符。
   *
   * **为什么必须跟随可视窗重算**：
   *  · 若固定用全量（4.6 天）下缘做基线，3 小时窗的波动只占柱高 8%，仍然近齐平；
   *  · 且平移到数值更低的窗口时，柱会落到基线以下（负柱），图形直接错。
   * 本重算只改**价格轴**（右轴）刻度，不触碰时间轴范围，故不会与两图同步形成回环。
   */
  const oiBaseRef = useRef<number | null>(null);
  const recomputeOiBase = useCallback(() => {
    const chart = lifecycle.chartRef.current;
    const series = barSeriesRef.current;
    if (!chart || !series) return;
    if (lifecycle.memberRef.current?.disposed) return;
    const bars = lastBarsRef.current;
    if (bars.length === 0) return;

    const range = safeCall(() => chart.timeScale().getVisibleRange());
    const from = range && typeof range.from === 'number' ? (range.from as number) : null;
    const to = range && typeof range.to === 'number' ? (range.to as number) : null;

    let min = Infinity;
    let max = -Infinity;
    const scan = (useWindow: boolean) => {
      for (const bar of bars) {
        const time = bar.time as number;
        if (useWindow && from !== null && to !== null && (time < from || time > to)) continue;
        const value = bar.value;
        if (!Number.isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
      }
    };
    if (from !== null && to !== null) scan(true);
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      // 视窗内一个真实样本都没有（例如刚切周期）→ 回落到全量已载入数据
      min = Infinity;
      max = -Infinity;
      scan(false);
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return;

    const span = max - min;
    // 全窗同值（span = 0）时退回「略低于该值」，避免柱高归零
    const base = span > 0 ? min - span * OI_BASE_MARGIN : min * (1 - 1e-3);
    const prev = oiBaseRef.current;
    // 变化不足 0.02% ⇒ 不下发，避免拖动过程中无谓的整图重绘
    if (prev !== null && Math.abs(base - prev) <= Math.abs(prev) * 2e-4) return;
    oiBaseRef.current = base;
    safeCall(() => series.applyOptions({ base }));
  }, [lifecycle]);

  /**
   * 可视窗变化 → 重算柱形基线。
   *
   * 只订阅**时间轴**的范围事件：柱形基线仅影响右价格轴刻度，不会反向改变时间轴范围，
   * 因此本订阅不可能形成回环。图表实例随 `viewKey` 重挂载（React key），本 effect 亦随之
   * 重建，不存在悬挂订阅。
   */
  useEffect(() => {
    const chart = lifecycle.chartRef.current;
    if (!chart) return;
    const handler = () => recomputeOiBase();
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
  }, [lifecycle, recomputeOiBase]);

  useEffect(() => {
    const chart = lifecycle.chartRef.current;
    const gridSeries = lifecycle.seriesRef.current as ISeriesApi<'Line'> | null;
    if (!chart || !gridSeries) return;

    // 本 effect 内全部为程序化写入（分段 setData / 网格 setData / 视窗保持）→ 先进入静默窗
    lifecycle.markProgrammatic();

    // 图表实例更换（重挂载）→ 旧的柱序列句柄已随 chart.remove() 失效，必须重建
    if (boundChartRef.current !== chart) {
      boundChartRef.current = chart;
      barSeriesRef.current = null;
      lastBarsRef.current = [];
      lastGridRef.current = [];
      // 新实例上的柱序列是全新的（base 回到库默认 0）→ 基线缓存一并作废，等下一轮重算
      oiBaseRef.current = null;
      // 价格格式同理：新序列没有继承任何 priceFormat，档位缓存必须作废，
      // 否则「档位没变」会让下面的 applyOptions 整段跳过，轴又退回原始数字
      lastFormatKeyRef.current = '';
    }

    const { bars, sampleTimes } = buildOiBars(points, unit, paletteRef.current);
    const gridData = buildGridAnchor(gridTimes, sampleTimes);
    const bySec = new Map<number, OIPoint>();
    for (const point of points) {
      /**
       * 键必须用**柱实际落位的时点**（对齐后 = `alignedTimestamp`），否则十字线命中的是
       * 「对齐后的柱时点」，而这里按真实快照时点建键 ⇒ 读数取不到值。
       * 取到的 `point` 仍是**真实样本对象**：读数条显示真实快照时间与数值。
       */
      const ms = point.alignedTimestamp ?? point.timestamp;
      if (Number.isFinite(ms) && ms > 0) {
        bySec.set(toChartSeconds(ms), point);
      }
    }
    oiBySecRef.current = bySec;

    // 本图真实数据域 = 真实样本 ∪ 网格锚点（升序去重后的最小/最大时点）
    let first: number | null = null;
    let last: number | null = null;
    const track = (sec: number) => {
      if (first === null || sec < first) first = sec;
      if (last === null || sec > last) last = sec;
    };
    for (const sec of sampleTimes) track(sec);
    for (const item of gridData) track(item.time as number);
    lifecycle.lastBarSecRef.current = last;
    if (lifecycle.memberRef.current) {
      lifecycle.memberRef.current.domain = first !== null && last !== null ? { from: first, to: last } : null;
      lifecycle.memberRef.current.points = gridData.length;
    }
    // 真实间隔中位数：不把「最后 24 条」当「近 24 小时」，也不假设固定周期
    if (sampleTimes.length >= 2) {
      const diffs: number[] = [];
      for (let i = 1; i < sampleTimes.length; i += 1) diffs.push(sampleTimes[i] - sampleTimes[i - 1]);
      diffs.sort((a, b) => a - b);
      stepSecRef.current = diffs[Math.floor(diffs.length / 2)] || 60;
    }
    followToleranceRef.current = Math.max(60, stepSecRef.current / 2);

    // 先记录当前视窗：实时刷新不得让视窗跳回
    const previousRange = chart.timeScale().getVisibleRange();
    const viewChanged = lifecycle.appliedViewKeyRef.current !== viewKey;
    if (viewChanged) {
      lifecycle.appliedViewKeyRef.current = viewKey;
      lifecycle.initRef.current = false;
    }

    // 柱序列：图表实例上不存在则创建（柱高=该时点总量；柱色由 buildOiBars 统一给成
    // COLOR.oiBar，逐点下发但取值恒等，不承载方向 / 增减语义）
    if (!barSeriesRef.current) {
      barSeriesRef.current = chart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        lastValueVisible: true,
      });
      /**
       * 顶部留 8% 余量：价格轴最高刻度正好落在绘图区顶边时，官方库把刻度文字
       * **以刻度值为中心**绘制 ⇒ 半截字被面板上边界切掉（实测 OI 面板顶部
       * 出现过被切一半的「1.25亿」）。留出余量后最高刻度落在框内。
       */
      // OI 价格轴不加额外边距：柱形基线（base）本身就是「窗内下缘再低 12%」，
      // 再叠边距会让刻度区间被压得过窄、库选不出刻度（实测轴上一个标签都没有）。
      barSeriesRef.current.priceScale().applyOptions({
        scaleMargins: { top: 0, bottom: 0 },
      });
    }
    const barSeries = barSeriesRef.current;

    // 价格格式：只在精度档位变化时下发，避免无谓的选项抖动。
    //
    // 2026-10-12（用户指令「持仓数量单位按万跟亿计算，不要全部显示阿拉伯数字」）：
    // OI 柱形的纵轴刻度 / 末值标签 / 十字线标签一律走 **万·亿** 口径
    // （官方 `type:'custom'` + formatter），不再把 1234567.89 这样的原始阿拉伯数字铺到轴上。
    // 数量（张/标的币）与名义额（USD）两种模式同口径；读数条本来就已是万/亿，此处与之对齐。
    // 只改**显示**，不改数值：柱高与读数仍是来源原值，不做任何换算。
    const values: number[] = [];
    for (const bar of bars) values.push(bar.value);
    const format = inferValueFormat(values);
    const formatKey = `${format.precision}:${format.minMove}:wanyi`;
    if (lastFormatKeyRef.current !== formatKey) {
      lastFormatKeyRef.current = formatKey;
      const priceFormat = {
        type: 'custom' as const,
        minMove: format.minMove,
        formatter: (price: number) => formatWanYi(price, '', 2),
      };
      barSeries.applyOptions({ priceFormat });
      /**
       * ⚠ 价格轴的格式**只认一个序列**，必须同时下发给网格锚点序列。
       *
       * 官方库 `PriceScale._internal_updateFormatter()` 的取法是：
       * 遍历该价格轴上的数据源，取 **zorder 最小**者作 `formatterSource`；
       * 同 zorder（默认全为 0）时按**加入顺序**先到先得。本图的网格锚点序列
       * 是在 `build()` 里先 `addSeries` 的，因此它才是格式来源。
       *
       * 它只承载 whitespace（没有任何数值），不给它同一份 `priceFormat` 时，
       * 柱序列上设的格式**永远不会出现在轴上** —— 实测切到「名义额 USD」后
       * 轴刻度仍是 `7745111887.20`、末值标签 `7680173922.62` 这样的原始数字，
       * 直接违反「按万/亿显示」口径（2026-10-17 修复）。
       * 网格序列不可见且无值，设格式对它自身零影响。
       */
      gridSeries.applyOptions({ priceFormat });
    }

    // 网格锚点先落位，再落真实柱（柱形天然断柱：缺口/空值处没有柱，无需分段）
    applySeriesUpdate<WhitespaceData>(gridSeries, lastGridRef.current, gridData, viewChanged);
    lastGridRef.current = gridData;
    applySeriesUpdate<HistogramData>(barSeries, lastBarsRef.current, bars, viewChanged);
    lastBarsRef.current = bars;
    // 柱值已更新 → 按新的可视窗重算基线（首次落位前视窗可能尚未就绪，下面还会再算一次）
    recomputeOiBase();

    // 同 K 线图：必须等到本图确有真实时间点才做首次落位（空时间轴调用官方 API 会抛错）
    if (!lifecycle.initRef.current) {
      if (first === null || last === null) return;
      lifecycle.initRef.current = true;
      applyInitialViewport(
        chart,
        syncHub,
        lifecycle.memberRef.current as ChartSyncMember,
        { from: first, to: last },
        initialViewport,
      );
      lifecycle.alignPriceScales();
      return;
    }

    /**
     * OI 图是时间窗的**跟随方**：数据更新后必须**重新对齐主图当前范围**，
     * 绝不 scrollToRealTime / fitContent —— 否则会把主图（K 线）拉回最新。
     *
     * 为什么不能沿用「本图先前的可见范围」：跟随图的 `setData` 会让官方库按**新点集**
     * 重算它自己的范围（切周期/补更早历史后尤其明显），此时把它自己那份过期范围写回去，
     * 就会覆盖掉主图刚同步过来的窗口 —— 实测切周期/补历史后 1m 上两图相差 1576px。
     * 因此改为「对齐主图当前逻辑范围」；只有主图暂无范围时才回落到保持自身范围。
     *
     * 本图不调用 `scheduleReport`：它的视窗上报通道是 `noopViewportChange`，
     * 而且 follower 的范围变化被同步层禁止反向驱动主图（见 `rangeHandler`）。
     */
    const currentMember = lifecycle.memberRef.current;
    if (currentMember && canSetVisibleRange(currentMember)) {
      if (!alignToLeader(syncHub, currentMember) && previousRange) {
        safeTimeScaleCall(() => chart.timeScale().setVisibleRange(previousRange));
      }
    }
    lifecycle.alignPriceScales();
  }, [points, unit, gridTimes, viewKey, initialViewport, syncHub, lifecycle, recomputeOiBase]);

  return (
    <div className="relative w-full h-full">
      <div ref={lifecycle.containerRef} className="w-full h-full" data-chart="lightweight-oi" />
      {points.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="text-[13px] text-neutral-400">{emptyText}</span>
        </div>
      )}
    </div>
  );
};

/** memo 化：理由同 `CandleVolumeChart`（阻断「视窗上报 → 重渲染」的重绘循环）。 */
export const OiBarChart = React.memo(OiBarChartInner);
OiBarChart.displayName = 'OiBarChart';

/* ------------------------------------------------------------------ *
 * 成交量柱形图（独立面板：设计稿「成交量」那一条）
 * ------------------------------------------------------------------ */

interface VolumeBarChartProps extends SharedChartProps {
  bars: Candle[];
  /** 无数据时的空态文案（不伪造柱子） */
  emptyText: string;
}

/**
 * 独立成交量面板。
 *
 * **为什么要单独一条**：设计稿（`gm-layout.html` 回测复盘中栏）是三段竖排 ——
 * `K 线`（clamp(250px,34vh,330px)）/ `成交量`（84px）/ `持仓量（OI）`（92px），
 * 每段各带自己的左标签与右轴刻度，只有最底那条有日期行。
 * 原实现把成交量以 `scaleMargins:{top:0.78}` 叠在 K 线 pane 底部 —— 与设计稿
 * 「独立一条、有自己的 10.0万 / 0 刻度」不一致（叠放时右轴刻度被价格轴吃掉）。
 *
 * **数据口径不变**：柱高 = 该根**原始 base 量**（`Candle.volume`，不是 USDT 名义额），
 * 颜色沿用涨绿跌红（与蜡烛同一套调色板），未收盘根半透明 —— 全部复用
 * `buildVolumeData`，不新造任何数值。
 *
 * 纵轴刻度走**万·亿**口径（设计稿写的是 `10.0万`），只改显示、不改数值。
 */
const VolumeBarChartInner: React.FC<VolumeBarChartProps> = ({
  bars,
  gridTimes,
  emptyText,
  viewKey,
  initialViewport,
  onViewportChange,
  onHover,
  onReady,
  syncHub,
}) => {
  const candleBySecRef = useRef<Map<number, Candle>>(new Map());
  const lastBarsRef = useRef<HistogramData[]>([]);
  const lastGridRef = useRef<WhitespaceData[]>([]);
  const barSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const boundChartRef = useRef<IChartApi | null>(null);
  const lastFormatKeyRef = useRef<string>('');
  /** 本图为跟随方，容差只影响「是否跟随最新」的判定，取固定 60s 即可 */
  const followToleranceRef = useRef(60);
  const onHoverRef = useRef(onHover);
  onHoverRef.current = onHover;

  const palette = useChartPalette();
  const paletteRef = useRef(palette);
  paletteRef.current = palette;

  const build = useCallback(
    (chart: IChartApi) => {
      /**
       * 网格锚点序列：只含时间、不含数值。
       * 作用是让本图时间点集补齐到三图共享并集，横坐标与 K 线严格对齐
       * （与 OI 图同一机制，理由见 `buildGridAnchor`）。
       */
      const gridSeries = chart.addSeries(LineSeries, {
        lineVisible: false,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      return {
        series: gridSeries,
        onCrosshair: (param: MouseEventParams<Time>) => {
          if (!param.point || typeof param.time !== 'number') {
            onHoverRef.current(null);
            return;
          }
          const candle = candleBySecRef.current.get(param.time) ?? null;
          // 悬停成交量条 ⇒ 读数条仍显示该根的 OHLC / 量（与 K 线同一份真实对象）
          onHoverRef.current({ source: 'candle', timeMs: param.time * 1000, candle });
        },
      };
    },
    [],
  );

  /** 主题热切换：柱色写在数据里（涨绿跌红），applyOptions 改不到 ⇒ 整表重下 */
  useEffect(() => {
    const barSeries = barSeriesRef.current;
    if (!barSeries) return;
    const data = buildVolumeData(bars, palette);
    barSeries.setData(data);
    lastBarsRef.current = data;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette]);

  const lifecycle = useChartLifecycle(
    build,
    followToleranceRef,
    syncHub,
    onHover,
    onReady,
    onViewportChange,
    // 成交量条是时间窗**跟随图**（同 OI）：首次落位优先复制主图（K 线）的时间窗
    'follower',
    viewKey,
    // 它上面还有 OI 一条 ⇒ 日期行只留最底那张（设计稿只有一行）
    true,
    'volume',
  );

  useEffect(() => {
    const chart = lifecycle.chartRef.current;
    const gridSeries = lifecycle.seriesRef.current as ISeriesApi<'Line'> | null;
    if (!chart || !gridSeries) return;

    lifecycle.markProgrammatic();

    if (boundChartRef.current !== chart) {
      boundChartRef.current = chart;
      barSeriesRef.current = null;
      lastBarsRef.current = [];
      lastGridRef.current = [];
      lastFormatKeyRef.current = '';
    }

    const volumeData = buildVolumeData(bars, paletteRef.current);
    const gridData = buildGridAnchor(gridTimes, buildCandleGrid(bars));

    const bySec = new Map<number, Candle>();
    for (const candle of bars) {
      if (Number.isFinite(candle.timestamp) && candle.timestamp > 0) {
        bySec.set(toChartSeconds(candle.timestamp), candle);
      }
    }
    candleBySecRef.current = bySec;

    // 本图真实数据域 = 真实成交量根 ∪ 网格锚点
    let first: number | null = null;
    let last: number | null = null;
    const track = (sec: number) => {
      if (first === null || sec < first) first = sec;
      if (last === null || sec > last) last = sec;
    };
    for (const item of volumeData) track(item.time as number);
    for (const item of gridData) track(item.time as number);
    lifecycle.lastBarSecRef.current = last;
    if (lifecycle.memberRef.current) {
      lifecycle.memberRef.current.domain = first !== null && last !== null ? { from: first, to: last } : null;
      lifecycle.memberRef.current.points = gridData.length;
    }

    const previousRange = chart.timeScale().getVisibleRange();
    const viewChanged = lifecycle.appliedViewKeyRef.current !== viewKey;
    if (viewChanged) {
      lifecycle.appliedViewKeyRef.current = viewKey;
      lifecycle.initRef.current = false;
    }

    if (!barSeriesRef.current) {
      barSeriesRef.current = chart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        lastValueVisible: false,
      });
      /**
       * 上下各留一点余量：柱形从 `0` 起画，`0` 正好落在绘图区底边时，
       * 官方库把刻度文字**以刻度值为中心**绘制 ⇒ 最底下那个刻度（0）会被
       * 面板下边界切掉一半（实测可见半截字）。留 10% 底边距后 0 落在框内。
       */
      barSeriesRef.current.priceScale().applyOptions({
        scaleMargins: { top: 0.16, bottom: 0.12 },
      });
    }
    const barSeries = barSeriesRef.current;

    // 纵轴刻度：设计稿「10.0万 / 0」⇒ 万·亿 口径（1 位小数，只改显示不改数值）
    const values: number[] = [];
    for (const item of volumeData) values.push(item.value);
    const format = inferValueFormat(values);
    const formatKey = `${format.precision}:${format.minMove}:wanyi1`;
    if (lastFormatKeyRef.current !== formatKey) {
      lastFormatKeyRef.current = formatKey;
      const priceFormat = {
        type: 'custom' as const,
        minMove: format.minMove,
        formatter: (value: number) => formatWanYi(value, '', 1),
      };
      barSeries.applyOptions({ priceFormat });
      // 价格轴格式只认 zorder 最小（此处为网格锚点序列）⇒ 必须同步下发
      gridSeries.applyOptions({ priceFormat });
    }

    applySeriesUpdate<WhitespaceData>(gridSeries, lastGridRef.current, gridData, viewChanged);
    lastGridRef.current = gridData;
    applySeriesUpdate<HistogramData>(barSeries, lastBarsRef.current, volumeData, viewChanged);
    lastBarsRef.current = volumeData;

    if (!lifecycle.initRef.current) {
      if (first === null || last === null) return;
      lifecycle.initRef.current = true;
      applyInitialViewport(
        chart,
        syncHub,
        lifecycle.memberRef.current as ChartSyncMember,
        { from: first, to: last },
        initialViewport,
      );
      lifecycle.alignPriceScales();
      return;
    }

    // 跟随方：数据更新后重新对齐主图当前逻辑范围（绝不 scrollToRealTime / fitContent）
    const currentMember = lifecycle.memberRef.current;
    if (currentMember && canSetVisibleRange(currentMember)) {
      if (!alignToLeader(syncHub, currentMember) && previousRange) {
        safeTimeScaleCall(() => chart.timeScale().setVisibleRange(previousRange));
      }
    }
    lifecycle.alignPriceScales();
  }, [bars, gridTimes, viewKey, initialViewport, syncHub, lifecycle]);

  return (
    <div className="relative w-full h-full overflow-hidden">
      {/* 叠放中间层：容器多出一个时轴高度，由外层裁掉（见 `applyTimeAxisClip`） */}
      <div
        ref={lifecycle.containerRef}
        className="w-full"
        style={{ height: 'calc(100% + var(--tz-axis-h, 28px))' }}
        data-chart="lightweight-volume"
      />
      {bars.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="text-[10px] text-neutral-400">{emptyText}</span>
        </div>
      )}
    </div>
  );
};

/** memo 化：理由同 `CandleVolumeChart`。 */
export const VolumeBarChart = React.memo(VolumeBarChartInner);
VolumeBarChart.displayName = 'VolumeBarChart';
