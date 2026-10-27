/**
 * 主区头部：标题 / 状态 / 时间范围 / 策略与参数 / 导出
 * ----------------------------------------------------------------------------
 * 时间范围优先用 `chart.window`（唯一可信来源）；列表接口的 window 字段恒为 null。
 * 参数摘要来自 `config.config.actual_config`，缺字段就少写一段，不补默认值。
 */

import React from 'react';
import { Download, MoreHorizontal } from 'lucide-react';
import type { BtRunDetail, BtRunListItem } from '../../api/backtestReview';
import {
  parseRunId,
  statusLabel,
  statusTone,
  symbolLabel,
} from '../../utils/runMeta';
import { DASH, fmtDate, fmtNum } from '../../utils/labFormat';

export interface RunHeaderProps {
  run: BtRunListItem | null;
  detail: BtRunDetail | null;
  windowStartMs: number | null;
  windowEndMs: number | null;
  onExport: () => void;
  loading: boolean;
}

export const RunHeader: React.FC<RunHeaderProps> = ({
  run,
  detail,
  windowStartMs,
  windowEndMs,
  onExport,
  loading,
}) => {
  const symbol = run?.symbols?.[0] ?? parseRunId(run?.run_id).symbol ?? '';
  const tf = detail?.config?.spec?.signal_timeframe || run?.window?.signal_timeframe || '';
  const tone = statusTone(run?.status);
  const badgeClass =
    tone === 'ok'
      ? 'ds-badge ds-badge--pos ds-badge--lg'
      : tone === 'warn'
        ? 'ds-badge ds-badge--warn ds-badge--lg'
        : tone === 'bad'
          ? 'ds-badge ds-badge--neg ds-badge--lg'
          : 'ds-badge ds-badge--neutral ds-badge--lg';

  const cfg = detail?.config?.config?.actual_config ?? detail?.config?.actual_config ?? null;
  const paramBits: string[] = [];
  if (cfg?.initial_equity != null) paramBits.push(`初始 ${fmtNum(cfg.initial_equity, 0)} USDT`);
  if (cfg?.risk_budget_per_trade != null) paramBits.push(`单笔风险 ${(cfg.risk_budget_per_trade * 100).toFixed(2)}%`);
  if (cfg?.fee_rate != null) paramBits.push(`费率 ${(cfg.fee_rate * 100).toFixed(3)}%`);
  if (cfg?.slippage_bps != null) paramBits.push(`滑点 ${cfg.slippage_bps} bps`);

  const range =
    windowStartMs != null && windowEndMs != null
      ? `${fmtDate(windowStartMs)} → ${fmtDate(windowEndMs)}`
      : DASH;

  return (
    <header className="ds-bt-head">
      <div style={{ minWidth: 0 }}>
        <div className="ds-bt-head__title">
          <span>
            {symbol ? symbolLabel(symbol) : DASH} {tf} 回测
          </span>
          <span className={badgeClass}>{statusLabel(run?.status)}</span>
          {/* CODE-REVIEW-02：未登记（无 run.json 的历史批量产物）如实标注，
             状态即便按产物证据推断为 succeeded，也不冒充正式运行档案 */}
          {run?.registered === false && (
            <span
              className="ds-badge ds-badge--neutral ds-badge--lg"
              title={run.record_note ?? '该运行无 run.json：历史批量产物，未登记（只读参考）'}
              data-testid="run-unregistered-badge"
            >
              未登记
            </span>
          )}
        </div>
        <div className="ds-bt-head__sub">
          <span>
            时间范围：<b style={{ color: 'var(--ds-text-2)' }}>{loading ? '加载中…' : range}</b>
          </span>
          <span>策略：{detail?.config?.spec?.strategy_id || run?.strategy_id || DASH}</span>
          <span>参数：{paramBits.length ? paramBits.join(' · ') : DASH}</span>
          <span>执行周期：{detail?.config?.spec?.exec_timeframe || run?.window?.exec_timeframe || DASH}</span>
        </div>
      </div>

      <div className="ds-bt-head__actions">
        <button
          type="button"
          className="ds-btn ds-btn--secondary ds-btn--sm"
          onClick={onExport}
          disabled={!run}
          title="导出该 run 的指标与逐笔成交（CSV）"
        >
          <Download className="w-3.5 h-3.5" />
          导出报告
        </button>
        <button
          type="button"
          className="ds-btn ds-btn--ghost ds-btn--icon"
          title={run ? `run_id: ${run.run_id}` : '未选择回测'}
          aria-label="更多信息"
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
};
