/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 单策略工作台 · **执行视图**：真实 15m K 线 + 原始成交量 + 真实信号 + 开平仓标记 +
 * 保护止损轨迹。选中一笔交易 ⇒ 用现有 `ChartHandle.setViewport` 定位其 entry/exit 区间，
 * 并高亮该笔标记。
 *
 * 纪律
 * ────
 * · K 线/成交量/信号/开平仓/止损轨迹**全部**来自 `GET /backtests/{id}/chart` 的真实字段，
 *   前端不重算任何成交或止损。
 * · `v` 是**原始 base 成交量**（不是 USDT 名义额）⇒ 只填 `Candle.volume`，
 *   `volumeUsd` 保持 `null`（不用名义额冒名）。
 * · 止损轨迹用**现有 `onReady` 句柄**叠加官方 `LineSeries`（不重写 K 线绘制引擎）；
 *   不使用 SVG 假 K 线、不使用静态 demo。
 * · 没有固定止盈规则时明确显示「未配置固定止盈」，**不造 TP 点**。
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  LineSeries,
  LineStyle,
  LineType,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import {
  CandleVolumeChart,
  OiBarChart,
  VolumeBarChart,
  buildCandleGrid,
  createChartSyncHub,
  toChartSeconds,
  type ChartHandle,
  type ChartHover,
  type ChartViewport,
} from './LightweightChart';
import type { Candle, OIPoint, Timeframe } from '../types/crypto';
import { alignOiToTimeframe } from '../api/adapters';
import type { LabChart, LabChartEvent, LabChartTrade, LabOrderRow } from '../api/labBacktest';
import { useChartPalette, withAlpha, type ChartPalette } from '../design/chartTheme';
/* K 线归一化：数组元组 / 对象两种线上形态都收，唯一实现在 utils/candles.ts */
import { normalizeCandle } from '../utils/candles';
import { fmtPrice } from '../utils/labFormat';
import { exitReasonLabel } from '../utils/runMeta';
import { nearestByTime, type CrosshairBus } from '../utils/crosshairBus';

/* ── 格式化（未知一律 '—'）────────────────────────────────────────────────── */
const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtPct = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const fmtUtc = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

