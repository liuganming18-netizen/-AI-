/**
 * 回测参数与信息卡
 * ----------------------------------------------------------------------------
 * 只写后端真的给了的字段。`run_id` 的时间后缀是稳定可信的「运行时刻」；
 * CODE-REVIEW-02 起，`created_at/finished_at` 已是落盘真实值（有 inputs.json 的
 * 历史 run）或 null（未记录），不再是读取时刻兜底，缺了一律显示「—」。
 */

import React from 'react';
import { runEpochMs, symbolLabel } from '../../utils/runMeta';
import { DASH, fmtDateTime, fmtInt, fmtNum, fmtPctPlain } from '../../utils/labFormat';

/**
 * 入参一律用**宽松结构类型**，不绑定某一套接口。
 * 项目里并存两套：`LabRunDetail`/`LabChart`（`api/labBacktest`，回测页主体在用）
 * 与 `BtRunDetail`/`BtChartPayload`（`api/backtestReview`，批次接口）。
 * 两者字段同名但必填性不同（`run_id` 一个可空），直接互相赋值会报类型错，
 * 而这几个组件只读少数字段 ⇒ 声明成可选即可两边通吃。
 */
export interface RunLike {
  run_id?: string | null;
  symbols?: string[] | null;
}

export interface ActualConfigLike {
  initial_equity?: number | null;
  risk_budget_per_trade?: number | null;
  fee_rate?: number | null;
  slippage_bps?: number | null;
}

export interface RunDetailLike {
  run_id?: string | null;
  engine?: { strategy_id?: string | null; exec_core_version?: string | null } | null;
  window?: {
    symbol?: string | null;
    exchange?: string | null;
    win_start_ms?: number | null;
    win_end_ms?: number | null;
    exec_timeframe?: string | null;
  } | null;
  /**
   * 两套接口的 `config` 结构不同：
   *   · 旧 `LabRunDetail`：`config.spec` + `config.config.{signal_params,exec_params}` + `config.actual_config`
   *   · 新 `BtRunDetail`：`config.config.actual_config`
   * 共同属性只有 `spec` 一项，写窄类型会因「无共同属性」被判不可赋值 ⇒ 这里放开，
   * 组件内部只读 `spec.*` 与 `actual_config.*` 这两处（见下方 `cfg` 取值）。
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  config?: any;
}

export interface ChartLike {
  window?: { start_ms?: number | null; end_ms?: number | null } | null;
  candles?: unknown[] | null;
  equity_curve?: unknown[] | null;
}

/** 只吃这几个字段 ⇒ 不绑定某一套指标接口（`LabMetrics` 与 `BtMetrics` 都能传）。 */
export interface RunInfoMetrics {
  equity_points?: number | null;
  daily_points?: number | null;
  total_fees?: number | null;
  total_funding_fee?: number | null;
  trades_count?: number | null;
}

export interface RunInfoCardProps {
  run: RunLike | null;
  detail: RunDetailLike | null;
  metrics: RunInfoMetrics | null;
  chart: ChartLike | null;
}

/** 设计稿 `.fld`：左边标签、右边浅灰胶囊。长值换行不溢出。 */
const Row: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
  <div className="ds-fld">
    <span className="ds-fld__k">{k}</span>
    <span className="ds-fld__v">{v}</span>
  </div>
);

export const RunInfoCard: React.FC<RunInfoCardProps> = ({ run, detail, metrics, chart }) => {
  const cfg = detail?.config?.config?.actual_config ?? detail?.config?.actual_config ?? null;
  // `run` 来自批次列表（另一套接口），未必拿得到 ⇒ 逐级退到 detail 自己的字段
  const symbol =
    run?.symbols?.[0] ??
    detail?.window?.symbol ??
    detail?.config?.spec?.symbols?.[0] ??
    '';
  const runId = run?.run_id ?? detail?.run_id ?? '';

  return (
    <section className="ds-card">
      <div className="ds-card__head">
        <span className="ds-card__title">历史回测配置</span>
      </div>
      <div
        className="ds-card__body"
        style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', rowGap: 0 }}
      >
        <Row k="交易对" v={symbol ? symbolLabel(symbol) : DASH} />
        <Row k="数据来源" v={detail?.config?.spec?.exchange ?? detail?.window?.exchange ?? DASH} />
        <Row
          k="信号 / 执行周期"
          v={
            detail?.config?.spec
              ? `${detail.config.spec.signal_timeframe ?? DASH} / ${detail.config.spec.exec_timeframe ?? DASH}`
              : DASH
          }
        />
        <Row k="运行时间 (UTC)" v={fmtDateTime(runEpochMs(runId))} />
        <Row
          k="初始权益"
          v={cfg?.initial_equity != null ? `${fmtNum(cfg.initial_equity, 0)} USDT` : DASH}
        />
        <Row
          k="单笔风险预算"
          v={cfg?.risk_budget_per_trade != null ? fmtPctPlain(cfg.risk_budget_per_trade * 100, 2) : DASH}
        />
        <Row k="手续费率" v={cfg?.fee_rate != null ? `${(cfg.fee_rate * 100).toFixed(3)}%` : DASH} />
        <Row k="滑点" v={cfg?.slippage_bps != null ? `${cfg.slippage_bps} bps` : DASH} />
      </div>

      {/* 设计稿在这张卡底部还有一段 12px 灰色说明（`.m`）。
          这里放**放不进胶囊的长值**：引擎全名、采样点数、费用合计。
          值都来自后端，取不到写 `—`。 */}
      <div className="ds-fldnote">
        <div>
          回测引擎：
          {detail?.engine
            ? `${detail.engine.strategy_id ?? DASH} · 执行核心 ${detail.engine.exec_core_version ?? DASH}`
            : DASH}
        </div>
        <div>权益采样点：{fmtInt(metrics?.equity_points ?? metrics?.daily_points ?? chart?.equity_curve?.length ?? null)}</div>
        <div>
          手续费 / 资金费：
          {metrics
            ? `${
                metrics.total_fees == null
                  ? DASH
                  : metrics.total_fees === 0 && (metrics.trades_count ?? 0) > 0
                    ? '0.0000 (未计费用)'
                    : metrics.total_fees.toFixed(4)
              } / ${
                metrics.total_funding_fee == null ? DASH : metrics.total_funding_fee.toFixed(4)
              } USDT`
            : DASH}
        </div>
      </div>
    </section>
  );
};
