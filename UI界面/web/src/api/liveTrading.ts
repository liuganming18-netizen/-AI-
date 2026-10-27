/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 信号与持仓接通（2026-10-13）· 纸面账本 / 实盘只读 取数与下单。
 *
 * 纪律：
 *  · **纸面成交只经服务端账本**（`POST /api/v1/paper/orders`）。浏览器**不得**造仓；
 *    持仓一律来自服务端回执。
 *  · **实盘账户只读**：仅 `fetch_balance` / `fetch_positions`；不提交订单、不改权限、
 *    不返回密钥。
 *  · 后端未给的一律 `null`，前端不补 0、不推断。
 */

import { ApiError, apiGet, apiPost, unwrapItems } from './client';
import type {
  Envelope,
  EnvelopeQuality,
  RawLiveAccount,
  RawPaperAccount,
  RawPaperPending,
  RawPaperPositionFlat,
} from './types';

// ── 视图模型 ─────────────────────────────────────────────────────────────────

/**
 * 未来待成交挂单（服务端登记）。**因果纪律**：应成交时刻未到 ⇒ 挂单等真实进程时钟；
 * 错过成交时刻 ⇒ 服务端拒单，**不用旧价回填**。前端只展示，不推断、不造仓。
 */
export interface PaperPendingView {
  arm?: string | null;
  direction?: string | null;
  pendingId: number;
  symbol: string;
  origin: string | null;
  signalUid: string | null;
  eventTime: number | null;
  availableAt: number | null;
  requestedAt: number;
  earliestFillMs: number;
  createdAt: number;
  status: string;
  reason: string | null;
}

export interface PaperPositionView {
  /** 稳定键：`{arm}:{position_id}`（服务端账本唯一） */
  key: string;
  arm: string;
  accountId: string;
  positionId: number;
  /** 交易所原始 market_id（如 AAVEUSDT） */
  symbol: string;
  direction: 'long' | 'short';
  qty: number;
  entryPrice: number;
  markPrice: number | null;
  markSource: string | null;
  unrealizedPnl: number | null;
  unrealizedPnlPct: number | null;
  stopPrice: number | null;
  atrAtEntry: number | null;
  leverage: number;
  notional: number;
  entryTs: number;
  signalUid: string | null;
  openedBy: string | null;
  // ── 旧版/未验收/规则违规 追加审计标注（后端只读追加，原账本未改）──────────────
  /** 追加审计结论（如 `legacy_unverified`）；后端未给 ⇒ null。 */
  verificationStatus: string | null;
  /** 展示标签（如「旧版/未验收/规则违规」）；后端未给 ⇒ null。 */
  label: string | null;
  /** 命中的违规项列表；空数组 = 无追加违规记录（不等于「已验收通过」）。 */
  violations: string[];
  /** 是否已排除在正式策略表现之外。 */
  excludedFromOfficial: boolean;
}

export interface PaperAccountView {
  arm: string;
  accountId: string;
  initialEquity: number;
  cash: number;
  equity: number | null;
  unrealizedPnl: number;
  usedMargin: number;
  grossNotional: number;
  realizedPnlTotal: number;
  feesPaidTotal: number;
  fundingPaidTotal: number;
  drawdown: number;
  drawdownLocked: boolean;
  nPositions: number;
  nOrders: number;
  nRejected: number;
  nClosed: number;
  modelVersion: string;
  paramsSource: string | null;
  positions: PaperPositionView[];
  /** 未来待成交挂单（服务端登记） */
  pending: PaperPendingView[];
  nPending: number;
}

/** 挂单原始行 → 视图（逐项容错；缺失即 null/0，不推断）。 */
function mapPaperPending(raw: RawPaperPending, arm: string): PaperPendingView {
  return {
    arm,
    direction: raw.direction ?? null,
    pendingId: num(raw.pending_id) ?? 0,
    symbol: raw.symbol,
    origin: raw.origin ?? null,
    signalUid: raw.signal_uid ?? null,
    eventTime: num(raw.event_time),
    availableAt: num(raw.available_at),
    requestedAt: num(raw.requested_at) ?? 0,
    earliestFillMs: num(raw.earliest_fill_ms) ?? 0,
    createdAt: num(raw.created_at) ?? 0,
    status: raw.status,
    reason: raw.reason ?? null,
  };
}

export interface LivePositionView {
  symbol: string | null;
  side: string | null;
  contracts: number | null;
  notionalUsd: number | null;
  entryPrice: number | null;
  markPrice: number | null;
  unrealizedPnl: number | null;
  leverage: number | null;
  raw: Record<string, unknown>;
}

export interface LiveAccountView {
  exchange: string;
  accountLabel: string;
  /**
   * `connected`（已连接、有持仓）/ `connected_no_positions`（已连接、当前无持仓）/
   * `not_configured`（未配置凭据）/ `auth_failed` / `error` / `reader_unavailable` / `stale`。
   */
  status: string;
  rawStatus: string;
  detail: string | null;
  errorClass: string | null;
  equityUsd: number | null;
  availableUsd: number | null;
  usedMarginUsd: number | null;
  positions: LivePositionView[];
  nPositions: number;
  readAt: string | null;
  ageSeconds: number | null;
}

