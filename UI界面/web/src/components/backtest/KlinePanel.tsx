/**
 * K 线面板
 * ----------------------------------------------------------------------------
 * 复刻设计稿的「周期切换 + 图内图例 + K线/成交量 + 买卖标记 + 双均线」。
 *
 * 三个诚实性约束：
 *   1. 周期只给**能从 15m 聚合出来**的档位（15m/1h/4h/1d）。1m/5m/分时禁用并写明
 *      原因 —— 从粗粒度反推细粒度就是编造数据。
 *   2. 均线是**收盘价的简单移动平均**，窗口不足的段不出线（不画假线）。
 *   3. 买卖标记只来自 `chart.trades` 的真实开平仓时点，不推断、不补点。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { LineSeries, type IChartApi, type ISeriesApi, type LineData } from 'lightweight-charts';
import {
  CandleVolumeChart,
  buildCandleGrid,
  createChartSyncHub,
  toChartSeconds,
  type ChartHandle,
  type ChartHover,
} from '../LightweightChart';
import type { BtChartPayload } from '../../api/backtestReview';
import { bucketCandles, normalizeCandles, sma, TF_OPTIONS, TF_SECONDS, toTimeframe, SOURCE_TIMEFRAME_SEC } from '../../utils/candles';
import { useChartPalette } from '../../design/chartTheme';
import { DASH, fmtCompact, fmtPrice } from '../../utils/labFormat';

export interface KlinePanelProps {
  chart: BtChartPayload | null;
  loading: boolean;
  error: string | null;
  /** 图表高度（px），默认 300 */
  height?: number;
}

const DEFAULT_TF = '1h';

