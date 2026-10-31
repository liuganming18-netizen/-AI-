/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 逐笔订单可视化定点组件 (GM-BACKTEST-ORDER-UI-18)
 * ----------------------------------------------------------------------------
 * 1. 完整展示：信号、委托、成交、持仓、平仓、手续费、滑点、资金费、净盈亏及退出原因（10项核心要素）。
 * 2. 深度追溯：支持点击信号、委托、成交、止损抬升节点及资金费结算节点直接定位对应 K 线和事件。
 * 3. 严格数据纪律：对缺失字段明确显示“暂无数据”，绝不编造数值。
 */

import React, { useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownCircle,
  ArrowRight,
  ArrowUpCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Compass,
  Crosshair,
  DollarSign,
  FileText,
  Layers,
  Percent,
  Shield,
  Tag,
  Zap,
} from 'lucide-react';
import type { LabTradeDetail, LabOrderRow, LabFundingRow } from '../../api/labBacktest';
import { TrustStateBadge, TrustStateAlertBanner } from './TrustStateBadge';
import { directionIsLong, exitReasonLabel } from '../../utils/runMeta';

/* ── 缺失字段统一标识 ──────────────────────────────────────────────────────── */
export const NO_DATA_TEXT = '暂无数据';

export function NoDataBadge({ note }: { note?: string }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-sans bg-neutral-800/80 text-neutral-400 border border-neutral-700/50"
      title={note || '该字段在上游落盘账本中未记录，暂无数据'}
      data-testid="no-data-field"
    >
      {NO_DATA_TEXT}
    </span>
  );
}

/** 格式化工具函数：缺值严格返回 NoDataBadge，绝不编造 0 或默认值 */
export function fmtValue(
  val: unknown,
  formatter?: (x: any) => React.ReactNode,
  note?: string,
): React.ReactNode {
  if (val === null || val === undefined || val === '') {
    return <NoDataBadge note={note} />;
  }
  if (typeof val === 'number' && !Number.isFinite(val)) {
    return <NoDataBadge note={note} />;
  }
  return formatter ? formatter(val) : String(val);
}

export function fmtUtcTime(ms: unknown): React.ReactNode {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) {
    return <NoDataBadge note="上游账本未记录时间戳" />;
  }
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return <NoDataBadge note="时间戳格式无效" />;
  const p = (n: number) => String(n).padStart(2, '0');
  const str = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
  return (
    <span className="font-mono text-neutral-200" title={`时间戳: ${ms} 毫秒 (UTC)`}>
      {str} <span className="text-[9px] text-neutral-500">UTC</span>
    </span>
  );
}

export function fmtMoney(num: unknown, digits = 2, unit = 'USDT'): React.ReactNode {
  if (typeof num !== 'number' || !Number.isFinite(num)) {
    return <NoDataBadge />;
  }
  const sign = num > 0 ? '+' : '';
  const text = num.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return (
    <span className="font-mono">
      {sign}
      {text} {unit ? <span className="text-[10px] font-sans text-neutral-400">{unit}</span> : null}
    </span>
  );
}

export function fmtNumVal(num: unknown, digits = 4): React.ReactNode {
  if (typeof num !== 'number' || !Number.isFinite(num)) {
    return <NoDataBadge />;
  }
  return (
    <span className="font-mono">
      {num.toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })}
    </span>
  );
}

export interface OrderDetailViewProps {
  detail: LabTradeDetail | null;
  loading: boolean;
  error: string | null;
  /** PROV-01 / 57 号修复：所属运行的可信防伪状态 */
  trustState?: string | null;
  /** 可信报告明细（含 reasons 拒绝原因等） */
  trust?: {
    schema?: string;
    run_id?: string;
    state: string;
    reasons?: Array<{ code: string; detail?: string; message?: string }>;
    checks?: Record<string, unknown>;
  } | null;
  /** 点击时定位图表到具体时间点，可附带选中的订单 ID */
  onLocateTime?: (timeMs: number, orderId?: string | null, reason?: string) => void;
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  currentIndex?: number;
  totalCount?: number;
  initialEquity?: number | null;
  /**
   * 未选中/零成交时的说明文案（由调用方按 `chart.signals` 真实计数生成）。
   * 零成交的 run 里若仍写「请点击任意一笔交易」，等于指着一个不存在的行让人点。
   */
  emptyHint?: string | null;
}

