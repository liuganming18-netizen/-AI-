/**
 * 批次逐笔复核表（BT-UI-01）
 * ----------------------------------------------------------------------------
 * 一张表把「一笔交易」复核所需的字段全部摆出来，且每一列都能指回账本：
 *
 *   币种 · 运行ID · 交易ID(trd_) · 候选ID(cand_ev_) · 信号时间 · 成交时间 ·
 *   买入价 · 初始止损 · 退出时间 · 退出价 · 退出原因 · 净盈亏
 *
 * 三个口径约定（写死在表头里，避免误读）：
 *   1. 所有时间都是 **UTC**；表头额外注明「北京时间 = UTC+8」——
 *      图表轴同样按 UTC 显示，两边对照不会错位。
 *   2. 净盈亏是**扣费后**（含手续费 + 资金费）的美元值；与 `/metrics` 的
 *      `net_pnl` 同源（都来自账本逐笔），不是估算。
 *   3. 缺失字段一律 `—`：没有初始止损就显示 `—`，不按入场价推算；
 *      候选 ID 缺失也不补。
 *
 * 点击任意行 ⇒ 由父组件切到该笔所属 run 并定位图表（入场前后）。
 */

import React, { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type { BtBatchTrade } from '../../api/backtestReview';
import { DASH, fmtDurationH, fmtInt, fmtPrice, fmtUsd } from '../../utils/labFormat';
import { exitReasonLabel } from '../../utils/runMeta';

function fmtUtc(ms: number | null | undefined): string {
  if (!Number.isFinite(ms as number) || (ms as number) <= 0) return DASH;
  const d = new Date(ms as number);
  if (Number.isNaN(d.getTime())) return DASH;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

function dirLabel(v: number | string | null | undefined): string {
  const s = String(v ?? '');
  if (s === '1' || s.toLowerCase() === 'long' || s.toLowerCase() === 'buy') return '多';
  if (s === '-1' || s.toLowerCase() === 'short' || s.toLowerCase() === 'sell') return '空';
  return DASH;
}

/** 统一走 `utils/runMeta` 的那张表（本地曾漏 `max_holding_expired` / `force_close_eod`
 *  ⇒ 同一列里一半中文一半英文枚举，看起来像字段没翻译）。 */
const exitLabel = (r: string | null | undefined): string => exitReasonLabel(r);

export interface BatchTradesTableProps {
  trades: BtBatchTrade[];
  loading: boolean;
  error: string | null;
  /** 缺失统计（后端如实给出：没有初始止损 / 没定位到信号时刻的笔数） */
  missingInitialStop?: number | null;
  unlinkedSignalTime?: number | null;
  /** 当前选中的一笔（跨 run 用 run_id + trade_id 联合判定） */
  selectedRunId?: string | null;
  selectedTradeId?: string | null;
  onLocate: (runId: string, tradeId: string | null) => void;
  /** 表格高度 */
  height?: number;
}

type ResultFilter = 'all' | 'win' | 'loss';

export const BatchTradesTable: React.FC<BatchTradesTableProps> = ({
  trades,
  loading,
  error,
  missingInitialStop = null,
  unlinkedSignalTime = null,
  selectedRunId = null,
  selectedTradeId = null,
  onLocate,
  height = 340,
}) => {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<ResultFilter>('all');

  /**
   * 脏行防御：接口契约是 `BtBatchTrade[]`，但实测后端在某些 run 上会给出 `null`
   * 元素（`{items:[null]}`）。原实现直接 `trades.filter(t => t.net_pnl ...)` ⇒
   * `Cannot read properties of null` ⇒ 整个页面被错误边界替换（列表和 K 线一起消失）。
   * 这里**先剔除空元素再统计**，且「共 N 笔」与空态判断都用剔除后的长度，
   * 避免出现「显示 12 笔但一行也渲染不出来」。
   */
  const safeTrades = useMemo(
    () => (trades ?? []).filter((t): t is BtBatchTrade => t != null),
    [trades],
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return safeTrades.filter((t) => {
      const net = t.net_pnl ?? 0;
      if (result === 'win' && !(net > 0)) return false;
      if (result === 'loss' && !(net < 0)) return false;
      if (q) {
        const hay = `${t.symbol ?? ''} ${t.trade_id ?? ''} ${t.signal_id ?? ''} ${t.run_id ?? ''} ${t.exit_reason ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [safeTrades, query, result]);

  return (
    <section className="ds-card" data-testid="bt-batch-trades">
      <div className="ds-card__head" style={{ flexWrap: 'wrap', gap: 8 }}>
        <span className="ds-card__title">批次逐笔复核</span>
        <span className="ds-card__meta">共 {fmtInt(safeTrades.length)} 笔（本批次全部 run）</span>
        <span className="ds-bar__sep" />
        <span className="ds-card__meta" title="时间列全部为 UTC；北京时间 = UTC + 8 小时">
          时间口径：UTC（北京时间 = UTC+8）
        </span>
        <span style={{ flex: 1 }} />
        <select
          className="ds-select"
          value={result}
          onChange={(e) => setResult(e.target.value as ResultFilter)}
          aria-label="按结果筛选"
          style={{ width: 96 }}
        >
          <option value="all">全部结果</option>
          <option value="win">盈利</option>
          <option value="loss">亏损</option>
        </select>
        <label className="ds-bt-search" style={{ width: 200 }}>
          <Search />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜币种 / 交易ID / 候选ID"
            aria-label="搜索批次逐笔"
          />
        </label>
      </div>

      {(missingInitialStop || unlinkedSignalTime) ? (
        <div className="ds-note ds-note--warn" style={{ margin: '0 12px 8px' }} data-testid="bt-batch-trades-gaps">
          {missingInitialStop ? `${missingInitialStop} 笔缺初始止损（账本无 stop_trace，未推算）` : ''}
          {missingInitialStop && unlinkedSignalTime ? ' · ' : ''}
          {unlinkedSignalTime ? `${unlinkedSignalTime} 笔未定位到候选信号时刻（留空，未按 ID 猜时间）` : ''}
        </div>
      ) : null}

      <div className="ds-tablewrap" style={{ maxHeight: height, overflow: 'auto' }}>
        <table className="ds-table ds-table--tight ds-table--clickable" data-testid="bt-batch-trades-table">
          <thead>
            <tr>
              <th style={{ width: 34 }}>#</th>
              <th style={{ width: 108 }}>币种</th>
              <th style={{ width: 44 }}>方向</th>
              <th style={{ width: 150 }}>运行 ID</th>
              <th style={{ width: 168 }}>交易 ID</th>
              <th style={{ width: 230 }}>候选 ID（信号）</th>
              <th style={{ width: 116 }}>信号时间 (UTC)</th>
              <th style={{ width: 116 }}>成交时间 (UTC)</th>
              <th className="ds-table__num" style={{ width: 96 }}>买入价</th>
              <th className="ds-table__num" style={{ width: 96 }}>初始止损</th>
              <th style={{ width: 116 }}>退出时间 (UTC)</th>
              <th className="ds-table__num" style={{ width: 96 }}>退出价</th>
              <th style={{ width: 104 }}>退出原因</th>
              <th className="ds-table__num" style={{ width: 96 }}>净盈亏</th>
              <th style={{ width: 76 }}>持仓</th>
            </tr>
          </thead>
          <tbody>
            {error ? (
              <tr>
                <td colSpan={15} className="ds-table__empty">
                  批次逐笔取数失败：{error}
                </td>
              </tr>
            ) : loading ? (
              <tr>
                <td colSpan={15} className="ds-table__empty">
                  读取批次逐笔…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={15} className="ds-table__empty" data-testid="bt-batch-trades-empty">
                  {safeTrades.length === 0
                    ? '本批次没有任何成交（各 run 账本 trades.jsonl 全为空）—— 这是真实结果，不是读取失败'
                    : '当前筛选条件下没有成交'}
                </td>
              </tr>
            ) : (
              rows.map((t, i) => {
                const sel = t.run_id === selectedRunId && (t.trade_id ?? null) === (selectedTradeId ?? null);
                const net = t.net_pnl;
                const tone = net == null ? '' : net > 0 ? 'ds-table__pos' : net < 0 ? 'ds-table__neg' : '';
                return (
                  <tr
                    key={`${t.run_id}|${t.trade_id ?? ''}|${i}`}
                    data-trade-id={t.trade_id ?? undefined}
                    data-run-id={t.run_id}
                    aria-selected={sel}
                    onClick={() => onLocate(t.run_id, t.trade_id ?? null)}
                    title="点击 ⇒ 切到该笔所属 run 并在图上定位入场前后"
                  >
                    <td className="ds-table__muted">{i + 1}</td>
                    <td className="ds-table__strong">{t.symbol ?? DASH}</td>
                    <td>
                      <span
                        className={`ds-badge ${
                          dirLabel(t.direction) === '多'
                            ? 'ds-badge--pos'
                            : dirLabel(t.direction) === '空'
                              ? 'ds-badge--neg'
                              : 'ds-badge--neutral'
                        }`}
                      >
                        {dirLabel(t.direction)}
                      </span>
                    </td>
                    <td className="ds-table__muted" title={t.run_id}>
                      {t.run_id.replace('run_WX_EVENT_TREND_V0_1_', '…_')}
                    </td>
                    <td className="ds-table__muted" title={t.trade_id_source ?? undefined}>
                      {t.trade_id ?? DASH}
                    </td>
                    <td className="ds-table__muted" title={t.signal_time_source ?? undefined}>
                      {t.signal_id ? t.signal_id.replace('cand_ev_WX_EVENT_TREND_V0_1_binance_', '…_') : DASH}
                    </td>
                    <td className="ds-table__muted">{fmtUtc(t.signal_time_ms)}</td>
                    <td className="ds-table__muted">{fmtUtc(t.entry_time_ms)}</td>
                    <td className="ds-table__num">{fmtPrice(t.entry_price)}</td>
                    <td className="ds-table__num" title={t.initial_stop_reason ?? undefined}>
                      {fmtPrice(t.initial_stop)}
                    </td>
                    <td className="ds-table__muted">{fmtUtc(t.exit_time_ms)}</td>
                    <td className="ds-table__num">{fmtPrice(t.exit_price)}</td>
                    <td className="ds-table__muted" title={t.exit_reason ?? undefined}>
                      {exitLabel(t.exit_reason)}
                    </td>
                    <td className={`ds-table__num ${tone}`} title={net == null ? undefined : `$${fmtUsd(net, 4)}`}>
                      {net == null ? DASH : `${net > 0 ? '+' : net < 0 ? '-' : ''}$${fmtUsd(Math.abs(net), 2)}`}
                    </td>
                    <td className="ds-table__muted">{fmtDurationH(t.holding_hours)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="ds-card__foot" style={{ padding: '6px 12px', fontSize: 'var(--ds-fs-2xs)', color: 'var(--ds-text-4)' }}>
        净盈亏 = 扣手续费与资金费后的美元值（账本逐笔口径，若账本未计费用则未含交易成本，属研究样本状态，非实盘已验证收益）；交易 ID 为账本 <code>trd_…</code>，
        候选 ID 为 <code>cand_ev_…</code>（与 K 线信号点同源）；初始止损取账本 <code>stop_trace</code> 首点。
      </div>
    </section>
  );
};

export default BatchTradesTable;
