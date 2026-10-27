/**
 * 回测复盘页数据层（契约：`bt_api_v1`，服务端 `框架/backend/backtest_api.py`）
 * ----------------------------------------------------------------------------
 * 与 `labBacktest.ts` 的分工：
 *   · `labBacktest.ts` 是**旧回测工作台**的调用层，类型里有若干与后端不一致的历史
 *     声明（如 `sharpe_*` 后端根本不返回）。为免牵连既有页面，本文件重新声明一套
 *     **只覆盖新复盘页所需字段**的类型，全部按真实响应核对过。
 *   · 未在此声明的字段一律视为「后端没给」，页面显示 `—`，不猜、不补默认值。
 *
 * 三条硬约束（来自真实数据核对，违反即会显示假数据）：
 *   1. 列表的 `created_at / started_at / finished_at` 是**读取时刻兜底**，576 条
 *      distinct = 1，不是运行时间 ⇒ 本文件**不导出**这三个字段，页面无从误用。
 *   2. 列表的 `window.win_start_ms / win_end_ms` 恒为 null ⇒ 真实窗口只能从
 *      `/chart` 的 `chart.window` 取，故列表卡片显示「运行时间」（run_id 后缀 epoch）。
 *   3. `net_return_pct` 单位是**百分数本身**（0.0635 表示 0.0635%），不是小数比例。
 */

import { apiGet } from './client';

/* ── 列表 ───────────────────────────────────────────────────────────── */

export interface BtRunWindow {
  win_start_ms: number | null;
  win_end_ms: number | null;
  symbol: string;
  exchange: string;
  market: string;
  signal_timeframe: string;
  exec_timeframe: string;
}

export interface BtRunListItem {
  run_id: string;
  status: string;
  strategy_id: string;
  strategy_key?: string;
  symbols: string[];
  window: BtRunWindow;
  net_return_pct: number | null;
  trades_count: number | null;
  max_drawdown_pct: number | null;
  failure_reason?: string | null;
  /** CODE-REVIEW-02：false = 无 run.json 的历史批量产物（未登记，只读视图） */
  registered?: boolean | null;
  /** 状态是按什么落盘证据推断的（如“manifest.json+summary.json 均在”） */
  status_basis?: string | null;
  record_note?: string | null;
}

export interface BtListEnvelope {
  items: BtRunListItem[];
  total: number;
  runs_root?: string | null;
  note?: string | null;
  quality?: { status?: string | null; note?: string | null } | null;
  missing_reason?: string | null;
}

/* ── 单 run 详情 ────────────────────────────────────────────────────── */

export interface BtActualConfig {
  initial_equity?: number | null;
  risk_budget_per_trade?: number | null;
  fee_rate?: number | null;
  slippage_bps?: number | null;
}

export interface BtRunDetail {
  run_id: string;
  status: string;
  strategy_id: string;
  config?: {
    spec?: {
      strategy_id?: string;
      symbols?: string[];
      exchange?: string;
      market?: string;
      signal_timeframe?: string;
      exec_timeframe?: string;
      initial_equity?: number | null;
    } | null;
    config?: { actual_config?: BtActualConfig | null } | null;
    actual_config?: BtActualConfig | null;
  } | null;
  engine?: {
    strategy_id?: string;
    signal_version?: string;
    core_version?: string;
    exec_core_version?: string;
  } | null;
  window?: BtRunWindow | null;
  quality?: { status?: string | null; note?: string | null } | null;
  missing_reason?: string | null;
  note?: string | null;
  /** CODE-REVIEW-02：未登记历史运行透传（false = 无 run.json，只读合成视图） */
  registered?: boolean | null;
  status_basis?: string | null;
  record_note?: string | null;
}

/* ── 指标（唯一可信来源：由账本现算） ─────────────────────────────────── */

export interface BtDrawdownDetail {
  peak_index: number;
  trough_index: number;
  peak_equity: number;
  trough_equity: number;
  points: number;
}

