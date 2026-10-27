/**
 * 指标条（8 格）
 * ----------------------------------------------------------------------------
 * 口径与设计稿 `gm-layout.html` 的八张指标卡一一对应：
 *   净收益 · 最大回撤 · 夏普比率 · 收益因子 · 成交笔数 · 胜率 · 盈亏比 · 总费用
 *
 * 三条硬规矩（缺就显示 —，绝不填 0）：
 *   · 夏普比率 —— 后端 `/metrics` 不返回该字段 ⇒ 恒 `—`，不自己按日收益估一个。
 *   · 收益因子 —— `profit_factor` 为 0 表示「一笔盈利都没有」，此时不是可比较的比值 ⇒ `—`。
 *   · 总费用   —— 必须手续费与资金费**两者都有**才给合计；缺一项就 `—`，
 *                 不允许「把没记录当成 0」再相加（那会给出一个偏小的假成本）。
 *
 * 年化收益率不在设计稿的八格里，已移除（窗口 <30 天本来也不折算，留着容易误读）。
 */

import React from 'react';
import { DASH, fmtInt, fmtPct, fmtPctPlain, fmtUsd } from '../../utils/labFormat';

/**
 * 指标条只吃这几个字段，因此**不绑定某一套接口**。
 * 项目里有两套指标返回：
 *   · `LabMetrics`（`api/labBacktest`，回测页主体在用）—— 有 `sharpe_annualized_sqrt365`
 *   · `BtMetrics`（`api/backtestReview`，批次接口）—— 没有夏普，有 `equity_points`
 * 写成结构化的宽类型，两边都能直接传，不必造适配对象。
 */
export interface MetricLike {
  net_return_pct?: number | null;
  max_drawdown_pct?: number | null;
  profit_factor?: number | null;
  avg_win_loss_ratio?: number | null;
  trades_count?: number | null;
  wins?: number | null;
  losses?: number | null;
  flats?: number | null;
  win_rate_pct?: number | null;
  total_fees?: number | null;
  total_funding_fee?: number | null;
  /** 旧接口的夏普：UTC 逐日净值年化（√365） */
  sharpe_annualized_sqrt365?: number | null;
  sharpe_basis?: string | null;
  equity_points?: number | null;
  daily_points?: number | null;
}

export interface MetricStripProps {
  metrics: MetricLike | null;
  /** 回测窗口（毫秒）。保留在接口里供后续年化/年数口径使用 */
  windowStartMs?: number | null;
  windowEndMs?: number | null;
  loading: boolean;
}

interface Cell {
  key: string;
  label: string;
  value: string;
  note?: string;
  tone: 'pos' | 'neg' | 'plain' | 'muted';
}