export interface PaperLedger {
  accounts: PaperAccountView[];
  /** 所有臂的未平仓持仓（扁平） */
  positions: PaperPositionView[];
  /** 所有臂的未来待成交挂单（扁平，服务端登记） */
  pending: PaperPendingView[];
  /** 账本不可用时的真实原因（如 live worker 未跑）；可用时为 null */
  missingReason: string | null;
  qualityNote: string | null;
  /** 服务端**新开仓总闸**（fail-closed）；null = 未返回。 */
  newEntryGate: {
    allowNewEntries: boolean;
    reason: string | null;
    exitsFeesFundingHealthUnaffected: boolean;
  } | null;
  /** 旧版/未验收账本标记（后端只读追加）。 */
  legacyLedger: {
    preserved: boolean;
    verificationStatus: string | null;
    label: string | null;
    excludedFromOfficial: boolean;
  } | null;
  /** 实时起点水位线（UTC）；解释「为何自动持仓可能为空」。 */
  liveStartUtc: string | null;
  /** 水位线之前「仅展示」信号数 / 之后可自动开仓信号数（后端真实计数）。 */
  nPreWatermarkDisplayOnly: number | null;
  nAfterWatermark: number | null;
  // ── WORK-04 退役：Paper 未接新共享核心 ⇒ `status='not_connected'`（fail-closed）──
  /**
   * 服务端 `quality.status`（`ok` / `not_connected` / `unavailable` / `retired`）。
   * `not_connected` ⇒ 该端点未接新核心，**禁止新开仓 / 真实下单**。
   */
  status: string | null;
  /** 新共享核心策略 id（后端 `quality.new_core`）。 */
  newCore: string | null;
  /** 新共享核心是否已接线到 Paper（后端 `quality.new_core_wired`）。 */
  newCoreWired: boolean | null;
  /** 已退役旧策略注册表键（后端 `quality.retired_strategy_keys`）。 */
  retiredStrategyKeys: string[];
  /** Paper / Live 接线状态块（后端 `quality.paper_live`）；缺失即 null。 */
  paperLive: EnvelopeQuality['paper_live'];
}

export interface LiveAccountsResult {
  accounts: LiveAccountView[];
  missingReason: string | null;
  qualityNote: string | null;
}