export interface BtMetrics {
  initial_equity: number | null;
  final_equity: number | null;
  net_pnl: number | null;
  /** 百分数本身（0.0635 = 0.0635%） */
  net_return_pct: number | null;
  gross_pnl: number | null;
  total_entry_fee: number | null;
  total_exit_fee: number | null;
  total_fees: number | null;
  total_funding_fee: number | null;
  trades_count: number | null;
  wins: number | null;
  losses: number | null;
  flats: number | null;
  win_rate_pct: number | null;
  win_rate_basis?: string | null;
  win_loss_basis?: string | null;
  /** 美元金额（不是百分比） */
  avg_win: number | null;
  avg_loss: number | null;
  avg_win_loss_ratio: number | null;
  avg_win_loss_ratio_basis?: string | null;
  profit_factor: number | null;
  profit_factor_basis?: string | null;
  expectancy_usd: number | null;
  max_drawdown_pct: number | null;
  max_drawdown_basis?: string | null;
  max_drawdown_detail: BtDrawdownDetail | null;
  equity_points: number | null;
  open_positions_remaining?: number | null;
  funding_events_total?: number | null;
  note?: string | null;
}

export interface BtBenchmarkMeta {
  benchmark_kind?: string;
  symbol?: string;
  entry_time_ms?: number | null;
  exit_time_ms?: number | null;
  gross_return_pct?: number | null;
  /** 纯价格口径（不含手续费 / 滑点 / 资金费） */
  price_only_return_pct?: number | null;
  /** 后端恒为 null —— 基准没有成本模型 */
  net_return_pct?: number | null;
  final_equity?: number | null;
  series_last_equity?: number | null;
}

export interface BtMetricsResponse {
  metrics: BtMetrics | null;
  metrics_from_ledger?: boolean | null;
  metrics_stored_summary?: Record<string, unknown> | null;
  metrics_notes?: string[] | null;
  benchmark_buy_hold?: BtBenchmarkMeta | null;
  monthly_returns?: { months?: BtMonthReturn[] | null } | null;
  quality?: { status?: string | null; note?: string | null } | null;
  missing_reason?: string | null;
}

export interface BtMonthReturn {
  month: string;
  month_end_equity?: number | null;
  basis_equity?: number | null;
  return_pct?: number | null;
  partial_month?: boolean;
  basis_note?: string | null;
}

/* ── 逐笔成交 ───────────────────────────────────────────────────────── */

export interface BtTradeRow {
  trade_id?: string | null;
  symbol?: string | null;
  /** 后端是**数字** 1 / -1，不是 'long' / 'short' */
  direction: number | string | null;
  signal_type?: number | string | null;
  entry_time?: number | null;
  exit_time?: number | null;
  entry_time_ms?: number | null;
  exit_time_ms?: number | null;
  entry_price?: number | null;
  exit_price?: number | null;
  qty?: number | null;
  gross_pnl?: number | null;
  net_pnl?: number | null;
  fees?: number | null;
  funding_fee?: number | null;
  exit_reason?: string | null;
  holding_hours?: number | null;
  mae?: number | null;
  mfe?: number | null;
  signal_id?: string | null;
  /* ── FT-REPLAY-01：复盘要看的四类时间与风险字段（缺就是 null，界面显示「未知」）── */
  /** 原信号 K 线收盘时间 */
  signal_time_ms?: number | null;
  signal_time_source?: string | null;
  /** 确认 K 线收盘时间（C0 无此环节 ⇒ null） */
  confirm_time_ms?: number | null;
  confirm_time_source?: string | null;
  /** 下单即成交时间（账本没记 ⇒ null + order_time='未记录'，绝不用成交时间顶替） */
  order_time_ms?: number | null;
  order_time?: string | null;
  order_time_source?: string | null;
  /** 原始 P0（箱体/扫单基准价）与入场时计划风险（USDT） */
  P0?: number | null;
  P0_source?: string | null;
  planned_risk_usdt?: number | null;
  planned_risk_source?: string | null;
  /** 实际仓位金额（stake）与数量 */
  stake_amount?: number | null;
  stake_amount_source?: string | null;
  /** 该笔属于哪个模式（C / C1 / C2）与上游事件 ID，供 C0/C1/C2 对照 */
  mode?: string | null;
  event_id?: string | null;
}

