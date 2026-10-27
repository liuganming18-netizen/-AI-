/**
 * 批次条（BT-UI-01）
 * ----------------------------------------------------------------------------
 * 一行回答四个问题，且**四个答案来自同一个 `batch_id`**：
 *   ① 我在看哪一批？（批次下拉，真实目录名）
 *   ② 这批跑了什么？（run 数 / 有成交 run 数 / 成交笔数 / 净盈亏 / 窗口）
 *   ③ 这些数字从哪来？（`derived_from` + 自洽核对结果）
 *   ④ 它是什么性质的结论？（研究分类 · 未通过验收 —— 不给「已验证」暗示）
 *
 * 纪律：
 * · 汇总字段在 `summary_loaded=false` 时是 `null` ⇒ 显示「加载中」，**不显示 0**。
 * · 批次列表是轻量的（只数目录），选中后才懒算汇总 —— 所以这里必须有 loading 态。
 * · 净盈亏是「各 run net_pnl 相加」的美元口径；组合回撤/夏普不在批次层合成，
 *   标签直接写「批次净盈亏（各 run 相加）」，避免被读成组合绩效。
 */

import React from 'react';
import type { BtBatch, BtBatchSummaryResponse } from '../../api/backtestReview';
import { DASH, fmtInt, fmtUsd } from '../../utils/labFormat';