// ── 数值容错（后端 null / 字符串数字均可）────────────────────────────────────
const num = (v: unknown): number | null => {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

function mapPaperPosition(
  row: RawPaperPositionFlat | (RawPaperAccount['positions'][number] & { arm?: string; account_id?: string }),
  arm: string,
  accountId: string,
): PaperPositionView {
  return {
    key: `${arm}:${row.position_id}`,
    arm,
    accountId,
    positionId: row.position_id,
    symbol: row.symbol,
    direction: row.direction === 'short' ? 'short' : 'long',
    qty: num(row.qty) ?? 0,
    entryPrice: num(row.entry_price) ?? 0,
    markPrice: num(row.mark_price),
    markSource: row.mark_source ?? null,
    unrealizedPnl: num(row.unrealized_pnl),
    unrealizedPnlPct: num(row.unrealized_pnl_pct),
    stopPrice: num(row.stop_price),
    atrAtEntry: num(row.atr_at_entry),
    leverage: num(row.leverage) ?? 1,
    notional: num(row.notional) ?? 0,
    entryTs: num(row.entry_ts) ?? 0,
    signalUid: row.signal_uid ?? null,
    openedBy: row.opened_by ?? null,
    verificationStatus: row.verification_status ?? null,
    label: row.label ?? null,
    violations: Array.isArray(row.violations) ? row.violations.filter((v): v is string => typeof v === 'string') : [],
    excludedFromOfficial: row.excluded_from_official_strategy_performance === true,
  };
}

function mapPaperAccount(raw: RawPaperAccount): PaperAccountView {
  const arm = raw.arm;
  const accountId = raw.account_id;
  const positions = Array.isArray(raw.positions)
    ? raw.positions.map((p) => mapPaperPosition(p, arm, accountId))
    : [];
  const pending = Array.isArray(raw.pending) ? raw.pending.map((p) => mapPaperPending(p, arm)) : [];
  return {
    arm,
    accountId,
    initialEquity: num(raw.initial_equity) ?? 0,
    cash: num(raw.cash) ?? 0,
    equity: num(raw.equity),
    unrealizedPnl: num(raw.unrealized_pnl) ?? 0,
    usedMargin: num(raw.used_margin) ?? 0,
    grossNotional: num(raw.gross_notional) ?? 0,
    realizedPnlTotal: num(raw.realized_pnl_total) ?? 0,
    feesPaidTotal: num(raw.fees_paid_total) ?? 0,
    fundingPaidTotal: num(raw.funding_paid_total) ?? 0,
    drawdown: num(raw.drawdown) ?? 0,
    drawdownLocked: raw.drawdown_locked === true,
    nPositions: positions.length,
    nOrders: num(raw.n_orders) ?? 0,
    nRejected: num(raw.n_rejected) ?? 0,
    nClosed: num(raw.n_closed) ?? 0,
    modelVersion: raw.model_version ?? '—',
    paramsSource: raw.params_source ?? null,
    positions,
    pending,
    nPending: num(raw.n_pending) ?? pending.length,
  };
}

/**
 * 交易所持仓行 → 视图。**字段名按 ccxt 统一口径**（symbol/side/contracts/entryPrice/
 * markPrice/unrealizedPnl/leverage/notional），逐项容错；缺失即 null，不推断。
 */
function mapLivePosition(raw: Record<string, unknown>): LivePositionView {
  return {
    symbol: typeof raw.symbol === 'string' ? raw.symbol : null,
    side: typeof raw.side === 'string' ? raw.side : null,
    contracts: num(raw.contracts),
    notionalUsd: num(raw.notional),
    entryPrice: num(raw.entryPrice),
    markPrice: num(raw.markPrice),
    unrealizedPnl: num(raw.unrealizedPnl),
    leverage: num(raw.leverage),
    raw,
  };
}

function mapLiveAccount(raw: RawLiveAccount): LiveAccountView {
  const positions = Array.isArray(raw.positions) ? raw.positions.map(mapLivePosition) : [];
  return {
    exchange: raw.exchange,
    accountLabel: raw.account_label,
    status: raw.status,
    rawStatus: raw.raw_status,
    detail: raw.detail ?? null,
    errorClass: raw.error_class ?? null,
    equityUsd: num(raw.equity_usd),
    availableUsd: num(raw.available_usd),
    usedMarginUsd: num(raw.used_margin_usd),
    positions,
    nPositions: num(raw.n_positions) ?? positions.length,
    readAt: raw.read_at ?? null,
    ageSeconds: num(raw.age_seconds),
  };
}

// ── 取数 ─────────────────────────────────────────────────────────────────────

/** `GET /paper/accounts` → 四臂服务端账本。 */
export async function fetchPaperLedger(signal?: AbortSignal): Promise<PaperLedger> {
  const env = await apiGet<Envelope<RawPaperAccount>>('/paper/accounts', signal);
  const accounts = unwrapItems(env).map(mapPaperAccount);
  const positions = accounts.flatMap((a) => a.positions);
  const pending = accounts.flatMap((a) => a.pending);
  const q = env.quality ?? null;
  const gate = q?.new_entry_gate ?? null;
  const legacy = q?.legacy_ledger ?? null;
  return {
    accounts,
    positions,
    pending,
    missingReason: env.missing_reason ?? null,
    qualityNote: q?.note ?? null,
    newEntryGate: gate
      ? {
          allowNewEntries: gate.allow_new_entries === true,
          reason: gate.reason ?? gate.gate_reason ?? null,
          exitsFeesFundingHealthUnaffected: gate.exits_fees_funding_health_unaffected === true,
        }
      : null,
    legacyLedger: legacy
      ? {
          preserved: legacy.preserved === true,
          verificationStatus: legacy.verification_status ?? null,
          label: legacy.label ?? null,
          excludedFromOfficial: legacy.excluded_from_official_strategy_performance === true,
        }
      : null,
    liveStartUtc: q?.live_start_utc ?? null,
    nPreWatermarkDisplayOnly: num(q?.n_pre_watermark_display_only),
    nAfterWatermark: num(q?.n_after_watermark),
    status: typeof q?.status === 'string' ? q.status : null,
    newCore: typeof q?.new_core === 'string' ? q.new_core : null,
    newCoreWired: typeof q?.new_core_wired === 'boolean' ? q.new_core_wired : null,
    retiredStrategyKeys: Array.isArray(q?.retired_strategy_keys)
      ? q.retired_strategy_keys.filter((k): k is string => typeof k === 'string')
      : [],
    paperLive: q?.paper_live ?? null,
  };
}

/** `GET /accounts` → 实盘**只读**账户快照。 */
export async function fetchLiveAccounts(signal?: AbortSignal): Promise<LiveAccountsResult> {
  const env = await apiGet<Envelope<RawLiveAccount>>('/accounts', signal);
  return {
    accounts: unwrapItems(env).map(mapLiveAccount),
    missingReason: env.missing_reason ?? null,
    qualityNote: env.quality?.note ?? null,
  };
}

export interface PaperOpenInput {
  arm: string;
  symbol: string;
  direction?: 'long' | 'short';
  /** 信号 ATR（模拟止损基数）。缺失时后端明确拒绝 `missing_atr_and_stop`。 */
  atr?: number | null;
  stopPrice?: number | null;
  signalUid?: string | null;
}

export interface PaperOrderResult {
  ok: boolean;
  reason: string;
  positionId?: number;
  orderId?: number;
  qty?: number;
  entryPrice?: number;
  rawPrice?: number;
  stopPrice?: number;
  notional?: number;
  fee?: number;
  signalUid?: string | null;
  /** 未来待成交：已登记挂单（应成交时刻未到），由真实进程时钟推进 */
  pending?: boolean;
  pendingId?: number;
  earliestFillMs?: number;
  /** 因果审计：请求时刻 / 应成交时刻下界 */
  requestedAt?: number;
  marketSource?: string | null;
}

/**
 * `POST /paper/orders` action=open → **服务端模拟开仓**。
 *
 * 服务端成交时钟已优化为 0m 即时撮合，支持多空双向开仓。
 */
export async function postPaperOpen(input: PaperOpenInput, signal?: AbortSignal): Promise<PaperOrderResult> {
  const env = await apiPost<Envelope<Record<string, unknown>>>(
    '/paper/orders',
    {
      arm: input.arm,
      action: 'open',
      symbol: input.symbol,
      direction: input.direction || 'long',
      atr: input.atr ?? null,
      stop_price: input.stopPrice ?? null,
      signal_uid: input.signalUid ?? null,
    },
    signal,
  );
  const row = unwrapItems(env)[0] ?? {};
  const reason = typeof row.reason === 'string' ? row.reason : 'unknown';
  return {
    ok: row.ok === true,
    reason,
    positionId: num(row.position_id) ?? undefined,
    orderId: num(row.order_id) ?? undefined,
    qty: num(row.qty) ?? undefined,
    entryPrice: num(row.entry_price) ?? undefined,
    rawPrice: num(row.raw_price) ?? undefined,
    stopPrice: num(row.stop_price) ?? undefined,
    notional: num(row.notional) ?? undefined,
    fee: num(row.fee) ?? undefined,
    signalUid: typeof row.signal_uid === 'string' ? row.signal_uid : null,
    pending: reason === 'pending_future_fill',
    pendingId: num(row.pending_id) ?? undefined,
    earliestFillMs: num(row.earliest_fill_ms) ?? undefined,
    requestedAt: num(row.requested_at) ?? undefined,
    marketSource: typeof row.market_source === 'string' ? row.market_source : null,
  };
}

/**
 * `POST /paper/orders` action=close → **服务端模拟平仓**（按最近一根已闭合 15m 收盘）。
 */
export async function postPaperClose(
  arm: string,
  positionId: number,
  reason = 'manual',
  signal?: AbortSignal,
): Promise<PaperOrderResult> {
  const env = await apiPost<Envelope<Record<string, unknown>>>(
    '/paper/orders',
    { arm, action: 'close', position_id: positionId, reason },
    signal,
  );
  const row = unwrapItems(env)[0] ?? {};
  return {
    ok: row.ok === true,
    reason: typeof row.reason === 'string' ? row.reason : 'unknown',
    positionId: num(row.position_id) ?? positionId,
  };
}

/** 把 `ApiError` 转成给用户看的单行文案。 */
export function describeOrderError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return `服务端拒绝：${err.message}`;
    if (err.status === 400) return `参数不合法：${err.message}`;
    if (err.status === 0) return '后端不可达（/api/v1）';
    return err.message;
  }
  return err instanceof Error ? err.message : '未知错误';
}

/**
 * `POST /paper/toggle_gate` 切换或设定实盘模拟运行/暂停（允许新开仓/暂停新开仓）。
 */