export interface BtTradesResponse {
  items: BtTradeRow[];
  total: number;
  trade_id_source?: string | null;
  link_notes?: string[] | null;
  missing_reason?: string | null;
}

/* ── 图表载荷 ───────────────────────────────────────────────────────── */

/** K 线是**数组元组**：`[ts_ms, open, high, low, close, volume]`（不是对象）。 */
export type BtCandleTuple = [number, number, number, number, number, number];

export interface BtChartSignal {
  t: number;
  kind?: string;
  direction?: string;
  signal_id?: string;
  stop_price?: number | null;
  atr?: number | null;
  oi_score?: number | null;
  oi_available?: boolean | null;
  oi_quality?: string | null;
}

export interface BtChartTrade {
  trade_id?: string;
  symbol?: string;
  direction?: string;
  entry?: { t: number; price: number; qty?: number } | null;
  exit?: { t: number; price: number; reason?: string } | null;
  net_pnl?: number | null;
}

export interface BtEquityPoint {
  t: number;
  equity: number;
  cash?: number | null;
  unrealized?: number | null;
}

export interface BtChartPayload {
  symbol: string;
  exec_timeframe: string;
  window: { start_ms: number | null; end_ms: number | null };
  candles: BtCandleTuple[];
  signals: BtChartSignal[];
  trades: BtChartTrade[];
  equity_curve: BtEquityPoint[];
  funding_events?: unknown[];
  benchmark?: {
    symbol?: string;
    return_pct?: number | null;
    equity_series?: { t: number; equity: number }[];
  } | null;
}

export interface BtChartResponse {
  chart: BtChartPayload | null;
  quality?: { status?: string | null; note?: string | null } | null;
  missing_reason?: string | null;
}

/* ── 拒单 / 订单（未成交 run 的日志来源） ────────────────────────────── */

export interface BtRejectedRow {
  symbol?: string | null;
  ts_ms?: number | null;
  phase?: string | null;
  reason?: string | null;
  [k: string]: unknown;
}

export interface BtRejectedResponse {
  items: BtRejectedRow[];
  missing_reason?: string | null;
}

export interface BtOrderRow {
  order_id?: string | null;
  symbol?: string | null;
  side?: string | null;
  status?: string | null;
  qty?: number | null;
  price?: number | null;
  ts_ms?: number | null;
  created_ms?: number | null;
  [k: string]: unknown;
}

export interface BtOrdersResponse {
  items: BtOrderRow[];
  unlinked_orders?: number | null;
  missing_reason?: string | null;
}

/* ── 取数 ───────────────────────────────────────────────────────────── */

/* ── 批次（`/batches`，2026-10-18 BT-UI-01 新增） ─────────────────────── */

/**
 * 批次 = **一次批量运行**的真实目录（`回测日志/event_trend_v0_1/runs_sep_all_latest` 等）。
 *
 * 为什么必须单独建模：单 run 视图里「列表 / 汇总 / 逐笔 / 图表」容易各自取数，
 * 一旦混入旧快照（如 `public/all_trades.json`）就变成跨批次拼数据。
 * 批次是唯一能把四者锚在同一次运行上的标识。
 *
 * `summary_loaded=false` 时成交/净盈亏等字段为 `null` —— 轻量列表只数目录，
 * 汇总由 `/batches/{id}/summary` 懒算，**不拿 null 当 0**。
 */
