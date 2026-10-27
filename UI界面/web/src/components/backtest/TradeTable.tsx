/**
 * 交易记录表 + 未成交 run 的日志视图
 * ----------------------------------------------------------------------------
 * 有成交：逐笔明细表（方向 / 开平价 / 持仓时长 / 盈亏 / 退出原因），带方向与结果
 * 筛选、搜索、分页。
 *
 * 无成交：不显示一张空表，而是切到「运行日志」—— 信号 / 订单 / 拒单 三段真实记录，
 * 并明确写出「该 run 账本 trades = 0」。这些数据来自 `/chart`、`/orders`、`/rejected`，
 * 不是漏斗（漏斗字段在 summary.json 里，后端未通过 API 暴露）。
 *
 * 盈亏百分比口径：**账户口径**（net_pnl / 初始权益），与顶部「总收益率」同分母，
 * 所以逐笔加总能对上总收益。不用「仓位收益率」是为了避免同一页出现两种百分比。
 */

import React, { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type {
  BtChartPayload,
  BtMetrics,
  BtOrderRow,
  BtRejectedRow,
  BtTradeRow,
} from '../../api/backtestReview';
import {
  DASH,
  fmtDateTime,
  fmtDurationH,
  fmtInt,
  fmtPct,
  fmtPrice,
} from '../../utils/labFormat';
import { directionLabel, exitReasonLabel } from '../../utils/runMeta';

const PAGE_SIZE = 10;

/**
 * 缺失值的三种叫法（FT-REPLAY-01）—— 不许都写成「—」，那等于把
 * 「这一步本来就没有」和「这一步有但没记下来」混成一件事：
 *   · `未知`     = 该字段本应存在，但账本/上游没有记录
 *   · `无此环节` = 该模式没有这一步（如 C0 没有独立确认 K 线）
 *   · `未记录`   = 时间字段：上游没落盘，禁止用相邻时间顶替
 */
const UNKNOWN = '未知';
const NOT_APPLICABLE = '无此环节';
const NOT_RECORDED = '未记录';

/** 毫秒时间戳 → 单元格内容；`null` 给调用方指定的缺失文案，悬浮显示原始毫秒值供核对。 */
function timeCell(
  ms: number | null | undefined,
  missing: string,
  source?: string | null,
): { text: string; title: string } {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) {
    return { text: missing, title: source ? `来源：${source}` : '账本无该时刻' };
  }
  return {
    text: fmtDateTime(ms),
    title: `原始时间戳 ${ms} ms（UTC）${source ? `\n来源：${source}` : ''}`,
  };
}

/** 数字单元格：缺就给缺失文案，不填 0。 */
function numCell(
  v: number | null | undefined,
  missing: string,
  fmt: (x: number) => string,
  source?: string | null,
): { text: string; title: string } {
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    return { text: missing, title: source ? `来源：${source}` : '账本无该值' };
  }
  return { text: fmt(v), title: source ? `来源：${source}` : '' };
}

export interface TradeTableProps {
  trades: BtTradeRow[];
  metrics: BtMetrics | null;
  chart: BtChartPayload | null;
  orders: BtOrderRow[];
  rejected: BtRejectedRow[];
  loading: boolean;
  error: string | null;
  /** 表格高度（px） */
  height?: number;
}

type DirFilter = 'all' | 'long' | 'short';
type ResultFilter = 'all' | 'win' | 'loss';