/** UTC 毫秒 → `2026-09-01 00:00`（带 UTC 后缀，防与北京时间混读） */
function fmtUtcMin(ms: number | null | undefined): string {
  if (!Number.isFinite(ms as number) || (ms as number) <= 0) return DASH;
  const d = new Date(ms as number);
  if (Number.isNaN(d.getTime())) return DASH;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

export interface BatchBarProps {
  batches: BtBatch[];
  batchId: string | null;
  onSelect: (id: string) => void;
  summary: BtBatchSummaryResponse | null;
  summaryLoading: boolean;
  summaryError: string | null;
  listLoading: boolean;
  listError: string | null;
  /** 批次模式：true = 列表/逐笔按批次；false = 按单 run（既有功能） */
  batchMode: boolean;
  onToggleMode: (v: boolean) => void;
  onRefresh: () => void;
}

export const BatchBar: React.FC<BatchBarProps> = ({
  batches,
  batchId,
  onSelect,
  summary,
  summaryLoading,
  summaryError,
  listLoading,
  listError,
  batchMode,
  onToggleMode,
  onRefresh,
}) => {
  const meta: BtBatch | null = summary?.batch ?? batches.find((b) => b.batch_id === batchId) ?? null;
  const checks = summary?.checks ?? null;
  const loaded = Boolean(meta?.summary_loaded);

  const checkText = (() => {
    if (!checks) return summaryLoading ? '核对中…' : '未核对';
    if (checks.trades_total_matches_rows === false) return '不自洽：汇总笔数 ≠ 逐笔行数';
    if (checks.net_pnl_matches_rows === false) return '不自洽：汇总净盈亏 ≠ 逐笔合计';
    return '自洽：汇总 = 逐笔合计';
  })();
  const checkOk = checks ? checks.trades_total_matches_rows && checks.net_pnl_matches_rows !== false : false;

  return (
    <div className="ds-card" data-testid="bt-batch-bar" data-batch-id={batchId ?? ''}>
      <div className="ds-bar" style={{ flexWrap: 'wrap', gap: 10 }}>
        <span className="ds-bar__label">批次</span>

        <label className="block" style={{ minWidth: 240 }} title="批次 = 一次批量运行的真实目录">
          <span className="text-[10px] text-neutral-500">回测批次（真实目录）</span>
          <select
            className="ds-select"
            value={batchId ?? ''}
            onChange={(e) => onSelect(e.target.value)}
            data-testid="bt-batch-select"
            disabled={listLoading && batches.length === 0}
          >
            {batches.length === 0 ? (
              <option value="">{listLoading ? '读取批次…' : '无批次目录'}</option>
            ) : (
              batches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id}
                  {b.is_default_root ? '（默认运行根）' : ''} · {b.runs_total} run
                </option>
              ))
            )}
          </select>
        </label>

        <div className="ds-seg" role="tablist" aria-label="取数口径">
          {(
            [
              [true, '按批次'],
              [false, '按单 run'],
            ] as [boolean, string][]
          ).map(([v, label]) => (
            <button
              key={label}
              type="button"
              role="tab"
              aria-selected={batchMode === v}
              className="ds-seg__item"
              onClick={() => onToggleMode(v)}
              data-testid={`bt-batch-mode-${v ? 'batch' : 'run'}`}
            >
              {label}
            </button>
          ))}
        </div>

        <span className="ds-bar__sep" />

        {/* 汇总指标：null ⇒ 「加载中」，绝不当 0 */}
        <span className="ds-mi" title="本批次 run 目录总数（轻量统计，不读 JSON）">
          <span className="ds-mi__k">运行数</span>
          <span className="ds-mi__v">{fmtInt(meta?.runs_total)}</span>
        </span>
        <span className="ds-mi" title="本批次里有成交（trade_count>0）的 run 数">
          <span className="ds-mi__k">有成交 run</span>
          <span className="ds-mi__v">
            {loaded ? fmtInt(meta?.runs_with_trades) : summaryLoading ? '加载中' : DASH}
          </span>
        </span>
        <span className="ds-mi" title="各 run summary_variant_a.trade_count 之和">
          <span className="ds-mi__k">成交笔数</span>
          <span className="ds-mi__v">
            {loaded ? fmtInt(meta?.trades_total) : summaryLoading ? '加载中' : DASH}
          </span>
        </span>
        <span className="ds-mi" title="各 run net_pnl 直接相加（美元）；不是组合净值口径">
          <span className="ds-mi__k">批次净盈亏（各 run 相加）</span>
          <span
            className={
              meta?.net_pnl_sum == null
                ? 'ds-mi__v'
                : `ds-mi__v ${meta.net_pnl_sum >= 0 ? 'ds-mi__v--pos' : 'ds-mi__v--neg'}`
            }
          >
            {meta?.net_pnl_sum == null
              ? summaryLoading
                ? '加载中'
                : DASH
              : `${meta.net_pnl_sum >= 0 ? '+' : '-'}$${fmtUsd(Math.abs(meta.net_pnl_sum), 2)}`}
          </span>
        </span>
        <span className="ds-mi" title={meta?.window_source ?? '窗口来自批次内取样 run 的 inputs.json'}>
          <span className="ds-mi__k">窗口（UTC）</span>
          <span className="ds-mi__v" style={{ fontSize: 'var(--ds-fs-xs)' }}>
            {meta?.window_start_ms
              ? `${fmtUtcMin(meta.window_start_ms)} → ${fmtUtcMin(meta.window_end_ms)}`
              : loaded
                ? DASH
                : '加载中'}
          </span>
        </span>

        <span className="ds-pagehead__spacer" />

        {/* 性质标注：不给「已验证」暗示 */}
        <span className="ds-badge ds-badge--warn" title="该策略 Gate 未过；页面只做研究复盘，不构成已验证结论">
          研究分类 · 未通过验收
        </span>
        <span
          className={`ds-badge ${checkOk ? 'ds-badge--pos' : checks ? 'ds-badge--neg' : 'ds-badge--neutral'}`}
          title={
            checks
              ? `汇总 trades_total=${checks.summary_trades_total} / 逐笔行数=${checks.trades_rows}；` +
                `汇总净盈亏=${checks.net_pnl_from_summary} / 逐笔合计=${checks.net_pnl_from_rows}`
              : '尚未取到核对结果'
          }
          data-testid="bt-batch-checks"
        >
          {checkText}
        </span>
        <button type="button" className="ds-btn ds-btn--ghost ds-btn--sm" onClick={onRefresh}>
          刷新
        </button>
      </div>

      {(listError || summaryError) && (
        <div className="ds-note ds-note--warn" style={{ margin: '0 12px 8px' }} data-testid="bt-batch-error">
          {listError ? `批次列表取数失败：${listError}` : ''}
          {summaryError ? `批次汇总取数失败：${summaryError}` : ''}
        </div>
      )}

      {meta?.derived_from && (
        <div
          className="text-[10px] text-neutral-500"
          style={{ padding: '0 12px 8px', fontFamily: 'var(--ds-mono)' }}
        >
          数据来源：{meta.derived_from}
          {meta.note ? ` · ${meta.note}` : ''}
        </div>
      )}
    </div>
  );
};

export default BatchBar;