export const OrderDetailView: React.FC<OrderDetailViewProps> = ({
  detail,
  loading,
  error,
  trustState,
  trust,
  onLocateTime,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  currentIndex,
  totalCount,
  initialEquity,
  emptyHint,
}) => {
  const [activeTab, setActiveTab] = useState<'all' | 'lifecycle' | 'cost' | 'trace' | 'funding'>('all');

  const rawTrade = detail?.trade;
  const stopTrace = useMemo(() => {
    if (!rawTrade) return [];
    if (Array.isArray(rawTrade.stop_trace) && rawTrade.stop_trace.length > 0) {
      return rawTrade.stop_trace;
    }
    if (rawTrade.stop_trace_json) {
      try {
        const parsed = JSON.parse(rawTrade.stop_trace_json);
        if (Array.isArray(parsed)) return parsed;
      } catch {
        // ignore json error
      }
    }
    return [];
  }, [rawTrade]);

  if (loading && !detail) {
    return (
      <div className="p-8 text-center text-neutral-400 font-mono text-xs flex items-center justify-center gap-2">
        <div className="w-4 h-4 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
        正在读取逐笔订单与全链路凭证…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 rounded-lg border border-rose-800/60 bg-rose-950/20 text-rose-300 text-xs flex items-center gap-2">
        <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
        <span>逐笔订单数据读取失败：{error}</span>
      </div>
    );
  }

  if (!detail?.trade) {
    return (
      <div
        className="p-8 text-center rounded-lg border border-neutral-800 bg-neutral-900/40 text-neutral-400 text-xs"
        data-testid="order-detail-empty"
      >
        <Compass className="w-6 h-6 mx-auto mb-2 text-neutral-500" />
        <div>
          {emptyHint ||
            '请在上方表格中点击任意一笔交易，即可在此查看该订单完整 10 项要素追溯明细'}
        </div>
      </div>
    );
  }

  const t = detail.trade;
  const sig = detail.original_signal;
  const orders = detail.orders ?? [];
  const funding = detail.funding ?? [];
  const crossCheck = detail.funding_cross_check;

  // 1. 拆解委托订单：开仓委托 vs 平仓委托
  const entryOrder = orders.find(
    (o) => o.phase === 'entry' || (o.phase && o.phase.includes('entry')) || (o.action && o.action.includes('open')) || o.order_id === t.order_id || o.order_id === t.entry_order_id,
  );
  const exitOrder = orders.find(
    (o) => o.phase === 'exit' || (o.phase && o.phase.includes('intrabar')) || (o.action && o.action.includes('exit')) || (o.action && o.action.includes('close')) || o.order_id === t.exit_order_id,
  );

  // 信号触发与确认时刻：只认后端真给的字段。实测 `original_signal` **不含**
  // `kind` / `signal_bar_open_ms` / `confirm_ms`（那三个只在 `chart.signals[]` 里）。
  const sigTime =
    sig?.snapshot_time_ms ?? sig?.available_at_ms ?? t.signal_time_ms ?? null;
  const sigConfirmTime = sig?.execution_earliest_ms ?? t.confirm_time_ms ?? null;
  /** 形态：只认后端 `kind`；缺失即 null ⇒ 界面写「暂无数据」，**不按 `breakout_close` 推断形态名** */
  const sigKindLabel = sig?.kind ?? null;

  // 方向：`/trades` 给 1 / -1（个别产物给 long/short）。解析不出来 ⇒ null = 方向未知，
  // 绝不能默认落到「做空」——那是把缺失字段当成了确定结论。
  const dirLong = directionIsLong(t.direction);
  const isLong = dirLong === true;
  const netPnl = typeof t.net_pnl === 'number' && Number.isFinite(t.net_pnl) ? t.net_pnl : null;
  const isWin = netPnl !== null && netPnl > 0;
  const pnlPct =
    initialEquity && initialEquity > 0 && netPnl !== null
      ? (netPnl / initialEquity) * 100
      : null;

  // 滑点与取整：账本口径 `slippage_loss = slippage_loss_pure + rounding_loss`，
  // 且**已经体现在成交价里**（gross_pnl 由 post_slip/post_round 价算出）⇒ 只能展示，不得再扣一次。
  const slippageLoss = t.slippage_loss ?? null;
  const slippagePure = t.slippage_loss_pure ?? null;
  const roundingLoss = t.rounding_loss ?? null;
  const priceAudit = t.price_audit;

  /**
   * 会计恒等式残差：净盈亏 = 毛盈亏 − 双边手续费 − 资金费（`funding_fee` 正数 = 成本）。
   * 四项缺任一 ⇒ null（界面写「无法核算」）。**绝不硬编码 0.0000**：
   * 那会让「账本不平」与「字段没取到」都显示成「已平账」。
   */
  const ledgerResidual = (() => {
    const g = t.gross_pnl;
    const f = t.fees;
    const fd = t.funding_fee;
    const n = t.net_pnl;
    const ok = [g, f, fd, n].every((x) => typeof x === 'number' && Number.isFinite(x));
    return ok ? (g as number) - (f as number) - (fd as number) - (n as number) : null;
  })();
  const residualBalanced = ledgerResidual !== null && Math.abs(ledgerResidual) <= 1e-6;
  const effectiveTrustState = trustState ?? detail.trust_state ?? null;
  const effectiveReasons = trust?.reasons ?? detail.trust?.reasons ?? null;

  return (
    <div className="space-y-4 text-xs font-sans text-neutral-200" data-testid="order-detail-container">
      {/* ── 可信验证失败显著警示条（若所属回测被 REJECTED） ── */}
      <TrustStateAlertBanner
        state={effectiveTrustState}
        reasons={effectiveReasons}
        runId={detail.run_id ?? rawTrade?.run_id ?? null}
      />

      {/* ── 顶部头栏：标的、订单 ID、全景方向与上下一笔导航 ── */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-xl border border-neutral-800 bg-neutral-900/90 backdrop-blur-sm shadow-sm">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="font-bold text-base text-neutral-100 font-sans tracking-wide">
            {t.symbol || NO_DATA_TEXT}
          </span>
          <TrustStateBadge state={effectiveTrustState} testid="order-detail-trust-badge" />
          <span
            className={`px-2.5 py-1 rounded-md font-semibold text-xs inline-flex items-center gap-1 ${
              dirLong === null
                ? 'bg-neutral-800/80 text-neutral-300 border border-neutral-600/60'
                : isLong
                  ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-700/60'
                  : 'bg-rose-950/80 text-rose-300 border border-rose-700/60'
            }`}
            data-testid="order-direction-badge"
            data-direction={dirLong === null ? 'unknown' : isLong ? 'long' : 'short'}
          >
            {dirLong === null ? (
              <AlertCircle className="w-3.5 h-3.5" />
            ) : isLong ? (
              <ArrowUpCircle className="w-3.5 h-3.5" />
            ) : (
              <ArrowDownCircle className="w-3.5 h-3.5" />
            )}
            {dirLong === null ? '方向未知' : isLong ? '做多 (LONG)' : '做空 (SHORT)'}
          </span>
          <span className="text-neutral-500 font-mono text-[11px]">
            Trade ID: <span className="text-neutral-300">{fmtValue(t.trade_id)}</span>
          </span>
          {t.order_id && (
            <span className="text-neutral-500 font-mono text-[11px]">
              开仓单: <span className="text-neutral-300">{t.order_id}</span>
            </span>
          )}
          {t.exit_order_id && (
            <span className="text-neutral-500 font-mono text-[11px]">
              平仓单: <span className="text-neutral-300">{t.exit_order_id}</span>
            </span>
          )}
          <span className="px-2 py-0.5 rounded text-[11px] bg-neutral-800 text-neutral-300 border border-neutral-700">
            退出原因: {exitReasonLabel(t.exit_reason)}
          </span>
        </div>

        {/* 上下一笔切换 */}
        <div className="flex items-center gap-2">
          {currentIndex !== undefined && totalCount !== undefined && (
            <span className="text-neutral-400 font-mono text-[11px] mr-1">
              第 <strong className="text-neutral-200">{currentIndex + 1}</strong> / {totalCount} 笔
            </span>
          )}
          <button
            type="button"
            className="px-2.5 py-1 rounded border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 disabled:opacity-30 disabled:pointer-events-none transition-colors inline-flex items-center gap-1 text-[11px]"
            onClick={onPrev}
            disabled={!hasPrev}
            title="查看上一笔订单"
          >
            <ChevronLeft className="w-3.5 h-3.5" /> 上一笔
          </button>
          <button
            type="button"
            className="px-2.5 py-1 rounded border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 disabled:opacity-30 disabled:pointer-events-none transition-colors inline-flex items-center gap-1 text-[11px]"
            onClick={onNext}
            disabled={!hasNext}
            title="查看下一笔订单"
          >
            下一笔 <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* ── 后台已标注的缺失项：原样列出，不补值、不静默 ── */}
      {(detail.missing ?? []).length > 0 && (
        <div
          className="p-2.5 rounded-lg border border-amber-800/60 bg-amber-950/20 text-amber-200 text-[11px] flex items-start gap-2"
          data-testid="order-detail-missing"
        >
          <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <div className="font-semibold">后台标注的缺失项（如实展示，不补值）</div>
            <ul className="list-disc list-inside text-amber-300/90">
              {(detail.missing ?? []).map((m, i) => (
                <li key={`missing-${i}`}>{m}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* ── 核心指标与会计对账条（毛盈亏 - 手续费 - 资金费 = 净盈亏） ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
        <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 flex flex-col justify-between">
          <div className="text-[10px] text-neutral-400 font-sans">毛盈亏 (Gross PnL)</div>
          <div className="text-sm font-bold mt-1 text-neutral-200">
            {fmtMoney(t.gross_pnl, 4)}
          </div>
          <div className="text-[9px] text-neutral-500 mt-0.5">按实际成交价计，已含滑点与取整</div>
        </div>

        <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 flex flex-col justify-between">
          <div className="text-[10px] text-neutral-400 font-sans">双边手续费 (Fees)</div>
          <div className="text-sm font-bold mt-1 text-amber-300">
            {typeof t.fees === 'number' && Number.isFinite(t.fees)
              ? `-${t.fees.toFixed(4)} USDT`
              : <NoDataBadge />}
          </div>
          <div className="text-[9px] text-neutral-500 mt-0.5">
            入 {fmtValue(t.entry_fee, (v) => v.toFixed(4))} / 出 {fmtValue(t.exit_fee, (v) => v.toFixed(4))}
          </div>
        </div>

        <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 flex flex-col justify-between">
          <div className="text-[10px] text-neutral-400 font-sans">资金费收支 (Funding)</div>
          <div className="text-sm font-bold mt-1 text-indigo-300">
            {typeof t.funding_fee === 'number' && Number.isFinite(t.funding_fee) ? (
              <>
                {t.funding_fee > 0 ? '-' : t.funding_fee < 0 ? '+' : ''}
                {Math.abs(t.funding_fee).toFixed(4)} USDT
              </>
            ) : (
              <NoDataBadge />
            )}
          </div>
          <div className="text-[9px] text-neutral-500 mt-0.5">
            {typeof t.funding_fee === 'number' && Number.isFinite(t.funding_fee)
              ? `${t.funding_fee > 0 ? '支出' : t.funding_fee < 0 ? '返还' : '无结算'} · `
              : ''}
            共 {fmtValue(t.funding_events, (x) => `${x} 次结算`)}
          </div>
        </div>

        <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 flex flex-col justify-between">
          <div className="text-[10px] text-neutral-400 font-sans">滑点损耗 (Slippage)</div>
          <div className="text-sm font-bold mt-1 text-neutral-200">
            {slippageLoss != null ? (
              <span className="text-rose-400 font-mono">-{slippageLoss.toFixed(4)} USDT</span>
            ) : (
              <NoDataBadge note="账本未单列滑点损耗" />
            )}
          </div>
          <div className="text-[9px] text-neutral-500 mt-0.5">
            {roundingLoss != null ? `取整 ${roundingLoss.toFixed(4)}` : '取整 暂无数据'}
            {' · '}
            {slippagePure != null ? `纯滑点 ${slippagePure.toFixed(4)}` : '纯滑点 暂无数据'}
          </div>
          <div className="text-[9px] text-neutral-600">已含入成交价，不重复扣减</div>
        </div>

        <div
          className={`p-3 rounded-lg border flex flex-col justify-between ${
            netPnl === null
              ? 'border-neutral-800/80 bg-neutral-950/60'
              : isWin
                ? 'border-emerald-800/60 bg-emerald-950/20'
                : 'border-rose-800/60 bg-rose-950/20'
          }`}
        >
          <div className="text-[10px] text-neutral-400 font-sans">净盈亏 (Net PnL)</div>
          <div
            className={`text-base font-bold mt-1 font-mono ${
              netPnl === null ? 'text-neutral-300' : isWin ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {fmtMoney(netPnl, 4)}
          </div>
          <div className="text-[9px] text-neutral-400 mt-0.5">
            {pnlPct != null ? `账户收益率 ${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%` : '最终实现损益'}
          </div>
        </div>

        <div className="p-3 rounded-lg border border-neutral-800/80 bg-neutral-950/60 flex flex-col justify-between">
          <div className="text-[10px] text-neutral-400 font-sans">会计平账核算</div>
          <div
            className={`text-[11px] font-mono mt-1 flex items-center gap-1 ${
              ledgerResidual === null
                ? 'text-neutral-400'
                : residualBalanced
                  ? 'text-emerald-300'
                  : 'text-rose-300'
            }`}
            data-testid="order-ledger-residual"
            data-residual={ledgerResidual === null ? 'null' : String(ledgerResidual)}
          >
            {ledgerResidual === null ? (
              <AlertCircle className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
            ) : residualBalanced ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
            ) : (
              <AlertCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
            )}
            <span>
              {ledgerResidual === null
                ? '无法核算（字段不全）'
                : residualBalanced
                  ? '平账残差 0.000000 U'
                  : `平账残差 ${ledgerResidual.toFixed(6)} U（不平）`}
            </span>
          </div>
          <div className="text-[9px] text-neutral-500 mt-0.5 font-mono truncate" title="净利 = 毛利 - 手续费 - 资金费（funding_fee 正数=成本）">
            净 = 毛 − 费 − 资金费
          </div>
        </div>
      </div>

      {/* ── 核心生命周期穿透卡片（信号 → 委托 → 成交 → 持仓 → 平仓） ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 1. 信号事件 (Signal) */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3" data-testid="order-section-signal">
          <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
            <div className="flex items-center gap-1.5 font-bold text-neutral-100">
              <Zap className="w-4 h-4 text-amber-400" />
              <span>① 信号事件 (Signal)</span>
            </div>
            {/* 点击定位信号事件按钮 */}
            {sigTime ? (
              <button
                type="button"
                className="px-2 py-0.5 rounded bg-amber-950/80 hover:bg-amber-900/90 text-amber-200 border border-amber-700/60 text-[11px] font-mono transition-colors inline-flex items-center gap-1"
                onClick={() => {
                  if (sigTime && onLocateTime) onLocateTime(sigTime, null, '定位候选信号事件');
                }}
                title="在 K 线图上高亮聚焦该信号触发的 K 线"
                data-testid="locate-signal-btn"
              >
                <Crosshair className="w-3 h-3 text-amber-300" />
                定位信号事件 K 线
              </button>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[11px]">
            <div>
              <span className="text-neutral-500">信号 ID:</span>{' '}
              <span className="font-mono text-neutral-300 break-all">
                {fmtValue(t.signal_id || sig?.candidate_id || sig?.signal_id)}
              </span>
            </div>
            <div>
              <span className="text-neutral-500">形态与方向:</span>{' '}
              <span className="font-sans text-neutral-200">
                {dirLong === null ? '方向未知' : isLong ? '做多' : '做空'}
              </span>
              {' '}
              {sigKindLabel ? (
                <span className="font-sans text-neutral-300">· {sigKindLabel}</span>
              ) : (
                <NoDataBadge note="后端候选表（candidates.jsonl）无 kind 字段；不按 breakout_close 推断形态名" />
              )}
            </div>
            <div>
              <span className="text-neutral-500">信号触发时刻:</span>{' '}
              {fmtUtcTime(sigTime)}
            </div>
            <div>
              <span className="text-neutral-500">最早执行/确认时刻:</span>{' '}
              {fmtUtcTime(sigConfirmTime)}
            </div>
            <div>
              <span className="text-neutral-500">箱体上下沿 (H / L):</span>{' '}
              <span className="font-mono text-neutral-300">
                {sig?.box_high_h != null && sig?.box_low_l != null ? (
                  `${sig.box_high_h} / ${sig.box_low_l}`
                ) : (
                  <NoDataBadge note="未记录箱体上下沿" />
                )}
              </span>
            </div>
            <div>
              <span className="text-neutral-500">波动率 ATR:</span>{' '}
              {fmtNumVal(sig?.atr ?? sig?.box_atr_a, 4)}
            </div>
            <div>
              <span className="text-neutral-500">成交量放大倍数:</span>{' '}
              {sig?.volume_ratio != null ? (
                <span className="font-mono text-neutral-200">{sig.volume_ratio.toFixed(2)}x</span>
              ) : (
                <NoDataBadge />
              )}
            </div>
            <div>
              <span className="text-neutral-500">OI 持仓量评分:</span>{' '}
              {sig?.oi_score != null ? (
                <span className="font-mono text-neutral-200">{sig.oi_score.toFixed(3)}</span>
              ) : sig?.oi_growth_6h != null ? (
                <span className="font-mono text-neutral-200">
                  +{(sig.oi_growth_6h * 100).toFixed(2)}% (6h)
                </span>
              ) : (
                <NoDataBadge />
              )}
            </div>
            <div>
              <span className="text-neutral-500">突破收盘价:</span>{' '}
              {fmtNumVal(sig?.breakout_close, 4)}
            </div>
            <div>
              <span className="text-neutral-500">信号期基准止损:</span>{' '}
              <span title="候选表 initial_stop_price（Lr − 0.1A）；不等于引擎实际使用的初始止损（见 ④ 段账本 stop_trace 首点）">
                {fmtNumVal(sig?.stop_price ?? sig?.initial_stop_price, 4)}
              </span>
            </div>
          </div>
        </div>

        {/* 2. 委托记录 (Orders) */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3" data-testid="order-section-orders">
          <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
            <div className="flex items-center gap-1.5 font-bold text-neutral-100">
              <FileText className="w-4 h-4 text-indigo-400" />
              <span>② 委托记录 (Orders)</span>
            </div>
            <span className="text-[10px] text-neutral-400 font-mono">共 {orders.length} 笔订单关联</span>
          </div>

          <div className="space-y-2.5">
            {/* 开仓委托 */}
            <div className="p-2.5 rounded-lg border border-indigo-900/40 bg-indigo-950/10 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-indigo-300 text-[11px] inline-flex items-center gap-1">
                  <ArrowUpCircle className="w-3.5 h-3.5 text-emerald-400" />
                  开仓委托 (Entry Order)
                </span>
                {entryOrder?.ts_ms ? (
                  <button
                    type="button"
                    className="px-2 py-0.5 rounded bg-indigo-950 hover:bg-indigo-900 text-indigo-200 border border-indigo-700/60 text-[10px] font-mono transition-colors inline-flex items-center gap-1"
                    onClick={() => {
                      if (onLocateTime) onLocateTime(entryOrder.ts_ms, entryOrder.order_id, '定位开仓委托');
                    }}
                    title="在 K 线图上高亮定位开仓委托"
                    data-testid="locate-entry-order-btn"
                  >
                    <Crosshair className="w-3 h-3 text-indigo-300" />
                    定位开仓委托 K 线
                  </button>
                ) : null}
              </div>
              {entryOrder ? (
                <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                  <div><span className="text-neutral-500">订单 ID:</span> <span className="font-mono text-neutral-300">{entryOrder.order_id}</span></div>
                  <div><span className="text-neutral-500">委托时点:</span> {fmtUtcTime(entryOrder.ts_ms)}</div>
                  <div><span className="text-neutral-500">委托方向:</span>{' '}
                    {entryOrder.side ? (
                      <span className="font-sans text-neutral-200">
                        {String(entryOrder.side).toLowerCase() === 'buy' ? '买入开多' : '卖出开空'}
                      </span>
                    ) : (
                      <NoDataBadge note="账本订单行未记录 side" />
                    )}
                  </div>
                  <div><span className="text-neutral-500">委托价格:</span> {fmtNumVal(entryOrder.price, 4)}</div>
                  <div><span className="text-neutral-500">委托数量:</span> {fmtNumVal(entryOrder.qty, 4)}</div>
                  <div><span className="text-neutral-500">止损挂单:</span> {fmtNumVal(entryOrder.stop_price, 4)}</div>
                  <div className="col-span-2"><span className="text-neutral-500">委托原因:</span> <span className="font-mono text-neutral-300">{entryOrder.reason || NO_DATA_TEXT}</span></div>
                </div>
              ) : (
                <div className="text-neutral-500 text-[11px]">未找到独立开仓订单明细（以成交记录为准）</div>
              )}
            </div>

            {/* 平仓委托 */}
            <div className="p-2.5 rounded-lg border border-neutral-800 bg-neutral-950/40 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-rose-300 text-[11px] inline-flex items-center gap-1">
                  <ArrowDownCircle className="w-3.5 h-3.5 text-rose-400" />
                  平仓委托 (Exit Order)
                </span>
                {exitOrder?.ts_ms ? (
                  <button
                    type="button"
                    className="px-2 py-0.5 rounded bg-rose-950 hover:bg-rose-900 text-rose-200 border border-rose-700/60 text-[10px] font-mono transition-colors inline-flex items-center gap-1"
                    onClick={() => {
                      if (onLocateTime) onLocateTime(exitOrder.ts_ms, exitOrder.order_id, '定位平仓委托');
                    }}
                    title="在 K 线图上高亮定位平仓委托"
                    data-testid="locate-exit-order-btn"
                  >
                    <Crosshair className="w-3 h-3 text-rose-300" />
                    定位平仓委托 K 线
                  </button>
                ) : null}
              </div>
              {exitOrder ? (
                <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                  <div><span className="text-neutral-500">订单 ID:</span> <span className="font-mono text-neutral-300">{exitOrder.order_id}</span></div>
                  <div><span className="text-neutral-500">委托时点:</span> {fmtUtcTime(exitOrder.ts_ms)}</div>
                  <div><span className="text-neutral-500">委托方向:</span>{' '}
                    {exitOrder.side ? (
                      <span className="font-sans text-neutral-200">
                        {String(exitOrder.side).toLowerCase() === 'sell' ? '卖出平多' : '买入平空'}
                      </span>
                    ) : (
                      <NoDataBadge note="账本订单行未记录 side" />
                    )}
                  </div>
                  <div><span className="text-neutral-500">委托价格:</span> {fmtNumVal(exitOrder.price, 4)}</div>
                  <div><span className="text-neutral-500">委托数量:</span> {fmtNumVal(exitOrder.qty, 4)}</div>
                  <div><span className="text-neutral-500">触发止损:</span> {fmtNumVal(exitOrder.stop_price, 4)}</div>
                  <div className="col-span-2"><span className="text-neutral-500">退出原因:</span> <span className="font-mono text-neutral-300">{exitReasonLabel(exitOrder.reason || t.exit_reason)}</span></div>
                </div>
              ) : (
                <div className="text-neutral-500 text-[11px]">未找到独立平仓订单明细</div>
              )}
            </div>
          </div>
        </div>

        {/* 3. 成交回报 (Execution & Fills) */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3" data-testid="order-section-fills">
          <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
            <div className="flex items-center gap-1.5 font-bold text-neutral-100">
              <DollarSign className="w-4 h-4 text-emerald-400" />
              <span>③ 成交回报 (Execution)</span>
            </div>
            {/* 定位进场成交 */}
            {t.entry_time_ms ? (
              <button
                type="button"
                className="px-2 py-0.5 rounded bg-emerald-950 hover:bg-emerald-900 text-emerald-200 border border-emerald-700/60 text-[11px] font-mono transition-colors inline-flex items-center gap-1"
                onClick={() => {
                  if (onLocateTime) onLocateTime(t.entry_time_ms, t.order_id, '定位开仓成交时刻');
                }}
                title="在 K 线图上高亮定位开仓成交时刻"
                data-testid="locate-entry-fill-btn"
              >
                <Crosshair className="w-3 h-3 text-emerald-300" />
                定位开仓成交 K 线
              </button>
            ) : null}
          </div>

          <div className="space-y-3 text-[11px]">
            {/* 开仓成交 */}
            <div className="p-2.5 rounded-lg border border-neutral-800 bg-neutral-950/40 space-y-1.5">
              <div className="text-emerald-400 font-semibold text-[11px]">▲ 进场成交 (Entry Fill)</div>
              <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                <div><span className="text-neutral-500">成交时刻:</span> {fmtUtcTime(t.entry_time_ms)}</div>
                <div><span className="text-neutral-500">实际成交价:</span> {fmtNumVal(t.entry_price, 4)}</div>
                <div><span className="text-neutral-500">成交数量:</span> {fmtNumVal(t.qty, 4)}</div>
                <div>
                  <span className="text-neutral-500">仓位金额:</span>{' '}
                  {typeof t.entry_price === 'number' && typeof t.qty === 'number'
                    ? fmtMoney(t.entry_price * t.qty)
                    : <NoDataBadge />}
                </div>
                <div><span className="text-neutral-500">开仓手续费:</span> {fmtValue(t.entry_fee, (f) => `${f.toFixed(4)} USDT`)}</div>
                <div>
                  <span className="text-neutral-500">入场滑点:</span>{' '}
                  {priceAudit?.entry?.slip_delta != null ? (
                    <span className="font-mono text-neutral-300">{priceAudit.entry.slip_delta} (步长 {priceAudit.entry.price_step ?? NO_DATA_TEXT})</span>
                  ) : (
                    <NoDataBadge note="未记录单笔滑点明细" />
                  )}
                </div>
              </div>
            </div>

            {/* 平仓成交 */}
            <div className="p-2.5 rounded-lg border border-neutral-800 bg-neutral-950/40 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-rose-400 font-semibold text-[11px]">▼ 平仓出场 (Exit Fill)</span>
                {t.exit_time_ms ? (
                  <button
                    type="button"
                    className="px-2 py-0.5 rounded bg-rose-950/80 hover:bg-rose-900 text-rose-200 border border-rose-700/60 text-[10px] font-mono transition-colors inline-flex items-center gap-1"
                    onClick={() => {
                      if (onLocateTime) onLocateTime(t.exit_time_ms, t.exit_order_id, '定位平仓离场时刻');
                    }}
                    title="在 K 线图上高亮定位平仓离场时刻"
                    data-testid="locate-exit-fill-btn"
                  >
                    <Crosshair className="w-3 h-3 text-rose-300" />
                    定位平仓成交 K 线
                  </button>
                ) : null}
              </div>
              <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                <div><span className="text-neutral-500">出场时刻:</span> {fmtUtcTime(t.exit_time_ms)}</div>
                <div><span className="text-neutral-500">实际出场价:</span> {fmtNumVal(t.exit_price, 4)}</div>
                <div><span className="text-neutral-500">平仓数量:</span> {fmtNumVal(t.qty, 4)}</div>
                <div>
                  <span className="text-neutral-500">出场金额:</span>{' '}
                  {typeof t.exit_price === 'number' && typeof t.qty === 'number'
                    ? fmtMoney(t.exit_price * t.qty)
                    : <NoDataBadge />}
                </div>
                <div><span className="text-neutral-500">平仓手续费:</span> {fmtValue(t.exit_fee, (f) => `${f.toFixed(4)} USDT`)}</div>
                <div>
                  <span className="text-neutral-500">出场滑点:</span>{' '}
                  {priceAudit?.exit?.slip_delta != null ? (
                    <span className="font-mono text-neutral-300">{priceAudit.exit.slip_delta} (步长 {priceAudit.exit.price_step ?? NO_DATA_TEXT})</span>
                  ) : (
                    <NoDataBadge note="未记录单笔滑点明细" />
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 4. 持仓管理与保护止损轨迹 (Position & Stop Trace) */}
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3" data-testid="order-section-position">
          <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
            <div className="flex items-center gap-1.5 font-bold text-neutral-100">
              <Shield className="w-4 h-4 text-cyan-400" />
              <span>④ 持仓与止损轨迹 (Position)</span>
            </div>
            <span className="text-[10px] text-neutral-400 font-mono">
              抬升记录: {stopTrace.length} 点
            </span>
          </div>

          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] pb-2 border-b border-neutral-800/60">
            <div>
              <span className="text-neutral-500">持仓时长:</span>{' '}
              <span className="font-mono text-neutral-200">
                {t.holding_hours != null
                  ? `${t.holding_hours.toFixed(2)} 小时 (${(t.holding_hours / 24).toFixed(1)} 天)`
                  : <NoDataBadge />}
              </span>
            </div>
            <div>
              <span className="text-neutral-500">初始止损（账本 stop_trace 首点）:</span>{' '}
              {fmtNumVal(t.initial_stop, 4)}
            </div>
            <div>
              <span className="text-neutral-500">最大有利浮盈 (MFE):</span>{' '}
              {t.mfe != null ? (
                <span className="font-mono text-emerald-400">+{(t.mfe * 100).toFixed(2)}%</span>
              ) : (
                <NoDataBadge />
              )}
            </div>
            <div>
              <span className="text-neutral-500">最大不利浮亏 (MAE):</span>{' '}
              {t.mae != null ? (
                <span className="font-mono text-rose-400">-{(t.mae * 100).toFixed(2)}%</span>
              ) : (
                <NoDataBadge />
              )}
            </div>
          </div>

          {/* 止损抬升轨迹列表 */}
          <div className="space-y-1.5">
            <div className="text-[11px] font-semibold text-neutral-300">
              4h 最低价动态追踪止损轨迹（单调抬升，绝不下移）：
            </div>
            {stopTrace.length === 0 ? (
              <div className="text-neutral-500 text-[11px]">无动态止损变动记录</div>
            ) : (
              <div className="max-h-36 overflow-y-auto space-y-1 pr-1 font-mono text-[11px]">
                {stopTrace.map((p, idx) => {
                  const ptTime = p.t ?? p.ts;
                  return (
                    <div
                      key={`stop-${ptTime}-${idx}`}
                      className="flex items-center justify-between p-1.5 rounded bg-neutral-950/60 border border-neutral-800/60 hover:border-cyan-700/50 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-neutral-500 text-[10px]">#{idx + 1}</span>
                        <span>{fmtUtcTime(ptTime)}</span>
                        <span className="text-cyan-300 font-bold">
                          {p.stop != null ? p.stop.toFixed(4) : NO_DATA_TEXT}
                        </span>
                        <span className="text-neutral-500 text-[10px]">
                          ({p.reason === 'init' ? '初始' : p.reason === 'trail' ? '4h抬升' : p.reason || NO_DATA_TEXT})
                        </span>
                      </div>
                      {ptTime ? (
                        <button
                          type="button"
                          className="px-1.5 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 text-cyan-300 text-[10px] transition-colors"
                          onClick={() => {
                            if (onLocateTime) onLocateTime(ptTime, null, `定位止损抬升时刻 #${idx + 1}`);
                          }}
                          title="定位该时刻对应的 K 线"
                        >
                          定位 K 线
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── 资金费结算事件流水明细 (Funding Events) ── */}
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3" data-testid="order-section-funding">
        <div className="flex items-center justify-between border-b border-neutral-800/80 pb-2">
          <div className="flex items-center gap-1.5 font-bold text-neutral-100">
            <Clock className="w-4 h-4 text-indigo-400" />
            <span>⑤ 资金费结算流水 (Funding Events · {funding.length} 笔)</span>
          </div>
          {crossCheck && (
            <span
              className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                crossCheck.match
                  ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60'
                  : 'bg-rose-950/40 text-rose-300 border-rose-800/60'
              }`}
            >
              {crossCheck.match ? '✓ 资金费事件与账本 100% 对账一致' : '⚠ 资金费存在微小差异'}
            </span>
          )}
        </div>

        {funding.length === 0 ? (
          <div className="text-neutral-500 text-[11px] py-2">
            该订单持仓期间未产生资金费结算事件（或持仓未跨越结算周期）
          </div>
        ) : (
          <div className="overflow-x-auto max-h-44 overflow-y-auto">
            <table className="w-full text-left font-mono text-[11px]">
              <thead className="text-neutral-500 border-b border-neutral-800 sticky top-0 bg-neutral-900">
                <tr>
                  <th className="py-1 px-2">#</th>
                  <th className="py-1 px-2">结算时刻 (UTC)</th>
                  <th className="py-1 px-2 text-right">费率</th>
                  <th className="py-1 px-2 text-right">标记价格</th>
                  <th className="py-1 px-2 text-right">结算金额</th>
                  <th className="py-1 px-2 text-right" title="账本 cum_funding_fee：正数 = 累计已付成本；净返佣时可为负。保留符号，不取绝对值">
                    累计资金费
                  </th>
                  <th className="py-1 px-2 text-center">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800/60">
                {funding.map((f, idx) => (
                  <tr key={`${f.funding_time_ms ?? f.event_time}-${idx}`} className="hover:bg-neutral-800/30">
                    <td className="py-1 px-2 text-neutral-500">{idx + 1}</td>
                    <td className="py-1 px-2">{fmtUtcTime(f.funding_time_ms ?? f.event_time)}</td>
                    <td className="py-1 px-2 text-right">{fmtValue(f.rate, (r) => `${(r * 100).toFixed(4)}%`)}</td>
                    <td className="py-1 px-2 text-right">{fmtNumVal(f.mark, 4)}</td>
                    <td className="py-1 px-2 text-right">
                      {typeof f.amount === 'number' ? (
                        <span className={f.amount < 0 ? 'text-rose-400' : 'text-emerald-400'}>
                          {f.amount < 0 ? '-' : '+'}
                          {Math.abs(f.amount).toFixed(4)}
                        </span>
                      ) : (
                        <NoDataBadge />
                      )}
                    </td>
                    <td className="py-1 px-2 text-right text-indigo-300">
                      {/* 保留符号：取绝对值会把「净返还」显示成「成本」 */}
                      {fmtValue(f.cum_funding_fee, (c) => c.toFixed(4))}
                    </td>
                    <td className="py-1 px-2 text-center">
                      <button
                        type="button"
                        className="px-1.5 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 text-indigo-300 text-[10px] transition-colors"
                        onClick={() => {
                          const ms = f.funding_time_ms ?? f.event_time;
                          if (ms && onLocateTime) onLocateTime(ms, null, `定位资金费结算时刻 #${idx + 1}`);
                        }}
                        title="定位该结算时刻的 K 线"
                      >
                        定位
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