const fmtVolume = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(3)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(3)}K`;
  return v.toFixed(4);
};

/** OI 总持仓量（标的基础币张数）。万/亿分档，读起来比 92,472,818 直观。 */
const fmtOi = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(3)} 亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
};

/* ══════════════════ 执行视图周期选择（日线 – 5分线）═════════════════════════
 *
 * 事实边界（不得含糊）：
 * · 本 run 的图表载荷 `GET /backtests/{id}/chart` **只有执行周期（15m）的真实 K 线**。
 * · 比执行周期**更粗**的周期，由这些真实 15m K 线按 **UTC 边界精确聚合**得到：
 *     O = 桶内首根 open，H = 桶内 max(high)，L = 桶内 min(low)，C = 桶内末根 close，
 *     V = 桶内 volume 求和，t = 桶起点。
 *   这是这些周期的**定义式**算法（不是均值、不是插值、不改时间戳），成交量精确可加。
 * · 比执行周期**更细**的 5m **无法**从 15m 还原 ⇒ 明确禁用并给出原因（不做假图）。
 *
 * 聚合只是「换个尺度看同一份真实数据」，标记（开平仓/订单/止损轨迹）仍落在**真实时点**上。
 */
const TF_MS: Record<Timeframe, number> = {
  '5m': 300_000,
  '15m': 900_000,
  '30m': 1_800_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '8h': 28_800_000,
  '12h': 43_200_000,
  '1d': 86_400_000,
};

/**
 * 「用户停手多久之后，才把当前视窗当成用户视窗回写给父组件」。
 *
 * 400ms 的取值依据（BACKTEST-CHART-JITTER-FIX-01）：
 *  · 视窗上报本身已经是 250ms 去抖（`LightweightChart.scheduleReport`）；
 *  · 再留 400ms ⇒ 连续拖动/滚轮期间不会有任何回写，手指停下 0.65s 后才落一次；
 *  · 不影响深链恢复（`?vf=&vt=`）与「手动视窗已恢复」徽标，它们只在停手后更新。
 */
const VIEWPORT_SETTLE_MS = 400;

/**
 * 判定「当前视窗已经是目标视窗」的容差（毫秒）。
 *
 * `manualViewport` 的取值就是本图 `visibleRange()` 上报出去的那两个毫秒值，理论上
 * 逐位相等；留 500ms 只是吸收「上报 → 存储 → 回传」链路上可能出现的时间戳取整
 * （`getVisibleRange()` 只精确到整秒），不引入任何可见偏差。
 */
const VIEWPORT_SAME_TOLERANCE_MS = 500;

export interface LabChartTfOption {
  value: Timeframe;
  label: string;
  enabled: boolean;
  reason?: string;
}
/** 周期选项：日线 → 5分线（与全站 `Timeframe` 同一套值，不做第二套命名）。 */
export const LAB_CHART_TF_OPTIONS: LabChartTfOption[] = [
  { value: '1d', label: '日线', enabled: true },
  { value: '12h', label: '12h', enabled: true },
  { value: '8h', label: '8h', enabled: true },
  { value: '4h', label: '4h', enabled: true },
  { value: '1h', label: '1h', enabled: true },
  { value: '30m', label: '30m', enabled: true },
  { value: '15m', label: '15m', enabled: true },
  {
    value: '5m',
    label: '5分线',
    enabled: false,
    reason: '本 run 的执行周期是 15m，比它更细的 5m 无法从 15m 还原（不插值、不合成）⇒ 禁用',
  },
];

/** 从连续蜡烛的时间差推断真实周期，防止用错周期基础误聚合 */
export function detectCandleTimeframe(bars: Candle[]): Timeframe | null {
  if (bars.length < 2) return null;
  const diff = bars[1].timestamp - bars[0].timestamp;
  if (diff >= 86_000_000 && diff <= 87_000_000) return '1d';
  if (diff >= 43_000_000 && diff <= 44_000_000) return '12h';
  if (diff >= 28_000_000 && diff <= 29_000_000) return '8h';
  if (diff >= 14_000_000 && diff <= 15_000_000) return '4h';
  if (diff >= 3_500_000 && diff <= 3_700_000) return '1h';
  if (diff >= 1_700_000 && diff <= 1_900_000) return '30m';
  if (diff >= 850_000 && diff <= 950_000) return '15m';
  return null;
}

/**
 * 把基础周期 K 线按 UTC 边界聚合到更粗周期。
 * 目标周期 ≤ 基础周期时**原样返回**（不反向合成、不编造细粒度）。
 */
export function bucketCandles(base: Candle[], baseTf: string, targetTf: string): Candle[] {
  if (!base.length || targetTf === baseTf) return base;
  const step = TF_MS[targetTf as Timeframe];
  const baseStep = TF_MS[baseTf as Timeframe];
  if (!step || !baseStep || step <= baseStep) return base;

  const out: Candle[] = [];
  let cur: Candle | null = null;
  let curBucket = Number.NaN;
  for (const b of base) {
    if (!Number.isFinite(b?.timestamp)) continue;
    const bucket = Math.floor(b.timestamp / step) * step;
    if (bucket !== curBucket) {
      if (cur) out.push(cur);
      curBucket = bucket;
      cur = { ...b, time: new Date(bucket).toISOString(), timestamp: bucket };
    } else if (cur) {
      cur.high = Math.max(cur.high ?? b.high ?? 0, b.high ?? 0);
      cur.low = Math.min(cur.low ?? b.low ?? Number.POSITIVE_INFINITY, b.low ?? Number.POSITIVE_INFINITY);
      cur.close = b.close;
      cur.volume = (cur.volume ?? 0) + (b.volume ?? 0);
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** 真实 chart.candles → 前端 `Candle`（`volumeUsd` 保持 null，不用名义额冒名）。 */
export function labCandlesToBars(chart: LabChart | null): Candle[] {
  if (!chart?.candles) return [];
  const out: Candle[] = [];
  for (const c of chart.candles) {
    const bar = normalizeCandle(c);
    if (bar) out.push(bar);
  }
  return out;
}

/** 对齐时间戳到当前显示周期的时间桶（秒），使日线等粗周期能精准落位 markers */
function alignTimeToTf(ms: number, tf: Timeframe): number {
  const step = TF_MS[tf];
  if (!step || step <= 900_000) {
    return toChartSeconds(ms);
  }
  const bucket = Math.floor(ms / step) * step;
  return toChartSeconds(bucket);
}

/** 开/平仓 + 信号标记（真实 trades / signals ⇒ 官方 markers）。 */
function buildTradeMarkers(
  chart: LabChart | null,
  selectedTrade: LabChartTrade | null,
  idOf: (t: LabChartTrade) => string | null,
  palette: ChartPalette,
  tf: Timeframe = '1d',
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  if (!chart?.trades) return out;
  for (const t of chart.trades) {
    const sel = selectedTrade !== null && idOf(t) === idOf(selectedTrade);
    /* 「选中」只用**颜色**表达（选中态取更亮的一档），不再放大箭头、不再加文字：
     * `size: 2` 的箭头宽度约等于 2–3 根蜡烛，会直接把所在那根 K 线压住；
     * `text: '开仓(选中)'` 又会糊在蜡烛上方。文案由下方读数条承担。
     * 规格 C4：`size: 1`、`text: ''`。 */
    if (Number.isFinite(t.entry?.t)) {
      out.push({
        id: `entry-${idOf(t) ?? t.entry.t}`,
        time: alignTimeToTf(t.entry.t, tf) as unknown as Time,
        position: 'belowBar',
        shape: 'arrowUp',
        color: palette.entry,
        text: '',
        size: 2,
      });
    }
    if (Number.isFinite(t.exit?.t)) {
      out.push({
        id: `exit-${idOf(t) ?? t.exit.t}`,
        time: alignTimeToTf(t.exit.t, tf) as unknown as Time,
        position: 'aboveBar',
        shape: 'arrowDown',
        color: palette.exit,
        text: '',
        size: 2,
      });
    }
  }
  return out;
}

/** Stored event ledger only: no candle-pattern inference or fallback timestamps. */
function buildEventMarkers(events: LabChartEvent[], palette: ChartPalette, tf: Timeframe): SeriesMarker<Time>[] {
  const styles: Record<string, { color: string; shape: 'square' | 'arrowDown' | 'arrowUp' | 'circle'; position: 'aboveBar' | 'belowBar'; label: string }> = {
    box: { color: palette.forming, shape: 'square', position: 'aboveBar', label: '箱体' },
    sweep_low: { color: palette.signalMarker, shape: 'arrowDown', position: 'belowBar', label: '扫低' },
    sweep_high: { color: palette.exit, shape: 'arrowUp', position: 'aboveBar', label: '扫高' },
    retest: { color: palette.confirmMarker, shape: 'circle', position: 'belowBar', label: '回踩' },
    breakout: { color: palette.entry, shape: 'square', position: 'aboveBar', label: '突破确认' },
    signal_triggered: { color: palette.signalMarker, shape: 'circle', position: 'aboveBar', label: '信号触发' },
    signal_invalidated: { color: palette.exit, shape: 'circle', position: 'aboveBar', label: '信号失效' },
    entry: { color: palette.entry, shape: 'arrowUp', position: 'belowBar', label: '入场' },
    close: { color: palette.exit, shape: 'arrowDown', position: 'aboveBar', label: '平仓' },
  };
  return events.flatMap((event) => {
    const style = styles[event.event_type];
    // Existing trade and signal builders already draw these with selected-trade styling.
    if (!style || ['entry', 'close', 'signal_triggered'].includes(event.event_type) || !Number.isFinite(event.event_time) || event.event_time <= 0) return [];
    return [{ id: event.event_id, time: alignTimeToTf(event.event_time, tf) as unknown as Time,
      position: style.position, shape: style.shape, color: style.color, text: style.label, size: 1 }];
  });
}

/**
 * 候选信号标记（真实 `chart.signals` ⇒ 官方 markers）。
 *
 * 这是「K 线 → 逐笔」链条的**第一环**：箱体突破那一刻的候选。没有它，
 * 图上就只剩开/平仓两支箭头，看不出「为什么在这根开仓」。
 *
 * 与**成交点分开**（BT-UI-01）：信号用 `inBar` **方块**（落在 K 线实体内），
 * 成交用 `aboveBar/belowBar` **箭头**（在 K 线上方/下方）。两者位置不同，
 * 同一根 K 线上「先有候选、后有成交」不会被两支箭头叠在一起看糊。
 *
 * `t <= 0` 的一律丢弃（历史 run 的 `chart.signals` 曾被上游写死成 `t: 0`）——
 * 宁可少画一个点，也不在错误的位置画一个假点。
 */
function buildSignalMarkers(
  chart: LabChart | null,
  palette: ChartPalette,
  tf: Timeframe = '1d',
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const s of chart?.signals ?? []) {
    if (!Number.isFinite(s?.t) || s.t <= 0) continue;
    out.push({
      id: `sig-${s.t}`,
      time: alignTimeToTf(s.t, tf) as unknown as Time,
      position: 'inBar',
      shape: 'square',
      /* 旧值是品牌蓝的旧色（规格 D1 要消灭的那支）⇒ 改走 --ds-chart-signal */
      color: palette.signalMarker,
      text: '',
      size: 1,
    });
  }
  return out;
}

/** 保护止损轨迹（引擎实况记录，不重建、不造点）。
 * 颜色由调用方从调色板传入（`--ds-chart-forming`）：旧值 #b45309 是硬编码，
 * 深色下不变、只有 3.3:1，落在规格 C6 禁止的中间地带。 */

/**
 * 确认 K 线标记（`chart.confirms` ⇒ 官方 markers）。
 *
 * 为什么要有这一层（FT-REPLAY-01）：一段交易在时间轴上是**四个点**，不是两个 ——
 *   ① 信号：候选 K 线收盘（`signals`，方块）
 *   ② 确认：确认 K 线收盘（`confirms`，圆点）—— C0 模式没有这一步，就不画
 *   ③ 下单/成交：真实入场（`trades.entry`，向上箭头）
 *   ④ 出场：真实出场（`trades.exit`，向下箭头）
 * 把它们画成同一个点，等于把信号时间冒充成成交时间。所以确认点用**独立颜色 + 圆形**，
 * 与信号方块（方形）在形状和颜色上都分开；同一根 K 线上出现两个 inBar 标记时并排显示。
 *
 * `t <= 0` 一律丢弃 —— 宁可少画一个点，也不在错误位置画假点。
 */
function buildConfirmMarkers(
  chart: LabChart | null,
  palette: ChartPalette,
  tf: Timeframe = '1d',
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  for (const c of chart?.confirms ?? []) {
    if (!Number.isFinite(c?.t) || c.t <= 0) continue;
    out.push({
      id: `cfm-${c.t}-${c.signal_id ?? ''}`,
      time: alignTimeToTf(c.t, tf) as unknown as Time,
      position: 'inBar',
      shape: 'circle',
      color: palette.confirmMarker,
      text: '',
      size: 1,
    });
  }
  return out;
}

/**
 * 订单/委托标记（账本 `orders` 表 ⇒ 官方 markers）。
 *
 * 与「成交」标记区分：成交用上下箭头（belowBar/aboveBar），订单用 **inBar 圆点**并按 `phase` 着色，
 * 便于在同一根 K 线上把「委托 → 成交」分层看出；同一 order 一行一标记，不做任何去重/合并。
 * 本策略为次根 15m 开盘撮合 ⇒ 成交价与订单价一致，圆点与箭头落在同一根上，属真实事实，不隐藏。
 */
function buildOrderMarkers(
  orders: LabOrderRow[] | null | undefined,
  palette: ChartPalette,
  tf: Timeframe = '1d',
  currentSymbol?: string | null,
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  if (!orders) return out;
  const normSym = currentSymbol ? currentSymbol.replace(/USDT$/i, '').toUpperCase() : '';
  /* 三种 phase 只保留两种可见色差（入场 / 盘中），EOD 用四级文字色：
   * 旧值 #1d4ed8 / #7c3aed / #64748b 全是硬编码，深色下对比度 2.7–3.2:1。 */
  const phaseColor: Record<string, string> = {
    C_entry: palette.order,
    D_intrabar: palette.signalMarker,
    EOD: palette.text,
  };
  for (const o of orders) {
    if (!Number.isFinite(o.ts_ms)) continue;
    // 单币 K 线标记严格只画当前币种，绝不将组合内其他币种的订单混入当前图表
    if (normSym && o.symbol) {
      const oSym = o.symbol.replace(/USDT$/i, '').toUpperCase();
      if (oSym !== normSym) continue;
    }
    out.push({
      id: `ord-${o.order_id}`,
      time: alignTimeToTf(o.ts_ms, tf) as unknown as Time,
      position: 'inBar',
      shape: 'circle',
      color: phaseColor[o.phase] ?? palette.text,
      text: '',
      size: 1,
    });
  }
  return out;
}

/**
 * run 消费区间的两个边界标记（BT-KLINE-CONTEXT-01）。
 *
 * 只在「上下文真的存在」时才画：上下文为空时边界就等于图的两端，画上去只是噪音。
 * 时间用 `alignTimeToTf` 吸附到展示周期 —— 标记必须落在真实 bar 上，否则官方库不渲染。
 *
 * **只有起点带文字**（实测教训）：默认 1h 视图下 run 窗口 6.75 小时、上下文 14 天，
 * 两个标记在屏幕上只差约 13px，`run 起` / `run 止` 两段文字必然叠在一起糊成一团。
 * 所以起点标 `run`、终点只留方块 —— 两枚方块夹住的区间就是 run 消费区间，
 * 精确起止时间由卡片头的琥珀徽标给出（那里有整段文字，不抢画面）。
 */
function buildRunRangeMarkers(
  chart: LabChart | null,
  palette: ChartPalette,
  tf: Timeframe = '1d',
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = [];
  const ctxDays = chart?.candles_context_days ?? 0;
  const rr = chart?.candles_run_range;
  if (!chart || !ctxDays || !rr) return out;
  if ((chart.candles_context_before ?? 0) > 0) {
    out.push({
      id: 'run-range-start',
      time: alignTimeToTf(rr.start_ms, tf) as unknown as Time,
      position: 'aboveBar',
      shape: 'square',
      color: palette.text,
      text: 'run',
      size: 1,
    });
  }
  if ((chart.candles_context_after ?? 0) > 0) {
    out.push({
      id: 'run-range-end',
      time: alignTimeToTf(rr.end_ms, tf) as unknown as Time,
      position: 'aboveBar',
      shape: 'square',
      color: palette.text,
      text: '',
      size: 1,
    });
  }
  return out;
}

export interface LabChartPanelProps {
  chart: LabChart | null;
  loading: boolean;
  error: string | null;
  missingReason: string | null;
  selectedTradeId: string | null;
  onSelectTrade: (tradeId: string | null) => void;
  /** 账本 `orders` 表（真实委托明细）；缺失 ⇒ 不画订单标记，不造点。 */
  orders?: LabOrderRow[] | null;
  /**
   * 逐笔明细行索引（真实 trade_id + 进出场毫秒）。
   *
   * 为什么需要：线上 `chart.trades[].trade_id` 实测为 `null`（落盘端缺陷，2026-10-17），
   * 而 `GET /trades` 给的是真实 `trd_N`。只靠图载荷 ⇒ 选中高亮 / 图上点击定位全失效。
   * 这里用**两边都有的真实时间戳**把 id 补回来；不用猜、不造假 id。
   */
  tradeIndex?: { tradeId: string; entryMs: number; exitMs: number }[] | null;
  /**
   * 跨图十字线总线（与净值 / 回撤两张图共享）。
   *
   * 不传 ⇒ 只在本组三张图之间联动（K 线 ⇄ 成交量 ⇄ 持仓量），行为退回到
   * 「单面板自洽」，既有调用方不受影响。
   */
  crosshairBus?: CrosshairBus | null;
  /** 运行身份标识 */
  runId?: string | null;
  /** 策略版本 */
  strategyVersion?: string | null;
  /** 订单/成交点击聚焦时间戳（毫秒） */
  focusTimeMs?: number | null;
  /** 当前选中的订单 ID */
  selectedOrderId?: string | null;
  /** 图表标记反查订单回调 */
  onSelectOrder?: (orderId: string | null) => void;
  /** 组合回测多标的列表 */
  symbols?: string[] | null;
  /** 当前选中的标的 */
  activeSymbol?: string | null;
  /** 切换标的回调 */
  onSelectSymbol?: (symbol: string) => void;
  /** 当前选中的显示周期 */
  timeframe?: string;
  /** 切换显示周期回调 */
  onTimeframeChange?: (tf: string) => void;
  /** 手动浏览调整的视窗范围（区分恢复手动视窗 vs 自动聚焦订单） */
  manualViewport?: { fromMs: number; toMs: number } | null;
  /** 手动视窗变更回调 */
  onManualViewportChange?: (fromMs: number, toMs: number) => void;
  /** 与资金分析图共享的时间点并集（UTC 秒）：补纯 whitespace 锚点消除点集差异 */
  gridTimes?: number[];
}

export const LabChartPanel: React.FC<LabChartPanelProps> = ({
  chart,
  loading,
  error,
  missingReason,
  selectedTradeId,
  onSelectTrade,
  orders = null,
  tradeIndex = null,
  crosshairBus = null,
  runId = null,
  strategyVersion = null,
  focusTimeMs = null,
  selectedOrderId = null,
  onSelectOrder,
  symbols = null,
  activeSymbol = null,
  onSelectSymbol,
  timeframe: propTimeframe,
  onTimeframeChange,
  manualViewport = null,
  onManualViewportChange,
  gridTimes: propsGridTimes,
}) => {
  /* 图表标记 / 止损轨迹的颜色一律走 `--ds-chart-*`（本文件不再出现色值字面量） */
  const palette = useChartPalette();
  /* 执行视图默认优先展示小时线（1h），以小时线为主，并允许在界面自由切换 1d/12h/8h/4h/1h/30m/15m */
  const [tf, setTf] = useState<Timeframe>((propTimeframe as Timeframe) || '1h');
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    if (propTimeframe && propTimeframe !== tf) {
      setTf(propTimeframe as Timeframe);
    }
  }, [propTimeframe]);

  const currentSymbolNorm = useMemo(() => {
    const raw = activeSymbol || (chart as any)?.symbol || (chart as any)?.meta?.symbol || '';
    return raw.replace(/USDT$/i, '').toUpperCase();
  }, [activeSymbol, chart]);

  const isOrderMatchingSymbol = useCallback(
    (o: LabOrderRow) => {
      if (!currentSymbolNorm) return true;
      const sym = (o.symbol || '').replace(/USDT$/i, '').toUpperCase();
      return !sym || sym === currentSymbolNorm;
    },
    [currentSymbolNorm],
  );

  const programmaticQuietUntilRef = useRef<number>(0);

  const rawBars = useMemo(() => labCandlesToBars(chart), [chart]);

  /** 检测当前返回蜡烛的真实周期，绝不能盲目拿 exec_timeframe 当成蜡烛周期 */
  const actualTf = useMemo(() => {
    return (
      (chart?.candles_timeframe as Timeframe) ||
      (chart?.display_timeframe as Timeframe) ||
      detectCandleTimeframe(rawBars) ||
      (chart?.exec_timeframe as Timeframe) ||
      '1h'
    );
  }, [chart, rawBars]);

  /** 展示用 K 线：若当前蜡烛已经是目标周期则原样展示；更细且目标更粗时才按 UTC 边界精确聚合 */
  const bars = useMemo(() => {
    if (!rawBars.length) return [];
    if (actualTf === tf) return rawBars;
    const actualStep = TF_MS[actualTf];
    const targetStep = TF_MS[tf];
    if (actualStep && targetStep && actualStep < targetStep) {
      return bucketCandles(rawBars, actualTf, tf);
    }
    return rawBars;
  }, [rawBars, actualTf, tf]);

  const isAggregated = tf !== actualTf;
  const baseGrid = useMemo(() => buildCandleGrid(bars), [bars]);
  const gridTimes = useMemo(() => {
    if (!propsGridTimes || propsGridTimes.length === 0) return baseGrid;
    const set = new Set<number>(baseGrid);
    for (const t of propsGridTimes) set.add(t);
    return Array.from(set).sort((a, b) => a - b);
  }, [baseGrid, propsGridTimes]);
  const viewKey = useMemo(
    () => (chart
      ? `lab:${chart.symbol}:${actualTf}:${tf}:${chart.window?.start_ms ?? 0}`
        // 上下文签名（BT-KLINE-CONTEXT-01）：载荷从「只有 run 区间」变成「含 ±7 天上下文」时
        // 必须当成**新视图**重新首屏落位，否则会沿用旧的窄视窗 —— 数据到了、屏幕还停在 17 小时里
        // （实测：1411 根已到位，可见区仍是 05-31 那 17 小时）。
        + `:ctx${chart.candles_context_days ?? 0}-${chart.candles_context_before ?? 0}`
      : 'lab:empty'),
    [chart, actualTf, tf],
  );
  /** 单图工作台：同步组只含这一张图（复用既有 hub 契约，行为与旧调用一致）。 */
  const syncHub = useMemo(() => createChartSyncHub(), [viewKey]);

  const [handle, setHandle] = useState<ChartHandle | null>(null);
  const [hover, setHover] = useState<ChartHover | null>(null);
  const setHoverClean = useCallback((h: ChartHover | null) => {
    setHover(h);
  }, []);
  /** OI 副图自己的悬停（不能和 K 线悬停共用一个 state，否则互相清空） */
  const [hoverOi, setHoverOi] = useState<OIPoint | null>(null);
  const [locateInfo, setLocateInfo] = useState<{ fromMs: number; toMs: number; ok: boolean } | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<LabChartEvent | null>(null);
  /**
   * 「停手后才把视窗交给父组件」的定时器与待写值（BACKTEST-CHART-JITTER-FIX-01）。
   *
   * 为什么必须延迟：父组件把 `onManualViewportChange` 的入参存成 `manualViewport`
   * state，本组件又有一个「`manualViewport` 一变就 `handle.setViewport(...)`」的
   * 恢复 effect ⇒ **上报即回写**形成闭环。而官方时间轴写入是**取整到整根**的
   * （`setViewport` 走 `timeToIndex()` ⇒ 整数逻辑索引），250ms 去抖的上报永远比
   * 手指落后一截 ⇒ 每次回写都把视窗往旧位置拽。
   * 实测（一次 45 步的快速甩动）：应用层在拖动过程中写了 **18 次**时间轴
   * （9 组 setVisibleRange + setVisibleLogicalRange），逐帧采样出现 **8 次方向反转**、
   * 每次回跳 **5–6 根**（≈45–54px）；慢速拖动 3.6 秒同样写了 54 次。
   * 现在改为「最后一次上报之后 400ms 才写一次」，拖动/缩放期间一次都不写。
   */
  const manualViewportTimerRef = useRef<number | null>(null);
  const pendingManualViewportRef = useRef<{ fromMs: number; toMs: number } | null>(null);
  /** 父组件当前持有的手动视窗：用来判断「这个视窗它已经知道了，不必再说一次」。 */
  const manualViewportRef = useRef(manualViewport);
  manualViewportRef.current = manualViewport;

  /**
   * 「首屏落位让位」标记（BT-KLINE-CONTEXT-01）。
   *
   * 为什么需要：打开页面时 `selectedTradeId` 会被**自动**设成本 run 第一笔
   * （见 `BacktestWorkbench` 的「自动选中本 run 第一笔」），紧接着下面的定位 effect
   * 用 `entry ± 12 根` 把视窗收窄 —— 实测 1h 下就是 **30 根 / 30 小时**，
   * 而载荷里有 **343 根 / 14 天上下文**（前 7 天 + 后 7 天）。
   * 结果：「扩了 ±7 天」在屏幕上一根也看不出来，7 天箱体依旧不可见。
   *
   * 规矩：**每个新视图（run / 标的 / 显示周期）的首屏一律按完整数据域落位**，
   * 自动选中不算用户意图；只有用户**主动**点某笔成交 / 订单时才定位收窄。
   * 键里带 `runId` + `symbol` + `tf` ⇒ 换 run / 换标的 / 换周期都会重新走首屏。
   *
   * 为什么键里还要带**句柄本身**（实测踩过）：切周期会换掉 `viewKey` ⇒ 图表实例重建，
   * 但定位 effect 会在「旧句柄还在、新实例未挂载」的那一帧先跑一次并消耗门闩，
   * 等新句柄 `onReady` 后再跑就变成「不是首屏」⇒ 照样收窄（实测 15m 落到 51 根、
   * 4h 落到 26 根）。把句柄身份并进键里，新实例天然重新获得一次首屏让位。
   */
  const firstPaintRef = useRef<{ key: string; handle: ChartHandle } | null>(null);
  const paintKey = `${runId ?? ''}|${chart?.symbol ?? ''}|${tf}`;
  /** 是否是「这个视图 + 这个图表实例」的首次落位；同一组合只返回一次 `true`。 */
  const isFirstPaintOf = (key: string, h: ChartHandle): boolean => {
    const cur = firstPaintRef.current;
    if (cur && cur.key === key && cur.handle === h) return false;
    firstPaintRef.current = { key, handle: h };
    return true;
  };

  useEffect(() => {
    if (typeof window !== 'undefined') {
      (window as any).__labChartHandle = handle;
      (window as any).__labChartBars = bars;
      (window as any).__labChartTf = tf;
      (window as any).__labOrders = orders;
      (window as any).__labSelectedOrderId = selectedOrderId;
      (window as any).__labChartData = chart;
      (window as any).__labCurrentRun = runId;
    }
    return () => {
      if (typeof window !== 'undefined' && (window as any).__labChartHandle === handle) {
        delete (window as any).__labChartHandle;
        delete (window as any).__labChartBars;
        delete (window as any).__labChartTf;
        delete (window as any).__labOrders;
        delete (window as any).__labSelectedOrderId;
      }
    };
  }, [handle, bars, tf, orders, selectedOrderId]);

  /* ── 三图十字线联动 ────────────────────────────────────────────────────
   * K 线 / 成交量 / 持仓量是**三个独立实例**（不是同一实例的三个 pane），
   * 官方库不会自动贯穿十字线。这里按 `InlineExpandedChart` 已验证的做法：
   * 任一图悬停 ⇒ 把同一时点推给另外两张。
   *
   * `syncingRef` 防止回环：程序化 `crosshairAt` 会让官方库再派发一次
   * crosshairMove，若不加锁就会 A→B→A 无限递归。 */
  const candleHandleRef = useRef<ChartHandle | null>(null);
  const volumeHandleRef = useRef<ChartHandle | null>(null);
  const oiHandleRef = useRef<ChartHandle | null>(null);
  const syncingRef = useRef(false);
  /** 程序化聚焦锁：防止 setViewport 平移引起的鼠标绝对像素 crosshairMove 覆盖真实订单时刻 */
  const programmaticFocusLockRef = useRef<number>(0);

  const onReady = useCallback((h: ChartHandle) => {
    candleHandleRef.current = h;
    setHandle(h);
    if (typeof window !== 'undefined') {
      (window as any).__labChartHandle = h;
      (window as any).__getChartTimeCoordinate = (timeSec: number) => {
        try {
          return h.chart.timeScale().timeToCoordinate(timeSec as UTCTimestamp);
        } catch {
          return null;
        }
      };
    }
  }, []);
  const onVolumeReady = useCallback((h: ChartHandle) => {
    volumeHandleRef.current = h;
  }, []);
  /* 视窗变化同时推给净值 / 回撤两张图。
     不推的话，K 线的时点常常落在净值图可见范围之外，十字线会被官方库挡掉，
     观感上就是「点了没反应」。

     **回写给父组件的那一棒必须延迟到「停手」之后**（BACKTEST-CHART-JITTER-FIX-01）：
     父组件一收到就 `setManualViewport`，本组件的恢复 effect 立刻 `setViewport`，
     而官方时间轴写入取整到整根 ⇒ 拖动过程中每 250ms 把视窗往旧位置拽一次。
     延迟 400ms 后：拖动/滚轮期间**零次**回写，停手后只写一次（且下面恢复 effect
     会判定「已经在目标视窗」而不再落位）。视窗广播（`emitViewport`）仍即时发出，
     净值/回撤图的联动观感不变。 */
  const onViewportChange = useCallback(
    (v: ChartViewport) => {
      if (performance.now() < programmaticQuietUntilRef.current) return;
      const barSpanMs = TF_MS[tf] ?? 3600_000;
      crosshairBus?.emitViewport('lab-chart', v.fromMs, v.toMs + barSpanMs);
      if (!onManualViewportChange) return;
      if (Date.now() < programmaticFocusLockRef.current) return;
      pendingManualViewportRef.current = { fromMs: v.fromMs, toMs: v.toMs };
      if (manualViewportTimerRef.current !== null) {
        window.clearTimeout(manualViewportTimerRef.current);
      }
      manualViewportTimerRef.current = window.setTimeout(() => {
        manualViewportTimerRef.current = null;
        const pending = pendingManualViewportRef.current;
        if (!pending) return;
        /**
         * 计时到了还要再核一次「是不是真的停住了」：
         * 若此刻的可见范围与待写值不一致 ⇒ 这 400ms 里手指还在动（上报比手慢半拍）
         * ⇒ **这一轮不写**，直接丢掉。丢不丢得起？丢得起 —— 范围变了就必然再派发一次
         * 范围事件、再调度一次上报，待写值会被刷成最新，计时器重新起算。
         * 这样「拖动中写一次旧窗口」的窗口期被彻底关掉，而不是只缩短。
         */
        const cur = candleHandleRef.current?.visibleRange();
        if (
          cur &&
          (Math.abs(cur.fromMs - pending.fromMs) >= VIEWPORT_SAME_TOLERANCE_MS ||
            Math.abs(cur.toMs - pending.toMs) >= VIEWPORT_SAME_TOLERANCE_MS)
        ) {
          return;
        }
        /**
         * 父组件**已经持有同一个视窗** ⇒ 一个字都不必再说。
         *
         * 这道守卫断的是一条会自己转下去的环（实测：页面完全空闲、鼠标不动，
         * 主图时间轴仍然每 ~840ms 被写一次，10 秒 12 次，直到页面关掉）：
         *   上报 → `setManualViewport({...})` 新建对象（**即使数值完全没变**）
         *   → `manualViewport` 引用变化 → `computedInitialViewport` 重算成新对象
         *   → 图表数据 effect 重跑 → 把「当前可见范围」按整秒再写回一次
         *   → 官方库派发一次范围事件 → 又调度一次上报 …… 如此往复。
         * 数值比较通过就不再上报，环在「父组件已知」这一步断开。
         */
        const known = manualViewportRef.current;
        if (
          known &&
          Math.abs(known.fromMs - pending.fromMs) < VIEWPORT_SAME_TOLERANCE_MS &&
          Math.abs(known.toMs - pending.toMs) < VIEWPORT_SAME_TOLERANCE_MS
        ) {
          return;
        }
        onManualViewportChange(pending.fromMs, pending.toMs);
      }, VIEWPORT_SETTLE_MS);
    },
    [crosshairBus, onManualViewportChange, tf],
  );

  /** 卸载时清掉待写定时器（切 run / 切页面不留悬挂回调）。 */
  useEffect(
    () => () => {
      if (manualViewportTimerRef.current !== null) {
        window.clearTimeout(manualViewportTimerRef.current);
        manualViewportTimerRef.current = null;
      }
    },
    [],
  );

  /* 接收资金图反向视窗广播（双向联动） */
  useEffect(() => {
    if (!crosshairBus || !handle) return undefined;
    return crosshairBus.registerViewport('lab-chart', (fromMs, toMs) => {
      if (Date.now() < programmaticFocusLockRef.current) return;
      programmaticQuietUntilRef.current = performance.now() + 500;
      handle.setViewport(fromMs, toMs);
    });
  }, [crosshairBus, handle]);

  /* ── OI 持仓量：真实 5m 观测点 → 按当前展示周期分桶（桶内取最后一个真实观测）──
   * 后端读接口按本 run 窗口现取（`repo.oi(period="5m")`，与策略喂数同一方法）。
   * 取不到 ⇒ `oiAligned` 为空，面板如实写「没有 5m 归档」，不画假柱。 */
  const oiPoints = useMemo<OIPoint[]>(() => {
    const out: OIPoint[] = [];
    for (const p of chart?.oi ?? []) {
      if (!p || !Number.isFinite(p.t) || !Number.isFinite(p.oi)) continue;
      out.push({
        time: String(p.t),
        timestamp: p.t,
        oiAmount: p.oi,
        oiUsd: null,
        oiDelta: null,
        oiDeltaUsd: null,
        deltaPercent: null,
        longOiUsd: null,
        shortOiUsd: null,
        longOiAmount: null,
        shortOiAmount: null,
        longRatio: null,
        shortRatio: null,
        netOiUsd: null,
      });
    }
    return out;
  }, [chart]);
  const oiAligned = useMemo(
    () => alignOiToTimeframe(oiPoints, TF_MS[tf] ?? 900_000),
    [oiPoints, tf],
  );
  const oiErr = chart?.oi_meta?.error ?? null;

  /* ── 三图十字线联动（实现）────────────────────────────────────────────
   * 依赖 `bars` 与 `oiAligned`，故放在两者之后定义。
   * 任一图悬停 ⇒ 把同一时点推给另外两张；`null` ⇒ 三张一起清除。 */
  const syncSiblings = useCallback(
    (timeMs: number | null, source: 'candle' | 'volume' | 'oi' | 'external') => {
      if (syncingRef.current) return;
      syncingRef.current = true;
      try {
        if (timeMs === null) {
          candleHandleRef.current?.clearCrosshair();
          volumeHandleRef.current?.clearCrosshair();
          oiHandleRef.current?.clearCrosshair();
          return;
        }
        const sec = toChartSeconds(timeMs);
        if (!sec) return;
        /* 容差取半个周期：跨周期时最近点可能落在相邻根上，超过半根就算「该图此处没有数据」 */
        const tol = (TF_MS[tf] ?? 900_000) / 2;
        if (source !== 'candle') {
          if (source === 'external' && Date.now() < programmaticFocusLockRef.current) return;
          const bar = nearestByTime(bars, timeMs, (b) => b.timestamp, tol);
          candleHandleRef.current?.crosshairAt(sec, bar && Number.isFinite(bar.close) ? bar.close : null);
          /* 读数条要自己更新：`crosshairAt` 只画线，官方库不会为程序化定位派发
             crosshairMove 事件，只等回调就会一直停在上一根的数值上。 */
          if (bar) setHoverClean({ source: 'candle', timeMs: bar.timestamp, candle: bar });
        }
        if (source !== 'volume') {
          const bar = nearestByTime(bars, timeMs, (b) => b.timestamp, tol);
          volumeHandleRef.current?.crosshairAt(
            sec,
            bar && Number.isFinite(bar.volume) ? bar.volume : null,
          );
        }
        if (source !== 'oi') {
          const point = nearestByTime(oiAligned, timeMs, (p) => p.timestamp, tol);
          oiHandleRef.current?.crosshairAt(
            sec,
            point && Number.isFinite(point.oiAmount) ? point.oiAmount : null,
          );
          /* 同上：程序化定位不会派发事件，OI 读数得自己同步 */
          if (point) setHoverOi(point);
        }
      } finally {
        syncingRef.current = false;
      }
    },
    [bars, oiAligned, tf],
  );

  const onHover = useCallback(
    (h: ChartHover | null) => {
      // 若处于程序化定位保护期内（例如刚点击订单行），忽略图表视窗平移触发的鼠标悬停覆盖
      if (Date.now() < programmaticFocusLockRef.current) return;
      setHoverClean(h);
      syncSiblings(h?.timeMs ?? null, 'candle');
      crosshairBus?.emit('lab-chart', h?.timeMs ?? null);
    },
    [syncSiblings, crosshairBus, setHoverClean],
  );
  const onOiHover = useCallback(
    (h: ChartHover | null) => {
      if (Date.now() < programmaticFocusLockRef.current) return;
      setHoverOi(h?.oiPoint ?? null);
      syncSiblings(h?.timeMs ?? null, 'oi');
      crosshairBus?.emit('lab-chart', h?.timeMs ?? null);
    },
    [syncSiblings, crosshairBus],
  );
  /* 成交量条与 K 线共用同一根数据，读数条照旧用 `setHover`；
     但联动来源必须标成 `volume`，否则「从成交量图滑到 K 线图」会被自己的来源判断挡掉。 */
  const onVolumeHover = useCallback(
    (h: ChartHover | null) => {
      if (Date.now() < programmaticFocusLockRef.current) return;
      setHoverClean(h);
      syncSiblings(h?.timeMs ?? null, 'volume');
      crosshairBus?.emit('lab-chart', h?.timeMs ?? null);
    },
    [syncSiblings, crosshairBus, setHoverClean],
  );
  /** OI 副图**不接管** `handle`：止损轨迹叠加与定位都用 K 线那张图的句柄。 */
  const onOiReady = useCallback((h: ChartHandle) => {
    oiHandleRef.current = h;
  }, []);

  /** 外部（净值 / 回撤图）悬停 ⇒ 把时点落到本组三张图。总线统一用毫秒。 */
  useEffect(() => {
    if (!crosshairBus) return undefined;
    return crosshairBus.register('lab-chart', (timeMs) => {
      /* 来源标成 `external`：外部图的悬停同样要刷新本组三图的十字线**和读数条**，
         若沿用 `'candle'` 会被「跳过发起者」的判断挡掉，读数条永远停在上一根。 */
      syncSiblings(timeMs, 'external');
    });
  }, [crosshairBus, syncSiblings]);

  /**
   * 首次落位后主动广播一次视窗。
   *
   * 常规上报走 250ms 去抖 + 400ms 程序化静默窗，首屏落位恰好落在静默窗内，
   * 不会产生上报 ⇒ 净值 / 回撤永远等不到第一次对齐。这里在落位稳定后读一次
   * 真实可见范围补上这一棒，用户一进页面五张图就是同一段窗口。
   */
  useEffect(() => {
    if (!crosshairBus || !handle) return undefined;
    const timer = window.setTimeout(() => {
      const r = handle.visibleRange();
      if (r) {
        // 语义统一：K 线主图 getVisibleRange().to 是最右可见 bar 的 open_time。
        // 其物理闭合右边界需加上本周期一根 bar 的跨度（TF_MS[tf]），
        // 使得连续折线图（资金曲线）与 K 线主图的右边界在物理时间上真正严格对齐。
        const barSpanMs = TF_MS[tf] ?? 3600_000;
        crosshairBus.emitViewport('lab-chart', r.fromMs, r.toMs + barSpanMs);
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [crosshairBus, handle, viewKey, tf]);

  /**
   * 图载荷里一笔的**真实 id**：优先用载荷自带的 `trade_id`；
   * 若为 `null`（落盘端缺陷）⇒ 用 entry 时刻去逐笔明细索引里换回真实 id。
   * 两边用的都是真实毫秒时间戳，不猜、不造。
   */
  const idByEntryMs = useMemo(() => {
    const m = new Map<number, string>();
    for (const x of tradeIndex ?? []) {
      if (Number.isFinite(x?.entryMs) && x.tradeId) m.set(x.entryMs, x.tradeId);
    }
    return m;
  }, [tradeIndex]);

  const idOf = useCallback(
    (t: LabChartTrade): string | null =>
      t.trade_id ?? (Number.isFinite(t.entry?.t) ? (idByEntryMs.get(t.entry.t) ?? null) : null),
    [idByEntryMs],
  );

  const selectedTrade: LabChartTrade | null = useMemo(() => {
    const list = chart?.trades ?? [];
    if (list.length === 0) return null;
    if (selectedTradeId) {
      return list.find((t) => idOf(t) === selectedTradeId) ?? list[0];
    }
    return list[0];
  }, [chart, selectedTradeId, idOf]);

  const extraMarkers = useMemo(
    () => [
      ...buildSignalMarkers(chart, palette, tf),
      ...buildConfirmMarkers(chart, palette, tf),
      ...buildTradeMarkers(chart, selectedTrade, idOf, palette, tf),
      ...buildEventMarkers(chart?.events ?? [], palette, tf),
      ...buildOrderMarkers(orders, palette, tf, currentSymbolNorm),
      ...buildRunRangeMarkers(chart, palette, tf),
    ],
    [chart, selectedTrade, idOf, orders, tf, palette, currentSymbolNorm],
  );
  const onChartTimeClick = useCallback((timeMs: number) => {
    const event = (chart?.events ?? []).find((item) => alignTimeToTf(item.event_time, tf) === Math.floor(timeMs / 1000) * 1000);
    if (event) setSelectedEvent(event);
  }, [chart?.events, tf]);

  /* ── 止损轨迹叠加：用现有 onReady 句柄加官方 LineSeries，不重写 K 线引擎 ── */
  const overlayRef = useRef<ISeriesApi<'Line'>[]>([]);
  useEffect(() => {
    const api = handle?.chart;
    overlayRef.current = [];
    if (!api || !chart?.trades?.length) return;

    const created: ISeriesApi<'Line'>[] = [];
    for (const t of chart.trades) {
      // 字段契约兼容：官方 runner 落 `t`，历史批量脚本落 `ts`（同值不同名）。
      // 两种都接受 ⇒ 上游无论往哪边收敛都能画；两者都没有的点直接丢弃，不猜时间。
      const trace = (t.stop_trace ?? [])
        .map((p) => ({
          t: Number.isFinite(p?.t) ? p.t : Number((p as unknown as { ts?: number })?.ts),
          stop: p?.stop,
        }))
        .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.stop));
      // 单点（只有 `init`、之后没有止损调整）**不合成轨迹**：一个点画不出线，
      // 硬拉一条会伪造「止损一路没动」的走势。如实显示在下方读数行里。
      if (trace.length < 2) continue;
      const isSel = selectedTrade !== null && idOf(t) === idOf(selectedTrade);
      try {
        const s = api.addSeries(LineSeries, {
          color: isSel ? palette.forming : withAlpha(palette.forming, 0.35),
          lineWidth: isSel ? 2 : 1,
          lineStyle: LineStyle.Solid,
          lineType: LineType.WithSteps,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: isSel ? '保护止损(选中)' : '',
        });
        s.setData(trace.map((p) => ({ time: toChartSeconds(p.t), value: p.stop })));
        created.push(s);
      } catch {
        /* 官方 API 异常不得打断渲染 */
      }
    }
    overlayRef.current = created;

    return () => {
      for (const s of created) {
        try {
          api.removeSeries(s);
        } catch {
          /* 图已销毁：忽略 */
        }
      }
      overlayRef.current = [];
    };
  }, [handle, chart, selectedTrade, idOf]);

  /* ── 统一首屏/聚焦视窗（首选手动视窗恢复，次选订单/交易聚焦 90 根饱满蜡烛） ── */
  const computedInitialViewport = useMemo<ChartViewport | null>(() => {
    if (manualViewport && Number.isFinite(manualViewport.fromMs) && Number.isFinite(manualViewport.toMs) && manualViewport.toMs > manualViewport.fromMs) {
      return { fromMs: manualViewport.fromMs, toMs: manualViewport.toMs, followLatest: false };
    }
    const tradeEntryT = selectedTrade?.entry?.t ?? (selectedTrade as any)?.entry_time ?? (selectedTrade as any)?.entry_time_ms ?? null;
    const centerMs = focusTimeMs ?? tradeEntryT;
    if (!centerMs || bars.length === 0) return null;
    const stepMs = TF_MS[tf] ?? 3_600_000;
    const barIdx = bars.findIndex((b) => b.timestamp <= centerMs && centerMs < b.timestamp + stepMs);
    let fromMs: number;
    let toMs: number;
    if (barIdx >= 0) {
      const startIdx = Math.max(0, barIdx - 45);
      const endIdx = Math.min(bars.length - 1, barIdx + 45);
      fromMs = bars[startIdx].timestamp;
      toMs = bars[endIdx].timestamp;
    } else {
      const padMs = Math.max(3_600_000 * 24, stepMs * 45);
      fromMs = centerMs - padMs;
      toMs = centerMs + padMs;
    }
    return { fromMs, toMs, followLatest: false };
  }, [manualViewport, focusTimeMs, selectedTrade, bars, tf]);

  /* ── 手动视窗恢复：优先落位并广播给资金图 ──
   *
   * 两道守卫（BACKTEST-CHART-JITTER-FIX-01）：
   *  ① **已经在目标视窗 ⇒ 一次都不写**。`manualViewport` 的值本来就是本图上报出去的，
   *     停手后父组件把它存回来时，图已经停在那个位置；此时再 `setViewport` 只会
   *     用「取整到整根」的索引把用户刚刚调好的窗口重新吸附一遍（观感即抖动）。
   *  ② 真需要落位时先进入静默窗：这次写入派发的范围事件不得再被当成「用户视窗」
   *     上报出去，否则又回到「写 → 报 → 再写」的闭环。
   */
  useEffect(() => {
    if (!manualViewport || !handle) return;
    if (!Number.isFinite(manualViewport.fromMs) || !Number.isFinite(manualViewport.toMs)) return;
    if (manualViewport.toMs <= manualViewport.fromMs) return;
    const cur = handle.visibleRange();
    if (
      cur &&
      Math.abs(cur.fromMs - manualViewport.fromMs) < VIEWPORT_SAME_TOLERANCE_MS &&
      Math.abs(cur.toMs - manualViewport.toMs) < VIEWPORT_SAME_TOLERANCE_MS
    ) {
      return;
    }
    programmaticQuietUntilRef.current = performance.now() + 500;
    handle.setViewport(manualViewport.fromMs, manualViewport.toMs);
    const stepMs = TF_MS[tf] ?? 3_600_000;
    crosshairBus?.emitViewport('lab-chart', manualViewport.fromMs, manualViewport.toMs + stepMs);
  }, [manualViewport, handle, tf, crosshairBus]);

  /* ── 选中交易 ⇒ 用现有 setViewport 定位 entry/exit 区间（自适应当前显示周期，约 90 根饱满蜡烛） ── */
  useEffect(() => {
    if (!selectedTrade || !handle) {
      setLocateInfo(null);
      return;
    }
    // 若当前有精确事件（如订单/成交点击）在加锁保护，不被交易级别视窗覆盖
    if (Date.now() < programmaticFocusLockRef.current) return;
    // 若当前存在恢复的手动视窗（深链或重开恢复），不被交易级别自动视窗覆盖
    if (manualViewport && Number.isFinite(manualViewport.fromMs) && Number.isFinite(manualViewport.toMs)) return;
    const stepMs = TF_MS[tf] ?? 3_600_000;
    const entryT = selectedTrade?.entry?.t ?? (selectedTrade as any)?.entry_time ?? (selectedTrade as any)?.entry_time_ms;
    const exitT = selectedTrade?.exit?.t ?? (selectedTrade as any)?.exit_time ?? (selectedTrade as any)?.exit_time_ms ?? entryT;
    if (!Number.isFinite(entryT)) return;

    const barIdx = bars.findIndex((b) => b.timestamp <= entryT && entryT < b.timestamp + stepMs);
    let fromMs: number;
    let toMs: number;
    if (barIdx >= 0) {
      const startIdx = Math.max(0, barIdx - 45);
      const endIdx = Math.min(bars.length - 1, barIdx + 45);
      fromMs = bars[startIdx].timestamp;
      toMs = bars[endIdx].timestamp;
    } else {
      const padMs = Math.max(3_600_000 * 24, stepMs * 45);
      fromMs = entryT - padMs;
      toMs = (Number.isFinite(exitT) ? exitT : entryT) + padMs;
    }
    const timer = setTimeout(() => {
      const ok = handle.setViewport(fromMs, toMs);
      setLocateInfo({ fromMs, toMs, ok });
      crosshairBus?.emitViewport('lab-chart', fromMs, toMs + stepMs);
    }, 60);
    return () => clearTimeout(timer);
  }, [selectedTrade, handle, tf, bars, crosshairBus, manualViewport]);

  /* ── 订单/成交点击聚焦定位（精确到事件发生时点） ── */
  useEffect(() => {
    if (!focusTimeMs || !handle) return;
    const stepMs = TF_MS[tf] ?? 3_600_000;
    const barIdx = bars.findIndex((b) => b.timestamp <= focusTimeMs && focusTimeMs < b.timestamp + stepMs);
    let fromMs: number;
    let toMs: number;
    if (barIdx >= 0) {
      const startIdx = Math.max(0, barIdx - 45);
      const endIdx = Math.min(bars.length - 1, barIdx + 45);
      fromMs = bars[startIdx].timestamp;
      toMs = bars[endIdx].timestamp;
    } else {
      const padMs = Math.max(3_600_000 * 24, stepMs * 45);
      fromMs = focusTimeMs - padMs;
      toMs = focusTimeMs + padMs;
    }
    // 加锁 800ms：让视窗平移过程中的 crosshairMove 不覆盖选中的订单时刻
    programmaticFocusLockRef.current = Date.now() + 800;
    const timer = setTimeout(() => {
      handle.setViewport(fromMs, toMs);
      crosshairBus?.emitViewport('lab-chart', fromMs, toMs + stepMs);
    }, 60);

    // 找到当前周期内对应的归桶 bar（左闭右开区间 [b.timestamp, b.timestamp + stepMs)，例如 14:30 严格归入 14:00 柱）
    const bar = barIdx >= 0 ? bars[barIdx] : nearestByTime(bars, focusTimeMs, (b) => b.timestamp, stepMs);
    if (bar) {
      const barSec = toChartSeconds(bar.timestamp);
      // 精准将十字线落到该归桶 bar 上，保证图表上准确高亮该根 K 线
      if (barSec) {
        handle.crosshairAt(barSec, Number.isFinite(bar.close) ? bar.close : null);
      }
      setHoverClean({ source: 'candle', timeMs: bar.timestamp, candle: bar });
      // 同步驱动成交量、持仓量（OI）及资金曲线同时定位到该时刻
      syncSiblings(bar.timestamp, 'candle');
      crosshairBus?.emit('lab-chart', bar.timestamp);
    }
    return () => clearTimeout(timer);
  }, [focusTimeMs, handle, tf, bars, syncSiblings, crosshairBus]);

  /* ── 上一笔 / 下一笔交易导航 ── */
  const tradeList = useMemo(() => chart?.trades ?? [], [chart]);
  const currentTradeIndex = useMemo(() => {
    if (!selectedTradeId || tradeList.length === 0) return -1;
    return tradeList.findIndex((t) => idOf(t) === selectedTradeId);
  }, [tradeList, selectedTradeId, idOf]);

  const handlePrevTrade = useCallback(() => {
    if (tradeList.length === 0) return;
    if (currentTradeIndex <= 0) {
      const last = tradeList[tradeList.length - 1];
      if (last) onSelectTrade(idOf(last));
    } else {
      const prev = tradeList[currentTradeIndex - 1];
      if (prev) onSelectTrade(idOf(prev));
    }
  }, [tradeList, currentTradeIndex, idOf, onSelectTrade]);

  const handleNextTrade = useCallback(() => {
    if (tradeList.length === 0) return;
    if (currentTradeIndex < 0 || currentTradeIndex >= tradeList.length - 1) {
      const first = tradeList[0];
      if (first) onSelectTrade(idOf(first));
    } else {
      const next = tradeList[currentTradeIndex + 1];
      if (next) onSelectTrade(idOf(next));
    }
  }, [tradeList, currentTradeIndex, idOf, onSelectTrade]);

  /* ── 图上点击 = 选中该 bar 上的真实开/平仓与委托订单（精确命中才选中，按当前周期对齐）── */
  useEffect(() => {
    const api = handle?.chart;
    if (!api) return;
    const tradesBySec = new Map<number, string[]>();
    for (const t of chart?.trades ?? []) {
      const id = idOf(t);
      if (!id) continue;
      if (Number.isFinite(t.entry?.t)) {
        const s = alignTimeToTf(t.entry.t, tf);
        const l = tradesBySec.get(s) ?? [];
        if (!l.includes(id)) l.push(id);
        tradesBySec.set(s, l);
      }
      if (Number.isFinite(t.exit?.t)) {
        const s = alignTimeToTf(t.exit.t, tf);
        const l = tradesBySec.get(s) ?? [];
        if (!l.includes(id)) l.push(id);
        tradesBySec.set(s, l);
      }
    }
    const ordersBySec = new Map<number, string[]>();
    for (const o of orders ?? []) {
      if (Number.isFinite(o.ts_ms) && o.order_id && isOrderMatchingSymbol(o)) {
        const bucketSec = alignTimeToTf(o.ts_ms, tf);
        const exactSec = toChartSeconds(o.ts_ms);
        for (const s of [bucketSec, exactSec]) {
          const l = ordersBySec.get(s) ?? [];
          if (!l.includes(o.order_id)) l.push(o.order_id);
          ordersBySec.set(s, l);
        }
      }
    }

    const handler = (param: { time?: unknown }) => {
      const sec = typeof param?.time === 'number' ? (param.time as number) : null;
      if (sec === null) return;
      // 同小时多个交易支持切换或选中
      const tradeIds = tradesBySec.get(sec) ?? [];
      if (tradeIds.length > 0) {
        const curIdx = selectedTradeId ? tradeIds.indexOf(selectedTradeId) : -1;
        const nextTradeId = tradeIds[(curIdx + 1) % tradeIds.length];
        onSelectTrade(nextTradeId);
      }
      // 图表标记反查订单：优先当前币种，重叠标记支持循环轮换选择，明确反查结果
      let orderIds = ordersBySec.get(sec) ?? [];
      if (orderIds.length === 0) {
        orderIds = ordersBySec.get(alignTimeToTf(sec * 1000, tf)) ?? [];
      }
      if (orderIds.length > 0 && onSelectOrder) {
        const curIdx = selectedOrderId ? orderIds.indexOf(selectedOrderId) : -1;
        const nextOrderId = orderIds[(curIdx + 1) % orderIds.length];
        onSelectOrder(nextOrderId);
      }
    };
    if (typeof window !== 'undefined') {
      (window as any).__triggerChartClick = (sec: number) => handler({ time: sec });
    }
    try {
      api.subscribeClick(handler as never);
    } catch {
      return;
    }
    return () => {
      if (typeof window !== 'undefined') {
        delete (window as any).__triggerChartClick;
      }
      try {
        api.unsubscribeClick(handler as never);
      } catch {
        /* 图已销毁：忽略 */
      }
    };
  }, [handle, chart, orders, onSelectTrade, onSelectOrder, selectedOrderId, selectedTradeId, idOf, tf, isOrderMatchingSymbol]);

  const hoveredBar = hover?.candle ?? null;
  const hoveredSignal = useMemo(() => {
    if (!hoveredBar || !chart?.signals) return null;
    return chart.signals.find((s) => s.t === hoveredBar.timestamp) ?? null;
  }, [hoveredBar, chart]);

  /** 当前时间桶内【属于当前币种】的委托明细，保留精确成交时间与价格，同小时多事件可区分 */
  const hoveredBucketOrders = useMemo(() => {
    if (!hoveredBar || !orders) return [];
    const step = TF_MS[tf] ?? 3_600_000;
    const start = hoveredBar.timestamp;
    const end = start + step;
    return orders.filter(
      (o) => typeof o.ts_ms === 'number' && o.ts_ms >= start && o.ts_ms < end && isOrderMatchingSymbol(o),
    );
  }, [hoveredBar, orders, tf, isOrderMatchingSymbol]);

  /** 当前时间桶内【跨币组合其他币种】的并发委托，单独标明币种与用途，绝不冒充当前币种K线事件 */
  const crossSymbolBucketOrders = useMemo(() => {
    if (!hoveredBar || !orders || !currentSymbolNorm) return [];
    const step = TF_MS[tf] ?? 3_600_000;
    const start = hoveredBar.timestamp;
    const end = start + step;
    return orders.filter(
      (o) => typeof o.ts_ms === 'number' && o.ts_ms >= start && o.ts_ms < end && !isOrderMatchingSymbol(o),
    );
  }, [hoveredBar, orders, tf, currentSymbolNorm, isOrderMatchingSymbol]);

  const tp = chart?.take_profit ?? null;
  const noFixedTp = tp ? tp.exists === false : null;

  /** 选中交易的止损轨迹读数：**账本有几个点就报几个点**。
   *  只有 1 个 `init` 点（无后续调整）时如实说明，**不合成轨迹**。 */
  const stopTraceInfo = useMemo(() => {
    if (!selectedTrade) return null;
    const tr = (selectedTrade.stop_trace ?? [])
      .map((p) => ({
        t: Number.isFinite(p?.t) ? p.t : Number((p as unknown as { ts?: number })?.ts),
        stop: p?.stop,
      }))
      .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.stop));
    return { n: tr.length, init: tr.length > 0 ? tr[0].stop : null,
             adjustments: Math.max(0, tr.length - 1) };
  }, [selectedTrade]);

  return (
    <div className="ds-card" data-testid="lab-chart-panel">
      {/* 卡片头：设计稿 `.h` —— 一行标题 + 一行灰字，并显示完整运行身份与联动控制器 */}
      <div className="ds-card__head flex flex-wrap items-center gap-2">
        <span
          className="ds-card__title"
          title="真实 K 线 / 成交量 / 信号 / 开平仓 / 保护止损轨迹；默认 1h 主小时线，保留 4h/15m 等多周期切换"
        >
          K 线 · 成交量 · 持仓量（OI）
        </span>
        <span className="ds-card__hint">
          {chart?.symbol ?? '—'} · {bars.length} 根
          {isAggregated ? `（${tf} 聚合）` : `（${tf} 原始）`}
          {chart?.trades ? ` · 开平仓 ${chart.trades.length} 笔` : ''}
          {orders && orders.length > 0 ? ` · 委托 ${orders.length} 条` : ''}
        </span>

        {/*
          上下文行情徽标（BT-KLINE-CONTEXT-01）：必须与「本 run 消费区间」分开讲，
          否则看的人会把上下文当成回测吃过的数据。
        */}
        {(chart?.candles_context_days ?? 0) > 0 && (
          <span
            className="text-[9px] font-mono px-2 py-0.5 rounded border border-amber-700/60 bg-amber-900/20 text-amber-300"
            title={
              `上下文行情：run 消费区间之外前后各 ${chart?.candles_context_days} 天真实行情`
              + `（前 ${chart?.candles_context_before ?? 0} 根 / 后 ${chart?.candles_context_after ?? 0} 根），`
              + `**不是本 run 消费过的数据**；`
              + (chart?.candles_run_range
                ? `run 区间 ${fmtUtc(chart.candles_run_range.start_ms)} ~ ${fmtUtc(chart.candles_run_range.end_ms)} UTC（图上「run」标签与紧随其后的方块之间）`
                : '')
              + (chart?.candles_context_note ? `；${chart.candles_context_note}` : '')
            }
          >
            上下文 ±{chart?.candles_context_days} 天 · 前{chart?.candles_context_before ?? 0}/后
            {chart?.candles_context_after ?? 0} 根
            {chart?.candles_context_tf ? `(${chart.candles_context_tf})` : ''}
            {chart?.candles_run_range
              ? ` · run 区间 ${fmtUtc(chart.candles_run_range.start_ms).split(' ')[0]} ${fmtUtc(chart.candles_run_range.start_ms).split(' ')[1]}~${fmtUtc(chart.candles_run_range.end_ms).split(' ')[1]}`
              : ''}
          </span>
        )}

        {/* 运行身份元数据小标签 */}
        <div className="flex items-center gap-1.5 text-[9px] font-mono text-neutral-400 bg-neutral-900/60 px-2 py-0.5 rounded border border-neutral-800">
          <span title={`回测运行ID: ${runId ?? '—'}`}>
            ID: <b className="text-neutral-300 font-semibold">{runId ? (runId.length > 18 ? `${runId.slice(0, 10)}...${runId.slice(-6)}` : runId) : '—'}</b>
          </span>
          <span className="text-neutral-600">|</span>
          <span title={`策略版本: ${strategyVersion ?? 'WX_EVENT_TREND_V0_1'}`}>
            策略: <b className="text-neutral-300">{strategyVersion ?? 'WX_EVENT_TREND_V0_1'}</b>
          </span>
          <span className="text-neutral-600">|</span>
          <span title="时区: UTC（北京时间为 UTC+8）">UTC</span>
          {chart?.window?.start_ms && chart?.window?.end_ms && (
            <>
              <span className="text-neutral-600">|</span>
              <span title={`区间: ${fmtUtc(chart.window.start_ms)} ~ ${fmtUtc(chart.window.end_ms)} UTC`}>
                {fmtUtc(chart.window.start_ms).split(' ')[0]} ~ {fmtUtc(chart.window.end_ms).split(' ')[0]}
              </span>
            </>
          )}
        </div>

        <span className="ds-pagehead__spacer" />

        {/* 多币组合标的快速切换下拉框 */}
        {symbols && symbols.length > 1 && (
          <div className="flex items-center gap-1.5 text-[10px] font-mono bg-neutral-900/80 px-2 py-0.5 rounded border border-neutral-700">
            <span className="text-neutral-400">选择标的:</span>
            <select
              data-testid="lab-chart-symbol-select"
              value={activeSymbol || (chart?.symbol?.replace('USDT', '') ?? '')}
              onChange={(e) => onSelectSymbol?.(e.target.value)}
              className="bg-transparent text-emerald-400 font-semibold focus:outline-none cursor-pointer"
            >
              {symbols.map((sym) => (
                <option key={sym} value={sym} className="bg-neutral-900 text-neutral-200">
                  {sym}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* 交易上一笔/下一笔导航 */}
        {tradeList.length > 0 && (
          <div className="flex items-center gap-1 bg-neutral-900/60 p-0.5 rounded border border-neutral-800 text-[10px]">
            <button
              type="button"
              className="px-1.5 py-0.5 hover:bg-neutral-800 rounded text-neutral-300 transition-colors"
              onClick={handlePrevTrade}
              title="定位到上一笔成交"
            >
              ◀ 上一笔
            </button>
            <span className="px-1 text-neutral-400 font-mono text-[9px]">
              {currentTradeIndex >= 0 ? `${currentTradeIndex + 1}/${tradeList.length}` : `0/${tradeList.length}`}
            </span>
            <button
              type="button"
              className="px-1.5 py-0.5 hover:bg-neutral-800 rounded text-neutral-300 transition-colors"
              onClick={handleNextTrade}
              title="定位到下一笔成交"
            >
              下一笔 ▶
            </button>
          </div>
        )}

        {/* 周期选择：小时线(1h)优先默认 – 5 分线。
            比执行周期更粗 ⇒ 由本 run 真实 K 线按 UTC 边界精确聚合；更细的 5m 明确禁用。 */}
        <div className="ds-tfgroup" role="group" aria-label="K 线周期" data-testid="lab-chart-tf">
          {LAB_CHART_TF_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              disabled={!o.enabled}
              title={
                o.reason ??
                (o.value === actualTf
                  ? '真实 K 线（原始粒度，未做任何二次聚合）'
                  : `由本标的 ${actualTf} 真实 K 线按 UTC 边界精确聚合（O=首/H=最高/L=最低/C=末/V=求和）`)
              }
              aria-pressed={tf === o.value}
              onClick={() => {
                if (!o.enabled) return;
                setTf(o.value);
                onTimeframeChange?.(o.value);
              }}
              data-tf={o.value}
              className={`ds-tfgroup__item${tf === o.value ? ' is-on' : ''}`}
            >
              {o.label}
            </button>
          ))}
        </div>

        {/* 展开/紧凑切换 */}
        <button
          type="button"
          className="ds-btn ds-btn--ghost ds-btn--sm"
          onClick={() => setIsExpanded(!isExpanded)}
          title="切换主图高度（展开大图 / 紧凑视图）"
        >
          {isExpanded ? '收起大图' : '展开大图'}
        </button>

        {selectedTrade && (
          <button
            type="button"
            className="ds-btn ds-btn--ghost ds-btn--sm"
            onClick={() => onSelectTrade(null)}
          >
            清除选中
          </button>
        )}

        {/* 读时重建标注：这份载荷不是 run 目录里的原始 chart.json（原文件缺失） */}
        {chart?.derived_from && (
          <span className="ds-badge ds-badge--warn" data-testid="lab-chart-derived" title={chart.derived_from}>
            读时重建
          </span>
        )}

        {/* 明确区分手动视窗恢复与自动聚焦 */}
        {manualViewport ? (
          <span className="ds-badge ds-badge--info font-mono text-[10px]" data-testid="lab-viewport-mode" title="用户先前手动平移/缩放的视窗范围已恢复">
            🎯 视窗: 手动已恢复
          </span>
        ) : focusTimeMs ? (
          <span className="ds-badge ds-badge--indigo font-mono text-[10px]" data-testid="lab-viewport-mode" title="根据所选订单事件时刻自动聚焦前后12根K线">
            🎯 视窗: 订单聚焦
          </span>
        ) : null}
      </div>

      {/* 图例：设计稿只有一行灰字。开/平/止损留在明面，
          候选信号 / C_entry / D_intrabar / EOD / 成交时刻口径收进折叠 —— 信息不删，但不占视野。 */}
      <div className="ds-chartlegend">
        <span className="ds-chartlegend__up">▲ 开仓</span>
        <span className="ds-chartlegend__dn">▼ 平仓</span>
        <span className="ds-chartlegend__stop">— 保护止损</span>
        <span className="ds-chartlegend__muted">时间轴 UTC（北京 = UTC+8）</span>
        <details className="ds-chartlegend__more">
          <summary>图例与口径</summary>
          <div>
            <span>■ 候选信号（箱体突破，落在 K 线实体内）</span>
            {/* 确认点是第四个时刻（FT-REPLAY-01）：C0 模式没有确认环节 ⇒ 该模式无此标记 */}
            {(chart?.confirms?.length ?? 0) > 0 && (
              <span>● 确认（确认 K 线收盘，与信号分开画；不是成交）</span>
            )}
            <span>▲ 实际入场 / ▼ 实际出场（真实成交时刻）</span>
            <span>● C_entry 入场</span>
            <span>● D_intrabar 盘中</span>
            <span>● EOD 日终</span>
            {noFixedTp === true && <span>止盈：无固定止盈</span>}
            {noFixedTp === null && <span>止盈规则字段缺失 ⇒ 不做假设</span>}
            {chart?.time_precision &&
              Object.entries(chart.time_precision).map(([k, v]) => (
                <span key={k}>
                  {k}：{String(v)}
                </span>
              ))}
          </div>
        </details>
      </div>

      {/* 悬停读数 */}
      <div className="ds-hoverbar">
        {hoveredBar ? (
          <>
            <span className="text-neutral-500">
              时刻 {fmtUtc(hoveredBar.timestamp)} UTC (闭 {fmtUtc(hoveredBar.timestamp + (TF_MS[tf] ?? 3_600_000))})
            </span>
            {focusTimeMs && Math.abs(focusTimeMs - hoveredBar.timestamp) <= (TF_MS[tf] ?? 3_600_000) && (
              <span className="px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-700/60 font-mono text-xs" data-testid="lab-focus-event-badge">
                🎯 定位事件 {fmtUtc(focusTimeMs)} UTC ({tf}归桶 {fmtUtc(hoveredBar.timestamp)})
              </span>
            )}
            {hoveredBucketOrders.length > 0 && (
              <span className="px-1.5 py-0.5 rounded bg-amber-950 text-amber-200 border border-amber-700/60 font-mono text-xs" data-testid="lab-bucket-orders-badge">
                {hoveredBucketOrders.length === 1 ? (
                  <>🎯 {currentSymbolNorm ? `${currentSymbolNorm} ` : ''}委托 {fmtUtc(hoveredBucketOrders[0].ts_ms)} @{fmtPrice(hoveredBucketOrders[0].price)} ({hoveredBucketOrders[0].side === 'buy' ? '买入' : '卖出'})</>
                ) : (
                  <>🎯 同柱 {currentSymbolNorm ? `${currentSymbolNorm} ` : ''}{hoveredBucketOrders.length} 笔委托: {hoveredBucketOrders.map((o, idx) => `#${idx + 1} ${fmtUtc(o.ts_ms).slice(5)} @${fmtPrice(o.price)}`).join(' | ')}</>
                )}
              </span>
            )}
            {crossSymbolBucketOrders.length > 0 && (
              <span className="px-1.5 py-0.5 rounded bg-neutral-900 text-neutral-400 border border-neutral-700/60 font-mono text-xs" data-testid="lab-cross-symbol-badge" title="回测组合中其他标的在此柱期间发生的并发委托，不属于当前单币K线">
                🌐 跨币并发: {crossSymbolBucketOrders.map((o) => `${(o.symbol || '').replace('USDT', '')} @${fmtPrice(o.price)}`).join(' | ')}
              </span>
            )}
            <span>开 <b className="text-neutral-100">{fmtPrice(hoveredBar.open)}</b></span>
            <span>高 <b className="text-neutral-100">{fmtPrice(hoveredBar.high)}</b></span>
            <span>低 <b className="text-neutral-100">{fmtPrice(hoveredBar.low)}</b></span>
            <span>
              收 <b className={(hoveredBar.close ?? 0) >= (hoveredBar.open ?? 0) ? 'text-emerald-600' : 'text-rose-600'}>
                {fmtPrice(hoveredBar.close)}
              </b>
            </span>
            <span>量（base） <b className="text-neutral-100">{fmtVolume(hoveredBar.volume)}</b></span>
            <span>
              持仓量 <b className="text-neutral-100">{fmtOi(hoverOi?.oiAmount)}</b>
              <span className="text-neutral-400">（5m 归档最近一条）</span>
            </span>
            {hoveredSignal ? (
              <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
                真实信号：{hoveredSignal.kind} / {hoveredSignal.direction}
                {hoveredSignal.stop_price !== null ? ` · 止损 ${fmtPrice(hoveredSignal.stop_price)}` : ''}
                {hoveredSignal.atr !== null ? ` · ATR ${fmtPrice(hoveredSignal.atr)}` : ''}
                {hoveredSignal.oi_quality ? ` · OI ${hoveredSignal.oi_quality}` : ''}
              </span>
            ) : (
              <span className="text-neutral-400">该根无真实信号记录</span>
            )}
          </>
        ) : (
          <span className="text-neutral-400">悬停 K 线看该根真实 OHLC / 量 / 信号</span>
        )}
      </div>

      <div
        className={`relative w-full ${isExpanded ? 'h-[460px]' : 'h-[280px]'} bg-white transition-all duration-200`}
        title="悬停看该根真实 OHLC / 原始成交量 / 信号；点击下方明细行或图上开/平仓标记 ⇒ 定位该笔 entry/exit 区间并高亮"
      >
        {selectedEvent && (
          <div className="absolute right-2 top-2 z-20 max-w-[360px] rounded border border-neutral-600 bg-neutral-950/95 p-2 text-[11px] text-neutral-100 shadow-lg" role="status">
            <button className="float-right ml-2 text-neutral-400" onClick={() => setSelectedEvent(null)} aria-label="关闭事件详情">×</button>
            <b>{selectedEvent.event_type}</b> · {fmtUtc(selectedEvent.event_time)} UTC
            <div>价格 {fmtPrice(selectedEvent.price)} · 来源 {selectedEvent.source}</div>
            <div>信号 {selectedEvent.signal_id ?? '—'} · 交易 {selectedEvent.trade_id ?? '—'}</div>
            {selectedEvent.reason && <div>{selectedEvent.reason}</div>}
          </div>
        )}
        {bars.length === 0 ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-xs text-neutral-500 gap-1">
            <span>{loading ? '正在读取真实图表载荷…' : '无真实 K 线数据（不绘制任何合成 K 线 / 静态 demo）'}</span>
            {error && <span className="text-rose-600">取数错误：{error}</span>}
            {missingReason && <span className="text-amber-700">后台说明：{missingReason}</span>}
          </div>        ) : (
          <CandleVolumeChart
            key={viewKey}
            bars={bars}
            timeframe={tf}
            gridTimes={gridTimes}
            viewKey={viewKey}
            initialViewport={computedInitialViewport}
            onViewportChange={onViewportChange}
            onTimeClick={onChartTimeClick}
            onHover={onHover}
            onReady={onReady}
            syncHub={syncHub}
            extraMarkers={extraMarkers}
            /* 成交量已拆成下面独立的 84px 一条（设计稿三段竖排：K 线 / 成交量 / 持仓量） */
            showVolume={false}
            /* 下面还有「成交量」「持仓量（OI）」两条 ⇒ 时间轴只在最底那张显示，
               否则同一屏出现两三排日期刻度（设计稿只有最底一行）。 */
            hideTimeAxis
          />
        )}
      </div>

      {(chart?.events?.length ?? 0) > 0 && (
        <div className="max-h-28 overflow-auto border-t border-neutral-800 bg-neutral-950/90 px-2 py-1" aria-label="回测事件时间线">
          {chart!.events!.map((event) => (
            <button key={event.event_id} className="mr-1 mb-1 rounded border border-neutral-700 px-1.5 py-0.5 text-[10px] text-neutral-200 hover:bg-neutral-800"
              onClick={() => {
                setSelectedEvent(event);
                const span = (TF_MS[tf] ?? 3_600_000) * 30;
                handle?.setViewport(event.event_time - span / 2, event.event_time + span / 2);
              }}>
              {event.event_type} · {fmtUtc(event.event_time)}
            </button>
          ))}
        </div>
      )}

      {/* ── 成交量（独立一条，设计稿 84px）──────────────────────────────────
          设计稿在 K 线与 OI 之间还有一条独立「成交量」，自带左标签与右轴刻度。
          这里画本 run 的真实 base 量（不是 USDT 名义额），涨绿跌红与蜡烛同一套色。 */}
      <div className="relative w-full h-[84px] border-t border-neutral-100 bg-white">
        <span className="absolute left-2 top-0.5 z-10 text-[10px] text-neutral-500 pointer-events-none">
          成交量
        </span>
        {bars.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-[10px] text-neutral-400">
            无真实成交量
          </div>
        ) : (
          <VolumeBarChart
            key={`vol:${viewKey}`}
            bars={bars}
            emptyText="无真实成交量"
            gridTimes={gridTimes}
            viewKey={`vol:${viewKey}`}
            initialViewport={computedInitialViewport}
            onViewportChange={onViewportChange}
            onHover={onVolumeHover}
            onReady={onVolumeReady}
            syncHub={syncHub}
          />
        )}
      </div>

      {/* ── 持仓量（OI）副图 ──────────────────────────────────────────────
          设计稿在成交量下面还有一条「持仓量（OI）」。这里画**真实 5m 归档**按当前
          展示周期分桶后的总量柱（柱色不承载增减语义）；与 K 线共享时间轴（同一个同步组）。
          取不到数据就写清楚原因，不画空轴冒充「有 OI」。 */}
      <div className="relative w-full h-[92px] border-t border-neutral-100 bg-white">
        <span className="absolute left-2 top-0.5 z-10 text-[10px] text-neutral-500 pointer-events-none">
          持仓量（OI）
        </span>
        {oiAligned.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-[10px] text-neutral-500 px-2 text-center">
            {oiErr ? `OI 取数失败：${oiErr}` : '该窗口没有 5m OI 归档（不补值、不画假柱）'}
          </div>
        ) : (
          <OiBarChart
            key={`oi:${viewKey}`}
            points={oiAligned}
            unit="amount"
            emptyText="该窗口没有 5m OI 归档（不补值）"
            gridTimes={gridTimes}
            viewKey={`oi:${viewKey}`}
            initialViewport={computedInitialViewport}
            onViewportChange={onViewportChange}
            onHover={onOiHover}
            onReady={onOiReady}
            syncHub={syncHub}
          />
        )}
      </div>

      {/* 选中定位读数：**只有真的选中某笔才出现**。
          未选中时不再常驻「点击下方明细行…」这类操作提示 —— 它属于说明而非数据，
          已收进图表区 title（悬停可见），不再占一行高度。 */}
      {selectedTrade && (() => {
        const entT = selectedTrade.entry?.t ?? (selectedTrade as any).entry_time ?? (selectedTrade as any).entry_time_ms ?? null;
        const entP = selectedTrade.entry?.price ?? (selectedTrade as any).entry_price ?? null;
        const extT = selectedTrade.exit?.t ?? (selectedTrade as any).exit_time ?? (selectedTrade as any).exit_time_ms ?? null;
        const extP = selectedTrade.exit?.price ?? (selectedTrade as any).exit_price ?? null;
        const extR = selectedTrade.exit?.reason ?? (selectedTrade as any).exit_reason ?? '';
        return (
          <div className="px-3 py-1.5 border-t border-neutral-200 bg-neutral-50 text-[10px] font-mono flex flex-wrap gap-x-4 gap-y-1">
            <span className="text-emerald-700 font-semibold">已选中 {selectedTrade.trade_id}</span>
            <span>entry {fmtUtc(entT)} @ {fmtPrice(entP)}</span>
            <span>
              exit {fmtUtc(extT)} @ {fmtPrice(extP)}（{exitReasonLabel(extR)}）
            </span>
            <span>
              止损轨迹 {stopTraceInfo?.n ?? 0} 点
              {stopTraceInfo?.n === 0 && '（账本无止损点 ⇒ 不合成）'}
              {stopTraceInfo?.n === 1 &&
                `（仅初始止损 ${fmtPrice(stopTraceInfo.init)}，无后续调整 ⇒ 不合成轨迹）`}
              {stopTraceInfo !== null && stopTraceInfo.n > 1 &&
                `（含 ${stopTraceInfo.adjustments} 次调整）`}
            </span>
            {locateInfo && (
              <span className={locateInfo.ok ? 'text-neutral-400' : 'text-amber-700'}>
                定位 {fmtUtc(locateInfo.fromMs)} → {fmtUtc(locateInfo.toMs)}
                {locateInfo.ok ? '' : '（未落实：图未就绪）'}
              </span>
            )}
          </div>
        );
      })()}
    </div>
  );
};

export default LabChartPanel;
