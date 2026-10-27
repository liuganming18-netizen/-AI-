/**
 * 分析面板（右栏）：交易统计 / 信号分析 / 因子分析
 * ----------------------------------------------------------------------------
 * 圆环用 SVG 双 circle + stroke-dasharray 画（不用 conic-gradient）：
 * 这样描边端点可以做圆角、也能被屏幕阅读器读到数值。
 *
 * 5 行键值里「平均盈利 / 平均亏损」后端给的是**美元**，这里换算成占初始权益的
 * 百分比显示（设计稿是百分比口径），并在副标写明换算依据。
 * 「最大单笔盈亏」后端无原生字段，由逐笔 net_pnl 取极值派生。
 */

import React, { useMemo, useState } from 'react';
import type { BtChartPayload, BtMetrics, BtTradeRow } from '../../api/backtestReview';
import { DASH, fmtPct, fmtPctPlain } from '../../utils/labFormat';
import { directionLabel } from '../../utils/runMeta';

type Tab = 'stats' | 'signals' | 'factors';

const TABS: { id: Tab; label: string }[] = [
  { id: 'stats', label: '交易统计' },
  { id: 'signals', label: '信号分析' },
  { id: 'factors', label: '因子分析' },
];

const DONUT_R = 46;
const DONUT_C = 2 * Math.PI * DONUT_R;

const WinRateDonut: React.FC<{ winRatePct: number | null; wins: number | null; losses: number | null; flats: number | null }> = ({
  winRatePct,
  wins,
  losses,
  flats,
}) => {
  const total = (wins ?? 0) + (losses ?? 0) + (flats ?? 0);
  const hasData = total > 0 && winRatePct != null;
  const rate = hasData ? Math.min(100, Math.max(0, winRatePct as number)) : 0;
  const winLen = (rate / 100) * DONUT_C;
  const flatRate = total > 0 ? ((flats ?? 0) / total) * 100 : 0;
  const flatLen = (flatRate / 100) * DONUT_C;

  return (
    <div className="ds-donut">
      <div className="ds-donut__chart">
        <svg viewBox="0 0 120 120" width="116" height="116" role="img" aria-label={`胜率 ${rate.toFixed(1)}%`}>
          <circle cx="60" cy="60" r={DONUT_R} fill="none" stroke="var(--ds-surface-3)" strokeWidth="11" />
          {hasData ? (
            <>
              {/* 盈利段 */}
              <circle
                cx="60"
                cy="60"
                r={DONUT_R}
                fill="none"
                stroke="var(--ds-pos)"
                strokeWidth="11"
                strokeLinecap="butt"
                strokeDasharray={`${winLen} ${DONUT_C - winLen}`}
                transform="rotate(-90 60 60)"
              />
              {/* 平盘段（若有） */}
              {flatLen > 0 ? (
                <circle
                  cx="60"
                  cy="60"
                  r={DONUT_R}
                  fill="none"
                  stroke="var(--ds-text-4)"
                  strokeWidth="11"
                  strokeDasharray={`${flatLen} ${DONUT_C - flatLen}`}
                  strokeDashoffset={-winLen}
                  transform="rotate(-90 60 60)"
                />
              ) : null}
              {/* 亏损段 */}
              <circle
                cx="60"
                cy="60"
                r={DONUT_R}
                fill="none"
                stroke="var(--ds-neg)"
                strokeWidth="11"
                strokeDasharray={`${DONUT_C - winLen - flatLen} ${winLen + flatLen}`}
                strokeDashoffset={-(winLen + flatLen)}
                transform="rotate(-90 60 60)"
              />
            </>
          ) : null}
        </svg>
        <div className="ds-donut__center">
          <span className="ds-donut__value">{hasData ? `${rate.toFixed(1)}%` : DASH}</span>
          <span className="ds-donut__label">{hasData ? '胜率' : '无成交'}</span>
        </div>
      </div>

      <div className="ds-donut__legend">
        <span className="ds-donut__row">
          <span className="ds-chartlegend__dot" style={{ background: 'var(--ds-pos)' }} />
          盈利交易 <b>{wins ?? DASH}</b>
          {hasData ? <span>（{rate.toFixed(1)}%）</span> : null}
        </span>
        <span className="ds-donut__row">
          <span className="ds-chartlegend__dot" style={{ background: 'var(--ds-neg)' }} />
          亏损交易 <b>{losses ?? DASH}</b>
          {hasData ? <span>（{(100 - rate - flatRate).toFixed(1)}%）</span> : null}
        </span>
        {flats ? (
          <span className="ds-donut__row">
            <span className="ds-chartlegend__dot" style={{ background: 'var(--ds-text-4)' }} />
            平盘 <b>{flats}</b>
            {hasData ? <span>（{flatRate.toFixed(1)}%）</span> : null}
          </span>
        ) : null}
        <span className="ds-donut__row" style={{ color: 'var(--ds-text-4)' }}>
          口径：扣费后 net_pnl &gt; 0 为盈
        </span>
      </div>
    </div>
  );
};