export async function postPaperToggleGate(
  action: 'toggle' | 'start' | 'pause' = 'toggle',
  allowNewEntries?: boolean,
  signal?: AbortSignal,
): Promise<{ allowNewEntries: boolean; reason: string | null; note?: string }> {
  const env = await apiPost<Envelope<{ allow_new_entries: boolean; reason: string | null }>>(
    '/paper/toggle_gate',
    allowNewEntries !== undefined ? { allow_new_entries: allowNewEntries } : { action },
    signal,
  );
  const item = unwrapItems(env)[0];
  return {
    allowNewEntries: item?.allow_new_entries ?? false,
    reason: item?.reason ?? null,
    note: env.quality?.note ?? undefined,
  };
}

/**
 * `POST /signals/clear` 清理实时信号库。
 */
export async function postClearSignals(
  scope: 'all' | 'expired' | 'current' = 'all',
  signal?: AbortSignal,
): Promise<{ ok: boolean; clearedCount: number; note?: string }> {
  const env = await apiPost<Envelope<{ cleared_count: number; scope: string }>>(
    '/signals/clear',
    { scope },
    signal,
  );
  const item = unwrapItems(env)[0];
  return {
    ok: env.quality?.status === 'ok',
    clearedCount: item?.cleared_count ?? 0,
    note: env.quality?.note ?? undefined,
  };
}

/**
 * 执行守护与信号引擎状态视图
 */
export interface ExecutionDaemonView {
  running: boolean;
  status: string;
  pid: number | null;
  cycleCount: number | null;
  lastStepUtc: string | null;
  blockedReason: string | null;
  overallHealth: string;
  signalEngineStatus: string | null;
  marketDataStatus: string | null;
  /** 实际接入信号引擎的标的数（全池口径） */
  universe: number | null;
  /** 各形态阶段各多少标的（IDLE / BOX / SWEEP / RETEST / BREAKOUT） */
  stateCounter: Record<string, number> | null;
  /** 预热统计（ready / skipped / elapsed_sec），用于区分「还在预热」与「已就绪」 */
  bootstrap: Record<string, unknown> | null;
  /** 预热回放期间产出但按纪律丢弃的候选数（证明引擎在真实历史上确实会出信号） */
  replayCandidates: number | null;
  /**
   * 被丢弃的历史候选**逐条明细**（SIGNAL-UI-LINK-07）。
   *
   * 为什么必须有：`replayCandidates` 只有一个数字。用户看到「历史回放丢弃 51 个」，
   * 却查不到任何一个候选的 ID / 时间 / 条件 / 不可执行原因 ⇒ 这个数字无法核对。
   * 现在后端 `bootstrap.discarded_detail` 与计数同源产出，这里原样透传。
   */
  discardedCandidates: DiscardedCandidate[];
  /** 预热回放里「仍在成交时效内」被恢复的候选数（与丢弃数之和 = 回放候选总数） */
  recoveredCandidates: number | null;
  /** 最近一次产生候选的 UTC（null = 本次进程启动后没有候选） */
  lastCandidateUtc: string | null;
  /** 漏斗（since_start = 本进程；history = 预热回放），用于解释「信号死在哪一关」 */
  funnel: Record<string, unknown> | null;
  /** IDLE 逐原因细分（冷却 / 等箱体 / 条件已满足等下一根） */
  idleBreakdown: Record<string, unknown> | null;
  /** 引擎实际处理到的最后已收盘 K 线（open_time 口径） */
  watermark: { h1: number | null; h4: number | null; h1Utc: string | null; h4Utc: string | null } | null;
  /** 行情最新已收盘 K 线（用于与 watermark 对比，判断是否滞后） */
  marketLatest: { close15mUtc: string | null; close1hUtc: string | null; close4hUtc: string | null } | null;
  /**
   * ATR 口径诊断（SIGNAL-HISTORY-FIX-02）。
   *
   * 冻结规则第 17 行要求「4h Wilder ATR(14)，连续递推，至少预热 200 根完整 4h」。
   * 修复前实时路径每根只取最近 42 根重算（递推被每根重置、且无 200 根预热）。
   * 这里把「当前到底用哪个口径、预热到第几根」暴露出来，让用户入口能核对，
   * 而不是只能读代码相信。
   */
  atrCaliber: Record<string, unknown> | null;
  /**
   * 水位一致性（SIGNAL-HISTORY-FIX-03）：库内最新已闭合 bar 与「引擎实际处理过的最大 bar」
   * 是否同一根。`lag_1h_bars>1` 就是漏处理；逐币分布防止「1 个标的落后」被全池 max 掩盖。
   */
  watermarkCheck: Record<string, unknown> | null;
  /**
   * 数据质量闸门（SIGNAL-HISTORY-FIX-03）：哪些标的因交易所占位行（零成交+平价）
   * 被**禁止决策**、各跳过多少根。只跳过决策，不删行、不改价。
   */
  dataQuality: Record<string, unknown> | null;
  /**
   * 逐币阶段映射 `{symbol: 'BOX'|'SWEEP'|'RETEST'|'IDLE'|...}`（SIGNAL-OBSERVE-FIX-12）。
   *
   * 后端 `signal_engine.per_symbol_state` 从 SIGNAL-CLOSURE-06 起就一直有，
   * 但**这里从来没解析过** ⇒ 前端拿不到任何逐币信息，只能看 `stateCounter`
   * 那一串计数。补上它是为了让「计数」与「具体是哪些币」能对上账。
   */
  perSymbolState: Record<string, string> | null;
  /**
   * 观察区**汇总**（SIGNAL-OBSERVE-FIX-12）：只含计数与就绪度，不含明细。
   * 明细走 `GET /signals/observe`（独立低频文件），原因见后端 `observe_summary`。
   */
  observeSummary: ObserveSummary | null;
}

/** 观察区汇总（来自每 2 秒重写的状态文件，体积可忽略）。 */
export interface ObserveSummary {
  definition: string | null;
  counts: Record<string, number>;
  observingTotal: number | null;
  dataNotReady: number | null;
  universe: number | null;
  detailEndpoint: string | null;
}

/**
 * 观察事件的**单项市场证据**（ACCUM-EARLY-02）。
 *
 * 工单原文：「主动买卖、三类多空比、阶段均价须进入每个观察事件的结构化证据记录
 * （实际读取值、时间范围、来源、缺失），不得留在『库里有但未参与』。」
 *
 * 纪律：值缺失就写 `missing` 原因，**不填 0、不拿上一轮冒充本轮**；
 * `fromLatestRow === false` 表示这一项是**逐列回退**得来的（最新行该列为空）。
 */
