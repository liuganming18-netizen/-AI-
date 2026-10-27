/**
 * 回测复盘 · 右栏（成本与结果 / 数据与回放限制 / 复盘与样本外检查）
 * ----------------------------------------------------------------------------
 * 对照设计稿 `gm-layout.html` 右栏的三张卡。三条硬规矩：
 *   1. 只写后端真的给了的字段；没有就 `—`，并在副标里说明「为什么没有」。
 *   2. 滑点**不单列**（`/metrics` 没有该字段，手续费里已含取整与滑点影响）
 *      ⇒ 显示 `—` 并注明「后端未单列」，不用「手续费 − 手续费」之类凑数。
 *   3. 「样本外 / 参数稳定性」项目里没有这项检查 ⇒ 如实写「未检查」，不写「通过」。
 */

import React from 'react';
import { DASH, fmtDateTime, fmtInt, fmtUsd } from '../../utils/labFormat';
import type { ChartLike, RunDetailLike } from './RunInfoCard';

export interface BacktestSidePanelProps {
  metrics: {
    total_fees?: number | null;
    total_funding_fee?: number | null;
    net_pnl?: number | null;
    net_return_pct?: number | null;
    trades_count?: number | null;
  } | null;
  detail: RunDetailLike | null;
  chart: ChartLike | null;
  /** 质量核对是否通过（父组件的 qualityOkOfRun）；未知传 null */
  qualityOk: boolean | null;
  /** 质量状态原文（如 formal / unverified_historical）；未知传 null */
  qualityStatus: string | null;
  /** 图表缺失原因（后端 `missing_reason`）；没有缺失传 null */
  missingReason: string | null;
}

const Row: React.FC<{ k: string; v: React.ReactNode; note?: string }> = ({ k, v, note }) => (
  <div className="ds-kvline">
    <span className="ds-kvline__k">{k}</span>
    <span className="ds-kvline__v" title={note}>
      {v}
    </span>
  </div>
);

const Card: React.FC<{ title: string; meta?: string; children: React.ReactNode }> = ({
  title,
  meta,
  children,
}) => (
  <section className="ds-card">
    <div className="ds-card__head">
      <span className="ds-card__title">{title}</span>
      {meta ? <span className="ds-card__meta">{meta}</span> : null}
    </div>
    <div className="ds-card__body" style={{ paddingTop: 2 }}>
      {children}
    </div>
  </section>
);

export const BacktestSidePanel: React.FC<BacktestSidePanelProps> = ({
  metrics,
  detail,
  chart,
  qualityOk,
  qualityStatus,
  missingReason,
}) => {
  const m = metrics;
  const feeKnown = m?.total_fees != null;
  const fundKnown = m?.total_funding_fee != null;

  const winStart = detail?.window?.win_start_ms ?? chart?.window?.start_ms ?? null;
  const winEnd = detail?.window?.win_end_ms ?? chart?.window?.end_ms ?? null;
  const execTf = detail?.window?.exec_timeframe ?? detail?.config?.spec?.exec_timeframe ?? DASH;
  const barCount = chart?.candles?.length ?? null;

  return (
    <>
      <Card title="成本与结果明细">
        <Row
          k="手续费"
          v={feeKnown ? `-${fmtUsd(Math.abs(m!.total_fees as number), 2)} 美元` : DASH}
          note={feeKnown ? '入场 + 出场手续费合计（账本逐笔）' : '该 run 未记录手续费'}
        />
        <Row
          k="资金费"
          v={
            fundKnown
              ? // 后端 `total_funding_fee` 正数 = 支出、负数 = 收入（成本口径）
                `${(m!.total_funding_fee as number) >= 0 ? '-' : '+'}${fmtUsd(
                  Math.abs(m!.total_funding_fee as number),
                  2,
                )} 美元`
              : DASH
          }
          note={fundKnown ? '正数=支出，负数=收入（成本口径）' : '该 run 未记录资金费'}
        />
        <Row k="滑点影响" v={DASH} note="后端未单列；滑点与取整影响已含在成交价里" />
        <Row
          k="已实现净盈亏"
          v={
            m?.net_pnl == null
              ? DASH
              : `${m.net_pnl < 0 ? '-' : '+'}${fmtUsd(Math.abs(m.net_pnl), 2)} 美元`
          }
          note={m?.net_pnl == null ? '无已平仓交易' : '扣手续费与资金费后'}
        />
        {(!fundKnown || !feeKnown) && (
          <div className="ds-nt2">资金费或手续费缺失 ⇒ 本卡结果受限，不能当完整成本</div>
        )}
      </Card>

      <Card title="数据与回放限制" meta={execTf !== DASH ? `${execTf} 粒度` : undefined}>
        <Row
          k="回测窗口 (UTC)"
          v={
            winStart == null || winEnd == null
              ? DASH
              : `${fmtDateTime(winStart)} → ${fmtDateTime(winEnd)}`
          }
          note="取自该 run 自己的窗口，不用表单里的值"
        />
        <Row k="K 线根数" v={barCount == null ? DASH : fmtInt(barCount)} note="该 run 图载荷实际根数" />
        <Row
          k="图表缺失"
          v={missingReason ? '有' : chart ? '无' : DASH}
          note={missingReason ?? undefined}
        />
        <Row k="未来数据检查" v="未执行" note="项目未实现该检查，不标「通过」" />
      </Card>

      <Card title="复盘与样本外检查">
        <Row
          k="质量核对"
          v={qualityOk == null ? DASH : qualityOk ? '通过' : '未通过'}
          note="来自 run 的质量状态，不是策略有效性结论"
        />
        <Row k="状态原文" v={qualityStatus ?? DASH} />
        <Row k="样本外 / 参数稳定性" v="未检查" note="项目未实现，不臆测" />
        <Row
          k="收益口径"
          v={m?.net_return_pct == null ? DASH : `${m.net_return_pct.toFixed(2)}%`}
          note="末点权益 / 首点权益 − 1（扣费后）"
        />
      </Card>
    </>
  );
};