export const TradeTable: React.FC<TradeTableProps> = ({
  trades,
  metrics,
  chart,
  orders,
  rejected,
  loading,
  error,
  height = 360,
}) => {
  const [dir, setDir] = useState<DirFilter>('all');
  const [result, setResult] = useState<ResultFilter>('all');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  const initialEquity = metrics?.initial_equity ?? null;
  const pnlPct = (net: number | null | undefined): number | null => {
    if (typeof net !== 'number' || !Number.isFinite(net)) return null;
    if (!initialEquity || initialEquity <= 0) return null;
    return (net / initialEquity) * 100;
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return trades.filter((t) => {
      const d = directionLabel(t.direction);
      if (dir === 'long' && d !== '多') return false;
      if (dir === 'short' && d !== '空') return false;
      const net = t.net_pnl ?? 0;
      if (result === 'win' && !(net > 0)) return false;
      if (result === 'loss' && !(net < 0)) return false;
      if (q) {
        const hay = `${t.symbol ?? ''} ${t.exit_reason ?? ''} ${t.trade_id ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [trades, dir, result, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const rows = filtered.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);

  const noTrades = (metrics?.trades_count ?? 0) === 0 && trades.length === 0;

  if (noTrades) {
    return (
      <RunLogPanel
        chart={chart}
        orders={orders}
        rejected={rejected}
        loading={loading}
        error={error}
        height={height}
      />
    );
  }

  return (
    <section className="ds-card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="ds-card__head" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="ds-card__title">交易记录</span>
        <span className="ds-card__meta">共 {fmtInt(trades.length)} 笔</span>
        <span style={{ flex: 1 }} />
        <select
          className="ds-select"
          value={dir}
          onChange={(e) => {
            setDir(e.target.value as DirFilter);
            setPage(1);
          }}
          aria-label="按方向筛选"
          style={{ width: 92 }}
        >
          <option value="all">全部方向</option>
          <option value="long">多</option>
          <option value="short">空</option>
        </select>
        <select
          className="ds-select"
          value={result}
          onChange={(e) => {
            setResult(e.target.value as ResultFilter);
            setPage(1);
          }}
          aria-label="按结果筛选"
          style={{ width: 92 }}
        >
          <option value="all">全部结果</option>
          <option value="win">盈利</option>
          <option value="loss">亏损</option>
        </select>
        <label className="ds-bt-search" style={{ width: 160 }}>
          <Search />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
            placeholder="搜索交易对 / 原因"
            aria-label="搜索交易"
          />
        </label>
      </div>

      <div className="ds-tablewrap" style={{ maxHeight: height, overflow: 'auto' }}>
        <table className="ds-table ds-table--tight">
          <thead>
            <tr>
              <th style={{ width: 36 }}>#</th>
              <th style={{ width: 108 }} title="候选 K 线收盘时间（不是成交时间）">信号时间 UTC</th>
              <th style={{ width: 108 }} title="确认 K 线收盘时间；C0 模式无此环节">确认时间 UTC</th>
              <th style={{ width: 108 }} title="下单即成交的真实时刻（入场成交时间）">下单/成交 UTC</th>
              <th style={{ width: 92 }}>交易对</th>
              <th style={{ width: 46 }}>方向</th>
              <th className="ds-table__num" style={{ width: 82 }}>开仓价</th>
              <th className="ds-table__num" style={{ width: 82 }}>平仓价</th>
              <th className="ds-table__num" style={{ width: 88 }}>数量</th>
              <th className="ds-table__num" style={{ width: 84 }}>仓位金额</th>
              <th className="ds-table__num" style={{ width: 78 }} title="箱体/扫单基准价 P0">原始 P0</th>
              <th className="ds-table__num" style={{ width: 78 }} title="入场时计划风险（USDT）">计划风险</th>
              <th style={{ width: 60 }}>持仓</th>
              <th className="ds-table__num" style={{ width: 76 }}>盈亏 (账户%)</th>
              <th style={{ width: 84 }}>退出原因</th>
            </tr>
          </thead>
          <tbody>
            {error ? (
              <tr>
                <td colSpan={15} className="ds-table__empty">
                  逐笔明细取数失败：{error}
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={15} className="ds-table__empty">
                  当前筛选条件下没有交易
                </td>
              </tr>
            ) : (
              rows.map((t, i) => {
                const d = directionLabel(t.direction);
                const pct = pnlPct(t.net_pnl);
                const tone = pct == null ? '' : pct > 0 ? 'ds-table__pos' : pct < 0 ? 'ds-table__neg' : '';
                /* C0 没有独立确认 K 线 ⇒ 确认时间显示「无此环节」，不显示「未知」；
                 * 其余模式确实没记录才叫「未知」。 */
                const isC0 = (t.mode ?? '').toUpperCase() === 'C';
                const sig = timeCell(t.signal_time_ms, UNKNOWN, t.signal_time_source);
                const cfm = timeCell(
                  t.confirm_time_ms,
                  isC0 ? NOT_APPLICABLE : UNKNOWN,
                  t.confirm_time_source,
                );
                const ord = timeCell(t.order_time_ms, NOT_RECORDED, t.order_time_source);
                const qty = numCell(t.qty, UNKNOWN, (x) => fmtPrice(x), 'trades.jsonl.qty');
                const stake = numCell(t.stake_amount, UNKNOWN, (x) => fmtPrice(x), t.stake_amount_source);
                const p0 = numCell(t.P0, UNKNOWN, (x) => fmtPrice(x), t.P0_source);
                const risk = numCell(
                  t.planned_risk_usdt,
                  UNKNOWN,
                  (x) => fmtPrice(x),
                  t.planned_risk_source,
                );
                return (
                  <tr key={t.trade_id ?? `${t.entry_time_ms}-${i}`}>
                    <td className="ds-table__muted">{(curPage - 1) * PAGE_SIZE + i + 1}</td>
                    <td className="ds-table__muted" title={sig.title}>{sig.text}</td>
                    <td className="ds-table__muted" title={cfm.title}>{cfm.text}</td>
                    <td className="ds-table__muted" title={ord.title}>{ord.text}</td>
                    <td className="ds-table__strong">{t.symbol ?? DASH}</td>
                    <td>
                      <span className={`ds-badge ${d === '多' ? 'ds-badge--pos' : d === '空' ? 'ds-badge--neg' : 'ds-badge--neutral'}`}>
                        {d}
                      </span>
                    </td>
                    <td className="ds-table__num">{fmtPrice(t.entry_price)}</td>
                    <td className="ds-table__num">{fmtPrice(t.exit_price)}</td>
                    <td className="ds-table__num" title={qty.title}>{qty.text}</td>
                    <td className="ds-table__num" title={stake.title}>{stake.text}</td>
                    <td className="ds-table__num" title={p0.title}>{p0.text}</td>
                    <td className="ds-table__num" title={risk.title}>{risk.text}</td>
                    <td className="ds-table__muted">{fmtDurationH(t.holding_hours)}</td>
                    <td className={`ds-table__num ${tone}`}>{pct == null ? DASH : fmtPct(pct, 3)}</td>
                    <td className="ds-table__muted">{exitReasonLabel(t.exit_reason)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="ds-pager">
        <span>
          共 {fmtInt(filtered.length)} 条
          {filtered.length !== trades.length ? `（全量 ${fmtInt(trades.length)}）` : ''}
        </span>
        <div className="ds-pager__btns">
          <button
            type="button"
            className="ds-pager__btn"
            disabled={curPage <= 1}
            onClick={() => setPage(curPage - 1)}
            aria-label="上一页"
          >
            ‹
          </button>
          {Array.from({ length: Math.min(5, pageCount) }).map((_, i) => {
            // 只显示当前页附近 5 个页码
            const start = Math.max(1, Math.min(curPage - 2, pageCount - 4));
            const p = start + i;
            if (p > pageCount) return null;
            return (
              <button
                key={p}
                type="button"
                className="ds-pager__btn"
                aria-current={p === curPage}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            );
          })}
          <button
            type="button"
            className="ds-pager__btn"
            disabled={curPage >= pageCount}
            onClick={() => setPage(curPage + 1)}
            aria-label="下一页"
          >
            ›
          </button>
        </div>
      </div>
    </section>
  );
};

/* ── 未成交 run 的运行日志 ───────────────────────────────────────────── */

const RunLogPanel: React.FC<{
  chart: BtChartPayload | null;
  orders: BtOrderRow[];
  rejected: BtRejectedRow[];
  loading: boolean;
  error: string | null;
  height: number;
}> = ({ chart, orders, rejected, loading, error, height }) => {
  const signals = chart?.signals ?? [];

  return (
    <section className="ds-card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="ds-card__head" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="ds-card__title">运行日志</span>
        <span className="ds-badge ds-badge--warn">该 run 账本 trades = 0</span>
        <span style={{ flex: 1 }} />
        <span className="ds-card__meta">
          信号 {signals.length} · 订单 {orders.length} · 拒单 {rejected.length}
        </span>
      </div>

      <div style={{ maxHeight: height, overflow: 'auto' }}>
        {error ? (
          <div className="ds-bt-empty" style={{ minHeight: 120 }}>
            日志取数失败：{error}
          </div>
        ) : loading ? (
          <div className="ds-bt-empty" style={{ minHeight: 120 }}>
            加载日志…
          </div>
        ) : signals.length === 0 && orders.length === 0 && rejected.length === 0 ? (
          <div className="ds-bt-empty" style={{ minHeight: 160, flexDirection: 'column', gap: 6 }}>
            <div>该 run 未产生信号、订单或拒单记录</div>
            <div style={{ fontSize: 'var(--ds-fs-2xs)', color: 'var(--ds-text-4)' }}>
              说明：summary.json 里的漏斗计数（boxes/sweeps/retests/…）后端尚未通过 API 暴露，
              此处不展示无法核实的数字。
            </div>
          </div>
        ) : (
          <>
            {signals.map((s, i) => (
              <div className="ds-logrow" key={`sig-${s.signal_id ?? i}`}>
                <span className="ds-logrow__t">{fmtDateTime(s.t)}</span>
                <span className="ds-logrow__k">信号</span>
                <span className="ds-logrow__v">
                  {s.kind ?? DASH} · {directionLabel(s.direction ?? null)}
                  {s.stop_price != null ? ` · 止损 ${fmtPrice(s.stop_price)}` : ''}
                  {s.oi_score != null ? ` · OI 评分 ${s.oi_score.toFixed(3)}` : ''}
                  {s.signal_id ? (
                    <span style={{ color: 'var(--ds-text-4)' }}> · {s.signal_id}</span>
                  ) : null}
                </span>
              </div>
            ))}
            {orders.map((o, i) => (
              <div className="ds-logrow" key={`ord-${o.order_id ?? i}`}>
                <span className="ds-logrow__t">{fmtDateTime(o.ts_ms ?? o.created_ms ?? null)}</span>
                <span className="ds-logrow__k">订单</span>
                <span className="ds-logrow__v">
                  {o.side ?? DASH} {o.symbol ?? ''} · 状态 {o.status ?? DASH}
                  {o.qty != null ? ` · 数量 ${o.qty}` : ''}
                </span>
              </div>
            ))}
            {rejected.map((r, i) => (
              <div className="ds-logrow" key={`rej-${i}`}>
                <span className="ds-logrow__t">{fmtDateTime(r.ts_ms ?? null)}</span>
                <span className="ds-logrow__k">拒单</span>
                <span className="ds-logrow__v">
                  {r.phase ?? DASH} · {r.reason ?? DASH}
                </span>
              </div>
            ))}
          </>
        )}
      </div>
    </section>
  );
};