export interface BtBatch {
  batch_id: string;
  path: string;
  generated_at_ms: number | null;
  runs_total: number;
  runs_with_summary: number | null;
  runs_missing_summary: number | null;
  runs_with_trades: number | null;
  runs_win: number | null;
  runs_loss: number | null;
  trades_total: number | null;
  net_pnl_sum: number | null;
  net_pnl_runs_counted: number | null;
  fee_total_sum: number | null;
  funding_total_sum: number | null;
  window_start_ms: number | null;
  window_end_ms: number | null;
  window_source: string | null;
  symbols_count: number | null;
  formal_pass_allowed_any: boolean | null;
  funnel_sum: Record<string, number> | null;
  derived_from: string | null;
  is_default_root: boolean;
  summary_loaded: boolean;
  note?: string | null;
  /** BACKFILL-01：清单型批次（不落 run 目录，数字来自 batch_meta.json） */
  manifest_batch?: boolean | null;
  title?: string | null;
  groups?: number | null;
  source?: Record<string, string> | null;
}

export interface BtBatchListResponse {
  items: BtBatch[];
  total: number;
  batch_root: string;
  batch_root_exists: boolean;
  derived_from?: string | null;
  note?: string | null;
}

export interface BtBatchRun {
  run_id: string;
  batch_id: string;
  symbol: string | null;
  status: string | null;
  trades_count: number | null;
  net_pnl: number | null;
  net_return_pct: number | null;
  has_trades: boolean;
  has_chart: boolean;
  /** CODE-REVIEW-02：false = 无 run.json 的历史批量产物（未登记） */
  registered?: boolean | null;
  /** FT-REPLAY-01：true = 研究期重建（上游落盘 CSV 重建，不是本引擎成绩） */
  research_period?: boolean | null;
  status_basis?: string | null;
  record_note?: string | null;
}

export interface BtBatchRunsResponse {
  batch_id: string;
  items: BtBatchRun[];
  total: number;
  derived_from?: string | null;
  note?: string | null;
}

/** 批次逐笔：一行一笔真实成交，字段全部来自账本（`trades.jsonl`）+ 图载荷交叉核对。 */
export interface BtBatchTrade {
  batch_id: string;
  run_id: string;
  trade_id: string | null;
  trade_id_source?: string | null;
  symbol: string | null;
  direction: number | string | null;
  signal_id: string | null;
  signal_time_ms: number | null;
  signal_time_source?: string | null;
  entry_time_ms: number | null;
  entry_price: number | null;
  initial_stop: number | null;
  initial_stop_reason?: string | null;
  exit_time_ms: number | null;
  exit_price: number | null;
  exit_reason: string | null;
  qty: number | null;
  gross_pnl: number | null;
  fees: number | null;
  funding_fee: number | null;
  net_pnl: number | null;
  mae: number | null;
  mfe: number | null;
  holding_hours: number | null;
  stop_trace_points?: number | null;
}

export interface BtBatchTradesResponse {
  batch_id: string;
  items: BtBatchTrade[];
  total: number;
  offset?: number;
  limit?: number;
  runs_with_trades?: number;
  missing_initial_stop?: number;
  unlinked_signal_time?: number;
  derived_from?: string | null;
  note?: string | null;
  missing_reason?: string | null;
}

export interface BtBatchSummaryResponse {
  batch_id: string;
  batch: BtBatch;
  trades: {
    count: number;
    wins: number;
    losses: number;
    flats: number;
    win_rate_pct: number | null;
    net_pnl_sum: number | null;
    fees_sum: number | null;
    funding_sum: number | null;
  };
  /** 汇总与逐笔行数的自洽核对（`true` 才算对得上） */
  checks: {
    trades_total_matches_rows: boolean;
    net_pnl_matches_rows: boolean | null;
    summary_trades_total: number | null;
    trades_rows: number;
    net_pnl_from_summary: number | null;
    net_pnl_from_rows: number | null;
  };
  derived_from?: string | null;
  missing_reason?: string | null;
}

/* ── 取数 ───────────────────────────────────────────────────────────── */

export function fetchBatches(signal?: AbortSignal): Promise<BtBatchListResponse> {
  return apiGet<BtBatchListResponse>('/backtests/batches', signal);
}

export function fetchBatchSummary(batchId: string, signal?: AbortSignal): Promise<BtBatchSummaryResponse> {
  return apiGet<BtBatchSummaryResponse>(`/backtests/batches/${encodeURIComponent(batchId)}/summary`, signal);
}