export interface ObserveEvidenceValue {
  /** 原始列名，用于去重判断与排错 */
  key: string;
  /** 中文含义（后端给，前端不自己翻译） */
  label: string;
  value: number | null;
  atUtc: string | null;
  lagSec: number | null;
  fromLatestRow: boolean | null;
  missing: string | null;
}

/** 阶段均价（成交量加权），只描述阶段位置，不是入场价。 */
export interface ObservePhasePrice {
  value: number | null;
  bars: number | null;
  fromUtc: string | null;
  toUtc: string | null;
  formula: string | null;
  source: string | null;
  missing: string | null;
}

/**
 * 一条观察事件的完整市场证据。
 *
 * `pending === true` 表示**后台还没算完**（单标的约 81ms，全池数百个事件约 30 秒），
 * 此时所有值为 null 且带 `missing` 说明 —— 不拿空对象冒充「已算完且无数据」。
 */
export interface ObserveEvidence {
  pending: boolean;
  /** 恒为 false：指标无发布时点 ⇒ 可得性 unproven，只能作证据 */
  inDecision: boolean;
  why: string | null;
  /** 主动买卖（5 分钟主动买量 / 主动卖量） */
  taker: ObserveEvidenceValue | null;
  /** 多空比：库里 5 列，但只有少数几类独立信息（同值即别名） */
  ratios: ObserveEvidenceValue[];
  stale: boolean;
  staleHours: number | null;
  staleNote: string | null;
  rangeFromUtc: string | null;
  rangeToUtc: string | null;
  source: string | null;
  availability: string | null;
  availabilityNote: string | null;
  tailNulls: string[];
  missingCols: string[];
  phasePrice: ObservePhasePrice | null;
  /** 整段缺失的原因清单（pending 时是「首次读取中」） */
  missing: string[];
}

/**
 * 一条「处于观察状态的事件」（SIGNAL-OBSERVE-FIX-12）。
 *
 * 定义来源：冻结规则 §3「每个 symbol+venue 最多一个处于观察状态的事件」
 * ⇒ 观察中 = `BOX` / `SWEEP` / `RETEST` 三态。`CANDIDATE` 及之后已结束，不算。
 * 这里只做字段归一，**不新增、不放宽任何门槛**。
 */
export interface ObserveItem {
  symbol: string;
  /** 形态阶段：箱体观察 / 已扫低 / 待突破 */
  state: string;
  stateCn: string;
  /** 下一关：SWEEP / RETEST / BREAKOUT */
  nextGate: string | null;
  createdUtc: string | null;
  /** 最近一次状态迁移（即「最近更新时间」） */
  updatedUtc: string | null;
  ageHours: number | null;
  /** 引擎已处理的最后一根 1h 收盘时间 */
  lastBarUtc: string | null;
  /** 该标的的数据是否已就绪（false ⇒ 不能拿「未满足」当结论） */
  dataReady: boolean;
  box: { h: number | null; l: number | null; a: number | null; er: number | null; widthAtr: number | null } | null;
  /** 已经走过的关（读事件流水，不重算） */
  satisfied: string[];
  /** 下一关明确没达到的条件，文案自带「当前实值」 */
  unmet: string[];
  /** 未到评估时点、暂不能下结论的条件（不等于不满足） */
  pendingEval: string[];
  deadlineUtc: string | null;
  remainingHours: number | null;
  expired: boolean | null;
  /** 结构化市场证据（主动买卖 / 多空比 / 阶段均价）；后端未给则为 null */
  evidence: ObserveEvidence | null;
}

/**
 * 观察区接口的**四态**（工单要求：不能统一显示成「交易信号 0」）。
 *  · ok            —— 读到且新鲜；此时 items 为空才是真的「没有符合条件的标的」
 *  · stale_record  —— 读到但已过期 / 写者已退出 ⇒ 不可当作现状
 *  · not_connected —— 运行时没起过或从未写出明细 ⇒ 数据未就绪
 *  · error         —— 读不了或解析失败 ⇒ 接口异常
 */
export type ObserveKind = 'ok' | 'stale_record' | 'not_connected' | 'error';

export interface ObserveView {
  kind: ObserveKind;
  /** 故障/未连接的具体原因；`kind === 'ok'` 时为 null */
  message: string | null;
  items: ObserveItem[];
  counts: Record<string, number>;
  observingTotal: number | null;
  dataNotReady: number | null;
  universe: number | null;
  shown: number | null;
  truncated: boolean;
  ageSeconds: number | null;
  generatedAt: string | null;
  scanErrorsTotal: number | null;
  definition: string | null;
}

/**
 * 观察区空态归因（**唯一实现**，页面不得自己再合成一遍）。
 *
 * 为什么必须分开：修复前「没有观察标的」「数据没就绪」「扫描/接口坏了」
 * 三种情况在页面上都显示成「交易信号 0 笔」，用户无法判断该等、该查还是该修。
 */
export type ObserveEmptyReason =
  | 'has_items'          // 有观察标的，正常渲染
  | 'fault'              // 接口/扫描异常
  | 'data_not_ready'     // 数据未就绪（写者不在、未写出、或全池无已处理 bar）
  | 'no_candidate';      // 数据正常、扫描正常，就是没有符合观察条件的标的

export function classifyObserve(v: ObserveView | null | undefined): ObserveEmptyReason {
  if (!v) return 'fault';
  if (v.kind === 'error' || v.kind === 'stale_record') return 'fault';
  if (v.kind === 'not_connected') return 'data_not_ready';
  // kind === 'ok'
  if (v.items.length > 0) return 'has_items';
  const scanned = v.universe ?? 0;
  if (scanned === 0) return 'data_not_ready';
  if ((v.scanErrorsTotal ?? 0) > 0) return 'fault';
  if ((v.dataNotReady ?? 0) > 0) return 'data_not_ready';
  return 'no_candidate';
}