export const KlinePanel: React.FC<KlinePanelProps> = ({ chart, loading, error, height = 290 }) => {
  const [tf, setTf] = useState<string>(DEFAULT_TF);
  const [handle, setHandle] = useState<ChartHandle | null>(null);
  const [hover, setHover] = useState<ChartHover | null>(null);
  const palette = useChartPalette();

  /* candles 有两种线上形态（数组元组 / 对象），一律走 normalizeCandles。
   * 它同时返回被丢弃的根数，下面会如实显示 —— 不能让人以为图上就是全部。 */
  const { bars: rawBars, skipped } = useMemo(
    () => normalizeCandles(chart?.candles),
    [chart],
  );

  const baseTf = chart?.exec_timeframe || '15m';
  const bars = useMemo(() => {
    const bucket = TF_SECONDS[tf] ?? 3600;
    return bucketCandles(rawBars, bucket, SOURCE_TIMEFRAME_SEC);
  }, [rawBars, tf]);

  const gridTimes = useMemo(() => buildCandleGrid(bars), [bars]);
  const viewKey = useMemo(
    () => `bt:${chart?.symbol ?? 'none'}:${tf}:${chart?.window?.start_ms ?? 0}`,
    [chart, tf],
  );
  const syncHub = useMemo(() => createChartSyncHub(), [viewKey]);

  const onReady = useMemo(() => (h: ChartHandle) => setHandle(h), []);
  const onHover = useMemo(() => (h: ChartHover | null) => setHover(h), []);
  // 单图：视窗变化不需要上报给别的图，但组件契约要求提供回调
  const onViewportChange = useMemo(() => () => {}, []);

  /* ── 买卖标记：真实开平仓时点 ─────────────────────────────────────── */
  const extraMarkers = useMemo(() => {
    const out: {
      time: ReturnType<typeof toChartSeconds>;
      position: 'aboveBar' | 'belowBar';
      shape: 'arrowUp' | 'arrowDown';
      color: string;
      text: string;
      size?: number;
    }[] = [];
    for (const t of chart?.trades ?? []) {
      const et = t.entry?.t;
      const xt = t.exit?.t;
      if (typeof et === 'number' && et > 0) {
        const long = (t.direction ?? 'long') === 'long';
        out.push({
          time: toChartSeconds(et),
          position: long ? 'belowBar' : 'aboveBar',
          shape: long ? 'arrowUp' : 'arrowDown',
          color: palette.entry,
          size: 2,
          /* 规格 C4：标记不带文字 —— 箭头旁挂「买入/卖出」会糊在蜡烛上，
             成交价与方向由下方读数条承担。 */
          text: '',
        });
      }
      if (typeof xt === 'number' && xt > 0) {
        const long = (t.direction ?? 'long') === 'long';
        out.push({
          time: toChartSeconds(xt),
          position: long ? 'aboveBar' : 'belowBar',
          shape: long ? 'arrowDown' : 'arrowUp',
          color: palette.exit,
          size: 2,
          text: '',
        });
      }
    }
    return out.sort((a, b) => Number(a.time) - Number(b.time));
  }, [chart, palette]);

  /* ── MA20 / MA60：挂在 onReady 句柄上，不改 K 线内核 ───────────────── */
  const maRef = useRef<{
    chart: IChartApi;
    fast: ISeriesApi<'Line'>;
    slow: ISeriesApi<'Line'>;
  } | null>(null);

  useEffect(() => {
    const api = handle?.chart;
    if (!api) return;
    // 图表被重建过（切 run / 切周期会换 syncHub）⇒ 旧 series 已随实例销毁
    if (maRef.current && maRef.current.chart !== api) maRef.current = null;

    if (!maRef.current) {
      const fast = api.addSeries(LineSeries, {
        color: palette.maFast,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      const slow = api.addSeries(LineSeries, {
        color: palette.maSlow,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      maRef.current = { chart: api, fast, slow };
    } else {
      maRef.current.fast.applyOptions({ color: palette.maFast });
      maRef.current.slow.applyOptions({ color: palette.maSlow });
    }

    const closes = bars.map((b) => b.close);
    const toLine = (values: (number | null)[]): LineData[] => {
      const out: LineData[] = [];
      for (let i = 0; i < bars.length; i += 1) {
        const v = values[i];
        if (typeof v !== 'number' || !Number.isFinite(v)) continue;
        const time = toChartSeconds(bars[i].timestamp);
        if (!time) continue;
        out.push({ time, value: v });
      }
      return out;
    };

    maRef.current.fast.setData(toLine(sma(closes, 20)));
    maRef.current.slow.setData(toLine(sma(closes, 60)));
  }, [handle, bars, palette]);

  /**
   * 默认视窗：只显示最近 150 根。
   * 4 千多根一次性铺满整屏时，单根宽度不足 0.2px，K 线会糊成一条色带；
   * 设计稿的观感是「看最近一段」。用户仍可自由缩放 / 拖到更早。
   */
  useEffect(() => {
    const api = handle?.chart;
    if (!api || bars.length === 0) return;
    if (bars.length <= 150) return;
    try {
      api.timeScale().setVisibleLogicalRange({ from: bars.length - 150, to: bars.length + 2 });
    } catch {
      /* 图表尚未就绪，下一轮会重试 */
    }
  }, [handle, bars]);

  /* ── 图例：hover 优先，否则末根 ──────────────────────────────────── */
  const indexByTs = useMemo(() => {
    const m = new Map<number, number>();
    bars.forEach((b, i) => m.set(b.timestamp, i));
    return m;
  }, [bars]);

  const shown = hover?.candle ?? bars[bars.length - 1] ?? null;
  const shownIdx = shown ? (indexByTs.get(shown.timestamp) ?? -1) : -1;
  const ma20 = useMemo(() => sma(bars.map((b) => b.close), 20), [bars]);
  const ma60 = useMemo(() => sma(bars.map((b) => b.close), 60), [bars]);

  const o = shown?.open ?? null;
  const h = shown?.high ?? null;
  const l = shown?.low ?? null;
  const c = shown?.close ?? null;
  const chg = o != null && c != null ? c - o : null;
  const chgPct = o != null && c != null && o !== 0 ? ((c - o) / o) * 100 : null;
  const up = chg != null && chg >= 0;

  return (
    <section className="ds-card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="ds-card__head" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="ds-card__title">价格走势</span>
        <div className="ds-tfbar" role="group" aria-label="K 线周期">
          {TF_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              type="button"
              className="ds-tf"
              aria-pressed={tf === opt.id}
              disabled={!opt.available}
              title={opt.reason}
              onClick={() => opt.available && setTf(opt.id)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <span className="ds-card__meta">
          {baseTf} 原始 · {bars.length.toLocaleString('en-US')} 根
          {skipped > 0 ? `（${skipped.toLocaleString('en-US')} 根字段不全已跳过）` : ''}
        </span>
      </div>

      <div className="ds-chartlegend">
        <span>
          <b>{chart?.symbol ?? DASH}</b> · {tf} · Binance
        </span>
        <span className="ds-chartlegend__num">
          开 {fmtPrice(o)} 高 {fmtPrice(h)} 低 {fmtPrice(l)} 收{' '}
          <b style={{ color: up ? 'var(--ds-pos)' : 'var(--ds-neg)' }}>{fmtPrice(c)}</b>
        </span>
        <span className="ds-chartlegend__num" style={{ color: up ? 'var(--ds-pos)' : 'var(--ds-neg)' }}>
          {chg == null ? DASH : `${chg >= 0 ? '+' : ''}${fmtPrice(chg)}`}
          {chgPct == null ? '' : ` (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%)`}
        </span>
        <span className="ds-chartlegend__num">
          <span className="ds-chartlegend__dot" style={{ background: palette.maFast }} />
          MA20 {shownIdx >= 0 ? fmtPrice(ma20[shownIdx]) : DASH}
        </span>
        <span className="ds-chartlegend__num">
          <span className="ds-chartlegend__dot" style={{ background: palette.maSlow }} />
          MA60 {shownIdx >= 0 ? fmtPrice(ma60[shownIdx]) : DASH}
        </span>
        <span className="ds-chartlegend__num">Vol {fmtCompact(shown?.volume ?? null)}</span>
      </div>

      <div className="ds-chartbox" style={{ height }}>
        {error ? (
          <div className="ds-bt-empty">K 线取数失败：{error}</div>
        ) : loading ? (
          <div className="ds-bt-empty">加载 K 线…</div>
        ) : bars.length === 0 ? (
          <div className="ds-bt-empty">该 run 没有 K 线数据</div>
        ) : (
          <CandleVolumeChart
            bars={bars}
            timeframe={toTimeframe(tf)}
            gridTimes={gridTimes}
            viewKey={viewKey}
            initialViewport={null}
            onViewportChange={onViewportChange}
            onHover={onHover}
            onReady={onReady}
            syncHub={syncHub}
            extraMarkers={extraMarkers}
          />
        )}
      </div>
    </section>
  );
};