export function fetchBatchRuns(batchId: string, signal?: AbortSignal): Promise<BtBatchRunsResponse> {
  return apiGet<BtBatchRunsResponse>(`/backtests/batches/${encodeURIComponent(batchId)}/runs`, signal);
}

export function fetchBatchTrades(
  batchId: string,
  limit = 2000,
  signal?: AbortSignal,
): Promise<BtBatchTradesResponse> {
  return apiGet<BtBatchTradesResponse>(
    `/backtests/batches/${encodeURIComponent(batchId)}/trades?limit=${limit}`,
    signal,
  );
}

export function fetchRunList(limit = 1000, signal?: AbortSignal): Promise<BtListEnvelope> {
  return apiGet<BtListEnvelope>(`/backtests?limit=${limit}`, signal);
}

export function fetchRunDetail(runId: string, signal?: AbortSignal): Promise<BtRunDetail> {
  return apiGet<BtRunDetail>(`/backtests/${encodeURIComponent(runId)}`, signal);
}

export function fetchMetrics(runId: string, signal?: AbortSignal): Promise<BtMetricsResponse> {
  return apiGet<BtMetricsResponse>(`/backtests/${encodeURIComponent(runId)}/metrics`, signal);
}

export function fetchChart(runId: string, signal?: AbortSignal): Promise<BtChartResponse> {
  return apiGet<BtChartResponse>(`/backtests/${encodeURIComponent(runId)}/chart`, signal);
}

export function fetchTrades(runId: string, signal?: AbortSignal): Promise<BtTradesResponse> {
  return apiGet<BtTradesResponse>(`/backtests/${encodeURIComponent(runId)}/trades`, signal);
}

export function fetchRejected(runId: string, signal?: AbortSignal): Promise<BtRejectedResponse> {
  return apiGet<BtRejectedResponse>(`/backtests/${encodeURIComponent(runId)}/rejected`, signal);
}

export function fetchOrders(runId: string, signal?: AbortSignal): Promise<BtOrdersResponse> {
  return apiGet<BtOrdersResponse>(`/backtests/${encodeURIComponent(runId)}/orders`, signal);
}

export interface BtBundle {
  detail: BtRunDetail | null;
  metrics: BtMetricsResponse | null;
  chart: BtChartResponse | null;
  trades: BtTradesResponse | null;
  orders: BtOrdersResponse | null;
  rejected: BtRejectedResponse | null;
  /** 每个子请求的失败原因（成功为空对象）；部分失败时页面仍能显示其余部分 */
  errors: Record<string, string>;
}

/**
 * 一次取齐一个 run 的全部展示数据。
 *
 * 用 `allSettled` 而不是 `all`：chart 有 2.7MB、equity 曲线 1.7 万点，
 * 任一子请求失败不应让整页变空 —— 缺哪块就在那块显示「取数失败：原因」。
 */
export async function fetchRunBundle(runId: string, signal?: AbortSignal): Promise<BtBundle> {
  const [detail, metrics, chart, trades, orders, rejected] = await Promise.allSettled([
    fetchRunDetail(runId, signal),
    fetchMetrics(runId, signal),
    fetchChart(runId, signal),
    fetchTrades(runId, signal),
    fetchOrders(runId, signal),
    fetchRejected(runId, signal),
  ]);

  const errors: Record<string, string> = {};
  const pick = <T,>(r: PromiseSettledResult<T>, key: string): T | null => {
    if (r.status === 'fulfilled') return r.value;
    const err = r.reason as { name?: string; message?: string };
    if (err?.name === 'AbortError') return null;
    errors[key] = err?.message || '取数失败';
    return null;
  };

  return {
    detail: pick(detail, 'detail'),
    metrics: pick(metrics, 'metrics'),
    chart: pick(chart, 'chart'),
    trades: pick(trades, 'trades'),
    orders: pick(orders, 'orders'),
    rejected: pick(rejected, 'rejected'),
    errors,
  };
}