/**
 * 观察事件市场证据 → 前端视图（**唯一**解析实现）。
 *
 * 为什么不在这里「补默认值」：证据的用途是让人判断「这个观察事件当时长什么样」。
 * 一旦前端把缺失填成 0、把 pending 填成空数组，页面就会显示成「有数据且是 0」，
 * 而真实情况是「没读到」。所以缺失一律保留 `missing` 原因。
 */
export function toObserveEvidence(raw: any): ObserveEvidence | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const num = (n: unknown): number | null =>
    typeof n === 'number' && Number.isFinite(n) ? n : null;
  const str = (s: unknown): string | null => (typeof s === 'string' && s !== '' ? s : null);
  const strArr = (a: unknown): string[] =>
    Array.isArray(a) ? a.filter((x: unknown): x is string => typeof x === 'string') : [];

  const one = (key: string, v: any): ObserveEvidenceValue | null => {
    if (!v || typeof v !== 'object') return null;
    return {
      key,
      label: str(v['含义']) ?? key,
      value: num(v['值']),
      atUtc: str(v['观测时刻_utc']),
      lagSec: num(v['距决策_秒']),
      fromLatestRow: typeof v['取自最新行'] === 'boolean' ? v['取自最新行'] : null,
      missing: str(v['缺失']),
    };
  };

  const mRaw = raw['主动买卖与多空比'];
  const m = mRaw && typeof mRaw === 'object' ? mRaw : null;
  const ratiosRaw = m ? m['三类多空比'] : null;
  const ratios: ObserveEvidenceValue[] = [];
  if (ratiosRaw && typeof ratiosRaw === 'object') {
    for (const k of Object.keys(ratiosRaw)) {
      const v = one(k, ratiosRaw[k]);
      if (v) ratios.push(v);
    }
  }
  const latest = m && typeof m['最新行'] === 'object' ? m['最新行'] : null;

  const pRaw = raw['阶段均价'];
  const p = pRaw && typeof pRaw === 'object' ? pRaw : null;

  return {
    pending: raw['pending'] === true,
    inDecision: raw['是否参与决策'] === true,
    why: str(raw['为什么只作证据']),
    taker: m ? one('sum_taker_long_short_vol_ratio', m['主动买卖']) : null,
    ratios,
    stale: m?.['陈旧'] === true,
    staleHours: m ? num(m['陈旧_小时']) : null,
    staleNote: m ? str(m['陈旧说明']) : null,
    rangeFromUtc: m?.['时间范围'] ? str(m['时间范围']['from_utc']) : null,
    rangeToUtc: m?.['时间范围'] ? str(m['时间范围']['to_utc']) : null,
    source: m ? str(m['来源']) : null,
    availability: m ? str(m['availability']) : null,
    availabilityNote: m ? str(m['availability_note']) : null,
    tailNulls: m ? strArr(m['尾部空值']) : [],
    missingCols: m ? strArr(m['缺失']) : [],
    phasePrice: p
      ? {
          value: num(p['阶段均价']),
          bars: num(p['bar数']),
          fromUtc: str(p['首根_utc']),
          toUtc: str(p['末根_utc']),
          formula: str(p['公式']),
          source: str(p['来源']),
          missing: str(p['缺失']),
        }
      : null,
    missing: strArr(raw['缺失']),
  };
}

/** 后端 `observe_events()` 一条原始记录 → 前端视图（字段名归一，不做推断）。 */
function toObserveItem(r: any): ObserveItem | null {
  const symbol = typeof r?.symbol === 'string' ? r.symbol : '';
  if (!symbol) return null;
  const num = (n: unknown): number | null =>
    typeof n === 'number' && Number.isFinite(n) ? n : null;
  const strArr = (a: unknown): string[] =>
    Array.isArray(a) ? a.filter((x: unknown): x is string => typeof x === 'string') : [];
  const boxRaw = r?.box && typeof r.box === 'object' ? r.box : null;
  return {
    symbol,
    state: typeof r?.state === 'string' ? r.state : '',
    stateCn: typeof r?.state_cn === 'string' ? r.state_cn : '',
    nextGate: typeof r?.next_gate === 'string' ? r.next_gate : null,
    createdUtc: typeof r?.created_utc === 'string' ? r.created_utc : null,
    updatedUtc: typeof r?.updated_utc === 'string' ? r.updated_utc : null,
    ageHours: num(r?.age_hours),
    lastBarUtc: typeof r?.last_bar_utc === 'string' ? r.last_bar_utc : null,
    dataReady: r?.data_ready === true,
    box: boxRaw
      ? {
          h: num(boxRaw.H),
          l: num(boxRaw.L),
          a: num(boxRaw.A),
          er: num(boxRaw.ER),
          widthAtr: num(boxRaw.width_atr),
        }
      : null,
    satisfied: strArr(r?.satisfied),
    unmet: strArr(r?.unmet),
    pendingEval: strArr(r?.pending_eval),
    deadlineUtc: typeof r?.deadline_utc === 'string' ? r.deadline_utc : null,
    remainingHours: num(r?.remaining_hours),
    expired: typeof r?.expired === 'boolean' ? r.expired : null,
    evidence: toObserveEvidence(r?.evidence),
  };
}

/**
 * 观察区接口原始信封 → `ObserveView`（**唯一**归一实现）。
 *
 * 页面用 `useEndpoint('/signals/observe')` 直接拿原始信封时也走这里，
 * 避免「一个接口两种解析」导致两处口径分叉。
 */
export function toObserveView(env: any): ObserveView {
  const q = env?.quality || {};
  const status = typeof q.status === 'string' ? q.status : 'ok';
  const kind: ObserveKind =
    status === 'ok'
      ? 'ok'
      : status === 'stale_record'
        ? 'stale_record'
        : status === 'not_connected'
          ? 'not_connected'
          : 'error';
  const rawItems: any[] = Array.isArray(env?.items) ? env.items : [];
  const items = rawItems
    .map(toObserveItem)
    .filter((x): x is ObserveItem => x !== null);
  return {
    kind,
    message: typeof env?.missing_reason === 'string' ? env.missing_reason : null,
    items,
    counts:
      env?.counts && typeof env.counts === 'object'
        ? (env.counts as Record<string, number>)
        : { BOX: 0, SWEEP: 0, RETEST: 0 },
    observingTotal: typeof env?.observing_total === 'number' ? env.observing_total : null,
    dataNotReady: typeof env?.data_not_ready === 'number' ? env.data_not_ready : null,
    universe: typeof env?.universe === 'number' ? env.universe : null,
    shown: typeof env?.shown === 'number' ? env.shown : null,
    truncated: env?.truncated === true,
    ageSeconds: typeof env?.age_seconds === 'number' ? env.age_seconds : null,
    generatedAt: typeof env?.generated_at === 'string' ? env.generated_at : null,
    scanErrorsTotal:
      typeof env?.scan_errors_total === 'number' ? env.scan_errors_total : null,
    definition: typeof env?.definition === 'string' ? env.definition : null,
  };
}

