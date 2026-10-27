/**
 * K 线载荷转换与周期聚合
 * ----------------------------------------------------------------------------
 * 后端 `/chart` 给的 candles 是**数组元组** `[ts_ms, open, high, low, close, volume]`
 * （不是对象，踩过坑），这里统一转成项目内部的 `Candle` 结构。
 *
 * 聚合是**纯 OHLC 重采样**：同一时间桶内 open 取首根、close 取末根、high/low 取极值、
 * volume 求和。这是对真实数据的重新分箱，不产生任何新信息，也不补空桶。
 */

import type { Candle, Timeframe } from '../types/crypto';

/** 后端执行周期 15m 是原始粒度；更粗的周期由它聚合而来。 */
export const SOURCE_TIMEFRAME_SEC = 900;

export const TF_SECONDS: Record<string, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1h': 3600,
  '4h': 14400,
  '8h': 28800,
  '12h': 43200,
  '1d': 86400,
};

/**
 * 周期切换条的选项。
 * `available=false` 的项**禁用并给原因**：数据只有 15m，1m / 5m 无法从更粗的
 * 数据反推（那会变成编造），因此不提供假图。
 */
export interface TfOption {
  id: string;
  label: string;
  available: boolean;
  reason?: string;
}

export const TF_OPTIONS: TfOption[] = [
  { id: 'tick', label: '分时', available: false, reason: '本 run 未落盘逐笔成交，分时无法还原' },
  { id: '1m', label: '1m', available: false, reason: '本 run 原始粒度为 15m，无法反推 1m' },
  { id: '5m', label: '5m', available: false, reason: '本 run 原始粒度为 15m，无法反推 5m' },
  { id: '15m', label: '15m', available: true },
  { id: '1h', label: '1h', available: true },
  { id: '4h', label: '4h', available: true },
  { id: '1d', label: '1D', available: true },
];

/**
 * 归一化一根 K 线。
 * ----------------------------------------------------------------------------
 * **同一端点会返回两种形态**（2026-10-17 实测）：
 *   · 多数 run：数组元组 `[t, o, h, l, c, v]`
 *   · 部分 run（如 ZRXUSDT 1792219657）：对象 `{ t, o, h, l, c, v }`
 * 只按元组解构会在对象上抛 `TypeError: xxx is not iterable`，
 * 而 React 里一个 render 期异常会**卸载整棵树** ⇒ 用户看到的就是整页白屏。
 *
 * 因此这里两种形态都收，且任何一根不合法就返回 null（由调用方丢弃并计数），
 * 绝不补 0、绝不猜时间。
 */
export function normalizeCandle(raw: unknown): Candle | null {
  let t: unknown;
  let o: unknown;
  let h: unknown;
  let l: unknown;
  let c: unknown;
  let v: unknown;

  if (Array.isArray(raw)) {
    [t, o, h, l, c, v] = raw as unknown[];
  } else if (raw && typeof raw === 'object') {
    const r = raw as Record<string, unknown>;
    t = r.t ?? r.time ?? r.timestamp;
    o = r.o ?? r.open;
    h = r.h ?? r.high;
    l = r.l ?? r.low;
    c = r.c ?? r.close;
    v = r.v ?? r.volume;
  } else {
    return null;
  }

  const num = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
  if (!num(t) || t <= 0) return null;
  if (!num(o) || !num(h) || !num(l) || !num(c)) return null;

  return {
    time: new Date(t).toISOString(),
    timestamp: t,
    open: o,
    high: h,
    low: l,
    close: c,
    volume: num(v) ? v : null,
    volumeUsd: null,
    // 历史 K 线一律视为已收盘：回测窗口的最后一根也是收盘后落盘的
    isClosed: true,
  };
}

/**
 * 批量归一化，并把「被丢掉多少根」一起返回 —— 页面上要如实显示跳过数，
 * 不能让用户以为图上那 9000 根就是全部。
 */
export function normalizeCandles(list: unknown): { bars: Candle[]; skipped: number } {
  if (!Array.isArray(list)) return { bars: [], skipped: 0 };
  const bars: Candle[] = [];
  let skipped = 0;
  for (const raw of list) {
    const bar = normalizeCandle(raw);
    if (bar) bars.push(bar);
    else skipped += 1;
  }
  return { bars, skipped };
}

/**
 * 按秒桶聚合。`bucketSec <= 源周期` 时原样返回（不做无意义的「聚合」）。
 * 桶边界按 UTC 对齐（`floor(ts / bucket) * bucket`），与交易所 K 线边界一致。
 */
export function bucketCandles(
  candles: Candle[],
  bucketSec: number,
  sourceSec = SOURCE_TIMEFRAME_SEC,
): Candle[] {
  if (!Number.isFinite(bucketSec) || bucketSec <= sourceSec) return candles;
  if (candles.length === 0) return candles;

  const out: Candle[] = [];
  let cur: Candle | null = null;
  let curBucket = -1;

  for (const k of candles) {
    const bucket = Math.floor(k.timestamp / 1000 / bucketSec) * bucketSec;
    if (bucket !== curBucket) {
      if (cur) out.push(cur);
      curBucket = bucket;
      cur = {
        ...k,
        timestamp: bucket * 1000,
        time: new Date(bucket * 1000).toISOString(),
        isClosed: true,
      };
      continue;
    }
    if (!cur) continue;
    // 同桶合并：high/low 取极值，close 跟随最后一根，volume 累加
    const high = [cur.high, k.high].filter((x): x is number => typeof x === 'number');
    const low = [cur.low, k.low].filter((x): x is number => typeof x === 'number');
    cur.high = high.length ? Math.max(...high) : null;
    cur.low = low.length ? Math.min(...low) : null;
    if (typeof k.close === 'number') cur.close = k.close;
    if (typeof k.volume === 'number') {
      cur.volume = (cur.volume ?? 0) + k.volume;
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** 简单移动平均。前 period-1 个点不足窗口 ⇒ null（不画假线）。 */
export function sma(values: (number | null)[], period: number): (number | null)[] {
  if (period <= 0) return values.map(() => null);
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  let count = 0;
  const window: (number | null)[] = [];
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    window.push(v);
    if (typeof v === 'number' && Number.isFinite(v)) {
      sum += v;
      count += 1;
    }
    if (window.length > period) {
      const dropped = window.shift();
      if (typeof dropped === 'number' && Number.isFinite(dropped)) {
        sum -= dropped;
        count -= 1;
      }
    }
    if (window.length === period && count === period) out[i] = sum / period;
  }
  return out;
}

/** 把 Timeframe 字符串映射到秒；未知返回 null（不猜）。 */
export function timeframeSeconds(tf: string | null | undefined): number | null {
  if (!tf) return null;
  return TF_SECONDS[tf] ?? null;
}

/** 内部 Timeframe 类型 → 本页的周期 id（用于传给图表组件） */
export function toTimeframe(id: string): Timeframe {
  return (TF_SECONDS[id] ? id : '15m') as Timeframe;
}