export const MetricStrip: React.FC<MetricStripProps> = ({ metrics, loading }) => {
  const m = metrics;
  const trades = m?.trades_count ?? null;
  const noTrades = trades === 0;
  const ret = m?.net_return_pct ?? null;

  // 盈亏比：均值口径优先（设计稿的「盈亏比」= 平均盈利 ÷ 平均亏损），没有则退回聚合口径 PF
  const pf = m?.profit_factor;
  const wins = m?.wins ?? null;
  const losses = m?.losses ?? null;
  /**
   * 「为什么没有这个比值」必须说准：这两个比值都是以**净亏损**为分母。
   * 原先只要值缺失就统一写「缺少盈利样本」，于是在「盈 1 笔、亏 0 笔」的 run 上
   * 出现自相矛盾的文案（同一行胜率写着「盈 1 · 亏 0」，下一行却说没有盈利样本）。
   * 缺就是缺，但**缺失原因不能编**。
   */
  const undefinedWhy =
    wins === 0 || wins === null
      ? '无盈利样本，无定义'
      : losses === 0
        ? '无亏损样本（分母为 0），无定义'
        : '比值缺失，无定义';
  const ratio = m?.avg_win_loss_ratio ?? (pf != null && pf > 0 ? pf : null);
  const ratioNote =
    m?.avg_win_loss_ratio != null
      ? '平均盈利 ÷ 平均亏损（均值口径）'
      : pf != null && pf > 0
        ? 'Σ盈利 ÷ |Σ亏损|（聚合口径）'
        : undefinedWhy;

  // 收益因子（PF）：0 或缺失都视为无定义
  const pfValue = pf != null && pf > 0 ? pf.toFixed(2) : DASH;

  // 总费用：手续费 + 资金费，两者都记录才给合计
  const feeKnown = m?.total_fees != null;
  const fundKnown = m?.total_funding_fee != null;
  const totalCost = feeKnown && fundKnown ? (m!.total_fees as number) + (m!.total_funding_fee as number) : null;
  const costNote =
    totalCost != null
      ? '手续费 + 资金费（支出为正）'
      : feeKnown || fundKnown
        ? '费用未完整记录，不给合计'
        : '无费用记录';

  const cells: Cell[] = [
    {
      key: 'net',
      label: '净收益',
      value: noTrades ? '无成交' : fmtPct(ret),
      note: noTrades ? '该 run 账本 0 笔成交' : '账户成本后收益',
      tone: noTrades ? 'muted' : (ret ?? 0) > 0 ? 'pos' : (ret ?? 0) < 0 ? 'neg' : 'plain',
    },
    {
      key: 'dd',
      label: '最大回撤',
      value:
        m?.max_drawdown_pct === null || m?.max_drawdown_pct === undefined
          ? DASH
          : `-${fmtPctPlain(Math.abs(m.max_drawdown_pct))}`,
      note: m?.max_drawdown_pct == null ? '无权益点或未计算' : '含未平仓权益',
      tone: m?.max_drawdown_pct == null ? 'muted' : 'neg',
    },
    {
      key: 'sharpe',
      label: '夏普比率',
      // 旧接口给的是 UTC 逐日净值年化（√365）。没有就是没有，不填 0、不自己估。
      value:
        m?.sharpe_annualized_sqrt365 == null ? DASH : m.sharpe_annualized_sqrt365.toFixed(2),
      note:
        m?.sharpe_annualized_sqrt365 == null
          ? '后端未返回该指标'
          : m?.sharpe_basis || '按日净收益年化（√365）',
      tone: m?.sharpe_annualized_sqrt365 == null ? 'muted' : 'plain',
    },
    {
      key: 'pf',
      label: '收益因子',
      value: pfValue,
      note: pfValue === DASH ? undefinedWhy : '净盈利 ÷ 净亏损',
      tone: pfValue === DASH ? 'muted' : 'plain',
    },
    {
      key: 'trades',
      label: '成交笔数',
      value: fmtInt(trades),
      note: loading
        ? '加载中'
        : m?.equity_points
          ? `权益点 ${fmtInt(m.equity_points)}`
          : m?.daily_points
            ? `日净值点 ${fmtInt(m.daily_points)}`
            : '完整交易数',
      tone: 'plain',
    },
    {
      key: 'win',
      label: '胜率',
      value: noTrades ? '无成交' : fmtPctPlain(m?.win_rate_pct ?? null, 1),
      note: noTrades
        ? DASH
        : `盈 ${fmtInt(m?.wins)} · 亏 ${fmtInt(m?.losses)}${m?.flats ? ` · 平 ${fmtInt(m.flats)}` : ''}`,
      tone: noTrades ? 'muted' : 'plain',
    },
    {
      key: 'ratio',
      label: '盈亏比',
      value: ratio === null ? DASH : ratio.toFixed(2),
      note: ratioNote,
      tone: ratio === null ? 'muted' : 'plain',
    },
    {
      key: 'cost',
      label: '总费用',
      value: totalCost == null ? DASH : `-${fmtUsd(Math.abs(totalCost), 2)}`,
      note: costNote,
      tone: totalCost == null ? 'muted' : 'neg',
    },
  ];

  return (
    <div className="ds-metricstrip" data-testid="bt-metricstrip">
      {cells.map((c) => (
        <div className="ds-metric" key={c.key}>
          <div className="ds-metric__k">{c.label}</div>
          <div
            className={`ds-metric__v${
              c.tone === 'pos'
                ? ' ds-metric__v--pos'
                : c.tone === 'neg'
                  ? ' ds-metric__v--neg'
                  : c.tone === 'muted'
                    ? ' ds-metric__v--muted'
                    : ''
            }`}
          >
            {loading && !m ? DASH : c.value}
          </div>
          {c.note ? (
            <div className="ds-metric__note" title={c.note}>
              {c.note}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
};