/**
 * `GET /signals/observe` 观察中事件明细（SIGNAL-OBSERVE-FIX-12）。
 *
 * 与 `fetchPaperDaemonStatus` 分两条通道：状态文件每 2 秒全量重写，
 * 塞 346 条明细约每天 10 GB 写盘；明细只在收盘推进/阶段迁移时才变 ⇒ 独立低频文件。
 * 这里**如实**把四态传出去，绝不把「读不到」压成「0 条」。
 */
export async function fetchObserveEvents(
  arm: string = '3a',
  signal?: AbortSignal,
): Promise<ObserveView> {
  try {
    const env = await apiGet<any>(
      `/signals/observe?arm=${encodeURIComponent(arm)}`,
      signal,
    );
    return toObserveView(env);
  } catch (err) {
    return {
      kind: 'error',
      message: err instanceof Error ? err.message : String(err),
      items: [],
      counts: { BOX: 0, SWEEP: 0, RETEST: 0 },
      observingTotal: null,
      dataNotReady: null,
      universe: null,
      shown: null,
      truncated: false,
      ageSeconds: null,
      generatedAt: null,
      scanErrorsTotal: null,
      definition: null,
    };
  }
}

/**
 * 一条「历史回放产生、按纪律丢弃」的候选（后端 `bootstrap.discarded_detail` 原样透传）。
 * 它**不是**当前可交易信号：只用于让「历史回放丢弃 N 个」这个数字可追溯到具体事件。
 */
export interface DiscardedCandidate {
  candidateId: string;
  symbol: string;
  snapshotUtc: string | null;
  availableAtUtc: string | null;
  executionEarliestUtc: string | null;
  discardReasons: string[];
  boxHighH: number | null;
  boxLowL: number | null;
  breakoutClose: number | null;
  volumeRatio: number | null;
  initialStopPrice: number | null;
}

/**
 * `GET /paper/status` 获取模拟盘执行守护进程及信号引擎全景状态。
 */