export interface AnalysisPanelProps {
  metrics: BtMetrics | null;
  trades: BtTradeRow[];
  chart: BtChartPayload | null;
  loading: boolean;
}

export const AnalysisPanel: React.FC<AnalysisPanelProps> = ({ metrics, trades, chart, loading }) => {
  const [tab, setTab] = useState<Tab>('stats');

  const initialEquity = metrics?.initial_equity ?? null;

  /** 美元 → 占初始权益的百分比（后端 avg_win/avg_loss 是美元） */
  const toPct = (usd: number | null | undefined): number | null => {
    if (typeof usd !== 'number' || !Number.isFinite(usd)) return null;
    if (!initialEquity || initialEquity <= 0) return null;
    return (usd / initialEquity) * 100;
  };

  const extremes = useMemo(() => {
    const vals = trades
      .map((t) => t.net_pnl)
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    if (vals.length === 0) return { best: null as number | null, worst: null as number | null };
    return { best: Math.max(...vals), worst: Math.min(...vals) };
  }, [trades]);

  const signals = chart?.signals ?? [];
  const signalKinds = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of signals) {
      const k = s.kind || '未知';
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [signals]);

  const latestSignal = signals.length ? signals[signals.length - 1] : null;

  const noTrades = (metrics?.trades_count ?? 0) === 0;

  return (
    <section className="ds-card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="ds-card__head" style={{ padding: 0 }}>
        <div className="ds-seg" style={{ margin: 0, width: '100%' }} role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              className="ds-seg__item"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              style={{ flex: 1, justifyContent: 'center' }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="ds-analysis">
        {tab === 'stats' ? (
          <>
            <WinRateDonut
              winRatePct={metrics?.win_rate_pct ?? null}
              wins={metrics?.wins ?? null}
              losses={metrics?.losses ?? null}
              flats={metrics?.flats ?? null}
            />

            <div>
              <div className="ds-kvline">
                <span className="ds-kvline__k">平均盈利</span>
                <span
                  className={`ds-kvline__v${toPct(metrics?.avg_win) != null ? ' ds-kvline__v--pos' : ' ds-kvline__v--muted'}`}
                >
                  {toPct(metrics?.avg_win) == null ? DASH : fmtPct(toPct(metrics?.avg_win))}
                </span>
              </div>
              <div className="ds-kvline">
                <span className="ds-kvline__k">平均亏损</span>
                <span
                  className={`ds-kvline__v${toPct(metrics?.avg_loss) != null ? ' ds-kvline__v--neg' : ' ds-kvline__v--muted'}`}
                >
                  {toPct(metrics?.avg_loss) == null ? DASH : fmtPct(toPct(metrics?.avg_loss))}
                </span>
              </div>
              <div className="ds-kvline">
                <span className="ds-kvline__k">盈亏比</span>
                <span className="ds-kvline__v">
                  {metrics?.avg_win_loss_ratio != null
                    ? metrics.avg_win_loss_ratio.toFixed(2)
                    : metrics?.profit_factor != null
                      ? `${metrics.profit_factor.toFixed(2)}（聚合）`
                      : DASH}
                </span>
              </div>
              <div className="ds-kvline">
                <span className="ds-kvline__k">
                  最大单笔盈利
                  <span style={{ color: 'var(--ds-text-4)', marginLeft: 4 }}>派生</span>
                </span>
                <span
                  className={`ds-kvline__v${toPct(extremes.best) != null ? ' ds-kvline__v--pos' : ' ds-kvline__v--muted'}`}
                >
                  {toPct(extremes.best) == null ? DASH : fmtPct(toPct(extremes.best))}
                </span>
              </div>
              <div className="ds-kvline">
                <span className="ds-kvline__k">
                  最大单笔亏损
                  <span style={{ color: 'var(--ds-text-4)', marginLeft: 4 }}>派生</span>
                </span>
                <span
                  className={`ds-kvline__v${toPct(extremes.worst) != null ? ' ds-kvline__v--neg' : ' ds-kvline__v--muted'}`}
                >
                  {toPct(extremes.worst) == null ? DASH : fmtPct(toPct(extremes.worst))}
                </span>
              </div>
            </div>

            {noTrades ? (
              <div className="ds-note ds-note--info" style={{ fontSize: 'var(--ds-fs-2xs)' }}>
                该 run 账本 0 笔成交，胜率与盈亏比无定义。左侧「交易记录」里可看它的运行日志。
              </div>
            ) : (
              <div className="ds-note" style={{ fontSize: 'var(--ds-fs-2xs)' }}>
                平均盈亏与最大单笔已按初始权益 {initialEquity?.toLocaleString('en-US') ?? DASH} USDT
                折算为百分比；后端原始字段为美元金额。
              </div>
            )}
          </>
        ) : null}

        {tab === 'signals' ? (
          <>
            <div className="ds-kvline">
              <span className="ds-kvline__k">信号总数</span>
              <span className="ds-kvline__v">{loading ? DASH : signals.length}</span>
            </div>
            {signalKinds.length === 0 ? (
              <div className="ds-bt-empty" style={{ minHeight: 160 }}>
                该 run 没有触发任何信号
              </div>
            ) : (
              signalKinds.map(([kind, n]) => (
                <div className="ds-kvline" key={kind}>
                  <span className="ds-kvline__k">{kind}</span>
                  <span className="ds-kvline__v">{n}</span>
                </div>
              ))
            )}
            {latestSignal ? (
              <div className="ds-note" style={{ fontSize: 'var(--ds-fs-2xs)' }}>
                最近一条：{latestSignal.kind ?? DASH} · {directionLabel(latestSignal.direction ?? null)} ·
                OI 可用 {latestSignal.oi_available === true ? '是' : latestSignal.oi_available === false ? '否' : DASH}
                {latestSignal.oi_quality ? `（${latestSignal.oi_quality}）` : ''}
              </div>
            ) : null}
          </>
        ) : null}

        {tab === 'factors' ? (
          <>
            <div className="ds-kvline">
              <span className="ds-kvline__k">OI 评分</span>
              <span className="ds-kvline__v">
                {latestSignal?.oi_score == null ? DASH : latestSignal.oi_score.toFixed(4)}
              </span>
            </div>
            <div className="ds-kvline">
              <span className="ds-kvline__k">ATR</span>
              <span className="ds-kvline__v">
                {latestSignal?.atr == null ? DASH : latestSignal.atr.toFixed(6)}
              </span>
            </div>
            <div className="ds-kvline">
              <span className="ds-kvline__k">信号止损价</span>
              <span className="ds-kvline__v">
                {latestSignal?.stop_price == null ? DASH : latestSignal.stop_price.toFixed(6)}
              </span>
            </div>
            <div className="ds-kvline">
              <span className="ds-kvline__k">OI 数据质量</span>
              <span className="ds-kvline__v">{latestSignal?.oi_quality ?? DASH}</span>
            </div>
            <div className="ds-kvline">
              <span className="ds-kvline__k">毛利 / 手续费 / 资金费</span>
              <span className="ds-kvline__v">
                {metrics?.gross_pnl == null ? DASH : metrics.gross_pnl.toFixed(2)} /{' '}
                {metrics?.total_fees == null
                  ? DASH
                  : metrics.total_fees === 0 && (metrics.trades_count ?? 0) > 0
                    ? '0.00 (未计费用)'
                    : metrics.total_fees.toFixed(2)} /{' '}
                {metrics?.total_funding_fee == null ? DASH : metrics.total_funding_fee.toFixed(2)}
              </span>
            </div>
            <div className="ds-note" style={{ fontSize: 'var(--ds-fs-2xs)' }}>
              因子侧只展示信号载荷里真实存在的字段；没有的字段显示 {DASH}，不做归因推断。
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
};
