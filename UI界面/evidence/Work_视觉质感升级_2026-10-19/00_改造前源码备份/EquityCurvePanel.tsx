/**
 * 收益曲线面板（策略 vs 基准 / 回撤 / 资金）
 * ----------------------------------------------------------------------------
 * 自己建 chart 实例（不用 `CandleVolumeChart`，那是 K 线专用）。要点：
 *   · 1.7 万个 15m 点必须设 `timeScale.minBarSpacing: 0.01`，否则 `fitContent()`
 *     只能铺出末尾一小段（踩过坑）。
 *   · 基准是**纯价格口径**（后端 `benchmark_buy_hold.cost_model = price_only`），
 *     不含手续费/滑点/资金费，图上必须标明，否则会被读成「策略跑输/跑赢」的等价对比。
 *   · 三种模式的数据都在 `useMemo` 里预计算，切 tab 只 `setData`，不重建实例。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { BtChartPayload } from '../../api/backtestReview';
import { useChartPalette } from '../../design/chartTheme';
import { DASH, fmtPct } from '../../utils/labFormat';

type Mode = 'return' | 'drawdown' | 'equity';

const MODES: { id: Mode; label: string }[] = [
  { id: 'return', label: '收益曲线' },
  { id: 'drawdown', label: '回撤曲线' },
  { id: 'equity', label: '资金曲线' },
];

const toSec = (ms: number): UTCTimestamp => Math.floor(ms / 1000) as UTCTimestamp;

export interface EquityCurvePanelProps {
  chart: BtChartPayload | null;
  loading: boolean;
  error: string | null;
  height?: number;
}

export const EquityCurvePanel: React.FC<EquityCurvePanelProps> = ({
  chart,
  loading,
  error,
  height = 196,
}) => {
  const [mode, setMode] = useState<Mode>('return');
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const stratRef = useRef<ISeriesApi<'Line'> | null>(null);
  const benchRef = useRef<ISeriesApi<'Line'> | null>(null);
  const palette = useChartPalette();

  const points = chart?.equity_curve ?? [];
  const benchPoints = chart?.benchmark?.equity_series ?? [];
  const initial = points.length ? points[0].equity : null;
  /**
   * 容器是**条件渲染**的（加载中/空数据时不渲染 div）。
   * 因此「建实例」的 effect 不能只依赖 palette —— 否则首帧 loading 时
   * containerRef 还是 null、effect 提前返回，等数据到了却再也不会重跑，
   * 结果是「面板在、图不在」。把 hasSeries 放进依赖即可在容器出现后建图。
   */
  const hasSeries = points.length >= 2;

  /* ── 三种模式的数据 ─────────────────────────────────────────────── */
  const seriesData = useMemo(() => {
    const empty = { strat: [] as LineData[], bench: [] as LineData[] };
    if (points.length < 2 || !initial || initial <= 0) return empty;

    if (mode === 'equity') {
      return {
        strat: points.map((p) => ({ time: toSec(p.t), value: p.equity })),
        bench: benchPoints.map((p) => ({ time: toSec(p.t), value: p.equity })),
      };
    }

    if (mode === 'drawdown') {
      let peak = points[0].equity;
      const dd: LineData[] = points.map((p) => {
        if (p.equity > peak) peak = p.equity;
        const v = peak > 0 ? (p.equity / peak - 1) * 100 : 0;
        return { time: toSec(p.t), value: v };
      });
      return { strat: dd, bench: [] };
    }

    // 收益曲线：两边都按各自首值归一化到 0%，这样比较的是「涨幅」而不是绝对金额
    const strat: LineData[] = points.map((p) => ({
      time: toSec(p.t),
      value: (p.equity / initial - 1) * 100,
    }));
    const benchBase = benchPoints.length ? benchPoints[0].equity : null;
    const bench: LineData[] =
      benchBase && benchBase > 0
        ? benchPoints.map((p) => ({ time: toSec(p.t), value: (p.equity / benchBase - 1) * 100 }))
        : [];
    return { strat, bench };
  }, [points, benchPoints, initial, mode]);

  /* ── 建实例（只建一次） ─────────────────────────────────────────── */
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    const c = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: palette.background },
        textColor: palette.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.gridVert },
        horzLines: { color: palette.gridHorz },
      },
      crosshair: {
        vertLine: { color: palette.crosshair, style: LineStyle.Dashed, labelBackgroundColor: palette.crosshairLabel },
        horzLine: { color: palette.crosshair, style: LineStyle.Dashed, labelBackgroundColor: palette.crosshairLabel },
      },
      rightPriceScale: { borderColor: palette.border, minimumWidth: 56 },
      timeScale: {
        borderColor: palette.border,
        // 17376 个 15m 点：不给亚像素 barSpacing 的话 fitContent 只铺出末尾一段
        minBarSpacing: 0.01,
        timeVisible: false,
        secondsVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    });

    const strat = c.addSeries(LineSeries, {
      color: palette.up,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    });
    const bench = c.addSeries(LineSeries, {
      color: palette.benchmark,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = c;
    stratRef.current = strat;
    benchRef.current = bench;

    return () => {
      c.remove();
      chartRef.current = null;
      stratRef.current = null;
      benchRef.current = null;
    };
    // 主题变化 / 容器首次出现时重建（颜色是构造期参数，重建比 applyOptions 更省事，
    // 且此图没有需要保留的交互状态）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [palette, hasSeries]);

  /* ── 数据下发 ───────────────────────────────────────────────────── */
  useEffect(() => {
    const strat = stratRef.current;
    const bench = benchRef.current;
    const c = chartRef.current;
    if (!strat || !bench || !c) return;

    strat.setData(seriesData.strat);
    bench.setData(seriesData.bench);
    bench.applyOptions({ visible: seriesData.bench.length > 0 });
    // autoSize 靠 ResizeObserver 落地，首帧尺寸可能还是 0；延一帧再 fitContent
    const raf = requestAnimationFrame(() => {
      try {
        c.timeScale().fitContent();
      } catch {
        /* 图表已销毁 */
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [seriesData]);

  const lastStrat = seriesData.strat.length
    ? (seriesData.strat[seriesData.strat.length - 1].value as number)
    : null;
  const lastBench = seriesData.bench.length
    ? (seriesData.bench[seriesData.bench.length - 1].value as number)
    : null;

  const benchNote = chart?.benchmark
    ? `基准 ${chart.benchmark.symbol ?? ''} 为纯价格口径（不含手续费/滑点/资金费）`
    : '该 run 无基准序列';

  return (
    <section className="ds-card" style={{ display: 'flex', flexDirection: 'column' }}>
      <div className="ds-card__head" style={{ padding: 0 }}>
        <div className="ds-seg" style={{ margin: 0, flex: 1 }} role="tablist">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              role="tab"
              className="ds-seg__item"
              aria-selected={mode === m.id}
              onClick={() => setMode(m.id)}
              style={{ flex: 1, justifyContent: 'center' }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: '6px 10px 0', display: 'flex', gap: 12, fontSize: 'var(--ds-fs-2xs)', color: 'var(--ds-text-3)' }}>
        <span>
          <span className="ds-chartlegend__dot" style={{ background: palette.up }} />
          策略
          <b style={{ marginLeft: 4, fontFamily: 'var(--ds-mono)', color: 'var(--ds-text-2)' }}>
            {mode === 'equity' ? (lastStrat == null ? DASH : lastStrat.toFixed(2)) : lastStrat == null ? DASH : fmtPct(lastStrat)}
          </b>
        </span>
        {seriesData.bench.length > 0 ? (
          <span>
            <span className="ds-chartlegend__dot" style={{ background: palette.benchmark }} />
            基准
            <b style={{ marginLeft: 4, fontFamily: 'var(--ds-mono)', color: 'var(--ds-text-2)' }}>
              {mode === 'equity' ? (lastBench == null ? DASH : lastBench.toFixed(2)) : lastBench == null ? DASH : fmtPct(lastBench)}
            </b>
          </span>
        ) : null}
      </div>

      <div style={{ height, position: 'relative' }}>
        {error ? (
          <div className="ds-bt-empty">权益曲线取数失败：{error}</div>
        ) : loading ? (
          <div className="ds-bt-empty">加载权益曲线…</div>
        ) : !hasSeries ? (
          <div className="ds-bt-empty">该 run 没有足够的权益点</div>
        ) : (
          <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
        )}
      </div>

      <div className="ds-card__foot" style={{ fontSize: 9, color: 'var(--ds-text-4)' }}>
        {benchNote}
      </div>
    </section>
  );
};
