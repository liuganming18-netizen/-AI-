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
  AreaSeries,
  ColorType,
  CrosshairMode,
  LastPriceAnimationMode,
  LineSeries,
  LineStyle,
  LineType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { BtChartPayload } from '../../api/backtestReview';
import { useChartPalette, withAlpha } from '../../design/chartTheme';
import { DASH, fmtPct } from '../../utils/labFormat';

type Mode = 'return' | 'drawdown' | 'equity';

const MODES: { id: Mode; label: string }[] = [
  { id: 'return', label: '收益曲线' },
  { id: 'drawdown', label: '回撤曲线' },
  { id: 'equity', label: '已实现资金曲线' },
];

const toSec = (ms: number): UTCTimestamp => Math.floor(ms / 1000) as UTCTimestamp;

/** 右价格轴最小宽度：本图无并排对照，取 56 已够（不挤占绘图区）。 */
const PRICE_SCALE_MIN_WIDTH = 56;

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
  const stratRef = useRef<ISeriesApi<'Area'> | null>(null);
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
        // 轴文字 12px：能读但不抢（与 K 线图同一口径）
        fontSize: 12,
        // ★ 必须传真实字体栈字符串：canvas 不解析 CSS 变量
        fontFamily: palette.font,
        attributionLogo: false,
      },
      grid: {
        // 净值曲线需要横线（读高度对应数值）；竖线是纯噪声，一律关
        horzLines: { color: palette.gridHorz, style: LineStyle.Solid, visible: true },
        vertLines: { color: palette.gridVert, style: LineStyle.Solid, visible: false },
      },
      crosshair: {
        // Magnet 会让十字线纵向跳，读图时「手在抖」⇒ 保持 Normal
        mode: CrosshairMode.Normal,
        vertLine: {
          color: palette.crosshair,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: palette.crosshairLabel,
        },
        horzLine: {
          color: palette.crosshair,
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: palette.crosshairLabel,
        },
      },
      rightPriceScale: {
        // 价格轴竖线是纯装饰，去掉后绘图区更宽
        borderVisible: false,
        borderColor: palette.border,
        minimumWidth: PRICE_SCALE_MIN_WIDTH,
        // 顶/底角标不画被裁一半的文字
        entireTextOnly: true,
        // 刻度更稀一点，轴更干净
        tickMarkDensity: 2.8,
        // 顶部留 14% / 底部 6%：曲线不贴边，末值标签不被裁
        scaleMargins: { top: 0.14, bottom: 0.06 },
      },
      timeScale: {
        borderVisible: true,
        borderColor: palette.border,
        // 17376 个 15m 点：不给亚像素 barSpacing 的话 fitContent 只铺出末尾一段
        minBarSpacing: 0.01,
        timeVisible: true,
        secondsVisible: false,
        // 最新一点右侧留 2 根空档：末值/十字线标签不被右边框切
        rightOffset: 2,
        // 不能拖到第一根左侧的空白；右侧保留呼吸，不锁死
        fixLeftEdge: true,
        fixRightEdge: false,
        // 窗口尺寸变化时视窗不跳（默认 false 会重算可见范围）
        lockVisibleTimeRangeOnResize: true,
      },
      handleScroll: true,
      handleScale: true,
    });

    /* 策略线改 AreaSeries + 渐变：一整条实线太平，渐变让「曲线的下方」有体量感，
     * 又不会像实心色块那样抢注意力。渐变上端 16% → 下端完全透明。
     * 不用 `LineType.Curved`：曲线平滑会在两个真实采样点之间「画出一条不存在的
     * 数据」—— 在量化工具里这是视觉层面的编数据。 */
    const strat = c.addSeries(AreaSeries, {
      lineColor: palette.up,
      lineWidth: 2,
      lineType: LineType.Simple,
      topColor: withAlpha(palette.up, 0.16),
      bottomColor: withAlpha(palette.up, 0),
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 3,
      // 描边用底色 = 白圈，与背景融合
      crosshairMarkerBorderColor: palette.background,
      crosshairMarkerBackgroundColor: palette.up,
      priceLineVisible: false,
      lastValueVisible: true,
      // 关掉末值点脉冲：实时刷新时它会持续闪，属于装饰性动画
      lastPriceAnimation: LastPriceAnimationMode.Disabled,
    });
    const bench = c.addSeries(LineSeries, {
      color: palette.benchmark,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lineType: LineType.Simple,
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
    /* 回撤曲线用「跌色」，收益/资金曲线用「涨色」——同一根线在三套口径下
     * 语义不同，颜色必须跟着口径走（否则「回撤」画成绿色，读图会反向）。 */
    const lineColor = mode === 'drawdown' ? palette.down : palette.up;
    strat.applyOptions({
      lineColor,
      topColor: withAlpha(lineColor, 0.16),
      bottomColor: withAlpha(lineColor, 0),
      crosshairMarkerBackgroundColor: lineColor,
    });
    // autoSize 靠 ResizeObserver 落地，首帧尺寸可能还是 0；延一帧再 fitContent
    const raf = requestAnimationFrame(() => {
      try {
        c.timeScale().fitContent();
      } catch {
        /* 图表已销毁 */
      }
    });
    return () => cancelAnimationFrame(raf);
    /* `palette` 也必须在依赖里：主题切换会**重建实例**（上面那个 effect），
     * 新实例是空的；只依赖 seriesData 的话切主题后这张图会一直空着。 */
  }, [seriesData, palette, mode]);

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
      <div style={{ padding: '2px 10px 4px', fontSize: '10px', color: 'var(--ds-text-4)' }}>
        已实现口径：曲线基于逐笔平仓净额展开，不含未平仓浮盈浮亏
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