export async function fetchPaperDaemonStatus(
  arm: string = '3a',
  signal?: AbortSignal,
): Promise<ExecutionDaemonView> {
  try {
    const env = await apiGet<any>(
      `/paper/execution_daemon/status?arm=${encodeURIComponent(arm)}`,
      signal,
    );
    const item = env?.item || (unwrapItems(env)[0] as any) || env?.data?.item || {};
    const execDaemon = item.execution_daemon || {};
    const runtimeIdent = item.runtime_identity || {};
    const signalEngine = item.signal_engine || {};
    const marketData = item.market_data || {};

    const running =
      item.running === true ||
      execDaemon.status === 'running' ||
      execDaemon.status === 'ok' ||
      item.status === 'running' ||
      runtimeIdent.status === 'running';

    const status =
      item.status ||
      execDaemon.status ||
      runtimeIdent.status ||
      (running ? 'running' : 'stopped');

    // 后端把进程身份放在 `runtime_identity` 里（pid / cycle_count 都在该层），
    // 顶层没有同名字段。此前只读顶层 ⇒ 页面恒显示「守护 PID: —、推进周期: 0 次」，
    // 即使运行时确实在跑，也像没启动。
    const pid =
      typeof item.pid === 'number'
        ? item.pid
        : typeof execDaemon.pid === 'number'
          ? execDaemon.pid
          : typeof runtimeIdent.pid === 'number'
            ? runtimeIdent.pid
            : null;

    const cycleCount =
      typeof item.cycle_count === 'number'
        ? item.cycle_count
        : typeof execDaemon.cycle_count === 'number'
          ? execDaemon.cycle_count
          : typeof runtimeIdent.cycle_count === 'number'
            ? runtimeIdent.cycle_count
            : 0;

    const signalEngineLastCycleUtc =
      typeof signalEngine.last_cycle_ms === 'number' && Number.isFinite(signalEngine.last_cycle_ms)
        ? new Date(signalEngine.last_cycle_ms).toISOString()
        : null;
    const lastStepUtc =
      signalEngineLastCycleUtc ||
      item.last_step_utc ||
      execDaemon.last_step_utc ||
      item.last_heartbeat_utc ||
      execDaemon.updated_at ||
      item.updated_at ||
      null;

    const blockedReason =
      item.blocked_reason ||
      execDaemon.blocked_reason ||
      item.block_reason ||
      runtimeIdent.block_reason ||
      null;

    const overallHealth =
      item.health ||
      runtimeIdent.overall_health ||
      env.quality?.status ||
      'ok';

    // ── 诊断透传（SIGNAL-UI-LINK-07）：只做类型归一，不做任何推断/补默认 ──────
    const bsObj =
      signalEngine.bootstrap && typeof signalEngine.bootstrap === 'object'
        ? (signalEngine.bootstrap as Record<string, any>)
        : null;
    const rawDetail: any[] = Array.isArray(bsObj?.discarded_detail) ? bsObj!.discarded_detail : [];
    const discardedCandidates: DiscardedCandidate[] = rawDetail
      .map((d) => ({
        candidateId: String(d?.candidate_id ?? ''),
        symbol: String(d?.symbol ?? ''),
        snapshotUtc: typeof d?.snapshot_utc === 'string' ? d.snapshot_utc : null,
        availableAtUtc: typeof d?.available_at_utc === 'string' ? d.available_at_utc : null,
        executionEarliestUtc:
          typeof d?.execution_earliest_utc === 'string' ? d.execution_earliest_utc : null,
        discardReasons: Array.isArray(d?.discard_reasons)
          ? d.discard_reasons.filter((x: unknown): x is string => typeof x === 'string')
          : [],
        boxHighH: typeof d?.box_high_h === 'number' ? d.box_high_h : null,
        boxLowL: typeof d?.box_low_l === 'number' ? d.box_low_l : null,
        breakoutClose: typeof d?.breakout_close === 'number' ? d.breakout_close : null,
        volumeRatio: typeof d?.volume_ratio === 'number' ? d.volume_ratio : null,
        initialStopPrice: typeof d?.initial_stop_price === 'number' ? d.initial_stop_price : null,
      }))
      .filter((d) => d.candidateId !== '');

    const numOrNull = (n: unknown): number | null =>
      typeof n === 'number' && Number.isFinite(n) ? n : null;
    const msToIso = (n: unknown): string | null => {
      const v = numOrNull(n);
      return v !== null && v > 0 ? new Date(v).toISOString() : null;
    };

    return {
      running,
      status,
      pid,
      cycleCount,
      lastStepUtc,
      blockedReason,
      overallHealth,
      signalEngineStatus: signalEngine.status || null,
      marketDataStatus: marketData.status || null,
      universe: typeof signalEngine.universe === 'number' ? signalEngine.universe : null,
      stateCounter:
        signalEngine.state_counter && typeof signalEngine.state_counter === 'object'
          ? (signalEngine.state_counter as Record<string, number>)
          : null,
      bootstrap:
        signalEngine.bootstrap && typeof signalEngine.bootstrap === 'object'
          ? (signalEngine.bootstrap as Record<string, unknown>)
          : null,
      replayCandidates:
        signalEngine.bootstrap &&
        typeof signalEngine.bootstrap === 'object' &&
        typeof (signalEngine.bootstrap as any).history_candidates_discarded === 'number'
          ? ((signalEngine.bootstrap as any).history_candidates_discarded as number)
          : null,
      // 缺陷（SIGNAL-HISTORY-FIX-03 实测）：`atrCaliber` 之前只在接口里声明、
      // `PositionPanel` 也在渲染，但**这里从来没赋值** ⇒ `executionDaemon.atrCaliber`
      // 恒为 undefined ⇒ 页面上「ATR 口径」那一行永远不出现。声明了不等于接上了。
      atrCaliber:
        signalEngine.atr_caliber && typeof signalEngine.atr_caliber === 'object'
          ? (signalEngine.atr_caliber as Record<string, unknown>)
          : null,
      watermarkCheck:
        signalEngine.watermark_check && typeof signalEngine.watermark_check === 'object'
          ? (signalEngine.watermark_check as Record<string, unknown>)
          : null,
      dataQuality:
        signalEngine.data_quality && typeof signalEngine.data_quality === 'object'
          ? (signalEngine.data_quality as Record<string, unknown>)
          : null,
      // SIGNAL-OBSERVE-FIX-12：后端从 SIGNAL-CLOSURE-06 起就有逐币阶段，
      // 但此前**从未解析** ⇒ 前端只能显示计数串。这里原样透传。
      perSymbolState:
        signalEngine.per_symbol_state && typeof signalEngine.per_symbol_state === 'object'
          ? (signalEngine.per_symbol_state as Record<string, string>)
          : null,
      observeSummary: (() => {
        const s = signalEngine.observe_summary;
        if (!s || typeof s !== 'object') return null;
        return {
          definition: typeof (s as any).definition === 'string' ? (s as any).definition : null,
          counts:
            (s as any).counts && typeof (s as any).counts === 'object'
              ? ((s as any).counts as Record<string, number>)
              : { BOX: 0, SWEEP: 0, RETEST: 0 },
          observingTotal:
            typeof (s as any).observing_total === 'number' ? (s as any).observing_total : null,
          dataNotReady:
            typeof (s as any).data_not_ready === 'number' ? (s as any).data_not_ready : null,
          universe: typeof (s as any).universe === 'number' ? (s as any).universe : null,
          detailEndpoint:
            typeof (s as any).detail_endpoint === 'string' ? (s as any).detail_endpoint : null,
        };
      })(),
      discardedCandidates,
      recoveredCandidates:
        bsObj && typeof bsObj.recovered_candidates === 'number'
          ? (bsObj.recovered_candidates as number)
          : null,
      lastCandidateUtc:
        typeof (signalEngine.funnel as any)?.last_candidate_utc === 'string'
          ? ((signalEngine.funnel as any).last_candidate_utc as string)
          : null,
      funnel:
        signalEngine.funnel && typeof signalEngine.funnel === 'object'
          ? (signalEngine.funnel as Record<string, unknown>)
          : null,
      idleBreakdown:
        signalEngine.idle_breakdown && typeof signalEngine.idle_breakdown === 'object'
          ? (signalEngine.idle_breakdown as Record<string, unknown>)
          : null,
      watermark: {
        h1: numOrNull(signalEngine.watermark_1h),
        h4: numOrNull(signalEngine.watermark_4h),
        h1Utc: msToIso(signalEngine.watermark_1h),
        h4Utc: msToIso(signalEngine.watermark_4h),
      },
      marketLatest: {
        close15mUtc: msToIso(marketData.latest_15m_close_time),
        close1hUtc: msToIso(marketData.latest_1h_close_time),
        close4hUtc: msToIso(marketData.latest_4h_close_time),
      },
    };
  } catch (err) {
    return {
      running: false,
      status: 'error',
      pid: null,
      cycleCount: null,
      lastStepUtc: null,
      blockedReason: err instanceof Error ? err.message : String(err),
      overallHealth: 'error',
      signalEngineStatus: null,
      marketDataStatus: null,
      universe: null,
      stateCounter: null,
      bootstrap: null,
      replayCandidates: null,
      atrCaliber: null,
      watermarkCheck: null,
      dataQuality: null,
      perSymbolState: null,
      observeSummary: null,
      discardedCandidates: [],
      recoveredCandidates: null,
      lastCandidateUtc: null,
      funnel: null,
      idleBreakdown: null,
      watermark: null,
      marketLatest: null,
    };
  }
}


