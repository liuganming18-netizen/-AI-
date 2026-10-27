/**
 * UI-PENDING-01 · 挂单展示独立组件测试夹具（TEST FIXTURE）
 *
 * ⚠ 这里每一行都是**人工构造的测试数据**，不来自任何账本、不来自任何接口响应。
 *   · 标的一律带 `TESTFIX` 前缀，肉眼即可与真实市场区分；
 *   · 时间戳固定（2026-10-24 UTC），截图与断言可逐位复现；
 *   · 本夹具只证明「页面在给定输入下如何渲染」，**不能**作为真实成交闭环证据，
 *     也不能证明后端真的会产出这些字段。
 *
 * 字段取值严格照 `框架/backend/paper_engine.py::account_view()` 的挂单投影
 * （pending_id / symbol / origin / signal_uid / event_time / available_at /
 *  requested_at / earliest_fill_ms / created_at / status / reason），
 * 不额外塞入契约里没有的字段（方向、数量等）。
 */

import type { PaperAccountView, PaperPendingView } from '../src/api/liveTrading';

export const FIXTURE_BANNER = 'TEST FIXTURE · 人工构造的挂单数据 · 非真实账本';

/** 构造一行挂单（只允许覆盖契约内字段）。 */
export function makePending(
  over: Partial<PaperPendingView> & { pendingId: number; symbol: string },
): PaperPendingView {
  return {
    pendingId: over.pendingId,
    symbol: over.symbol,
    origin: over.origin ?? null,
    signalUid: over.signalUid ?? null,
    eventTime: over.eventTime ?? null,
    availableAt: over.availableAt ?? null,
    requestedAt: over.requestedAt ?? 0,
    earliestFillMs: over.earliestFillMs ?? 0,
    createdAt: over.createdAt ?? 0,
    status: over.status ?? 'pending',
    reason: over.reason ?? null,
  };
}

/** 构造一个纸面账户视图（只填挂单块渲染所需的最小集合）。 */
export function makeAccount(pending: PaperPendingView[], arm = 'A'): PaperAccountView {
  return {
    arm,
    accountId: `paper43_${arm}`,
    initialEquity: 5000,
    cash: 5000,
    equity: 5000,
    unrealizedPnl: 0,
    usedMargin: 0,
    grossNotional: 0,
    realizedPnlTotal: 0,
    feesPaidTotal: 0,
    fundingPaidTotal: 0,
    drawdown: 0,
    drawdownLocked: false,
    nPositions: 0,
    nOrders: 0,
    nRejected: 0,
    nClosed: 0,
    modelVersion: 'TESTFIX-MODEL',
    paramsSource: 'TESTFIX',
    positions: [],
    pending,
    nPending: pending.length,
  };
}

/** 挂单 #9001：信号触发，`reason` 为 null（真实挂单常态）。 */
export const ROW_SIGNAL = makePending({
  pendingId: 9001,
  symbol: 'TESTFIXUSDT',
  origin: 'signal',
  signalUid: 'sig_TESTFIX_9001',
  eventTime: 1792800000000, // 10-24 00:00Z
  availableAt: 1792800060000, // 10-24 00:01Z
  requestedAt: 1792800900000, // 10-24 00:15Z
  earliestFillMs: 1792801800000, // 10-24 00:30Z
  createdAt: 1792800900000,
  status: 'pending',
  reason: null,
});

/** 挂单 #9002：手动请求，带一条**显式标记**的 `reason`（用于验证该字段真的被渲染）。 */
export const ROW_MANUAL_WITH_REASON = makePending({
  pendingId: 9002,
  symbol: 'TESTFIX2USDT',
  origin: 'manual',
  signalUid: null,
  eventTime: 1792803600000, // 10-24 01:00Z
  availableAt: 1792803660000, // 10-24 01:01Z
  requestedAt: 1792804500000, // 10-24 01:15Z
  earliestFillMs: 1792805400000, // 10-24 01:30Z
  createdAt: 1792804500000,
  status: 'pending',
  reason: 'test_fixture_reason_visible',
});

export type CaseName = 'has_pending' | 'empty' | 'unreadable' | 'not_connected';

export interface CaseFixture {
  accounts: PaperAccountView[];
  pending: PaperPendingView[];
  missingReason: string | null;
}

/** 三态 + 未连接态的统一入参。 */
export const CASES: Record<CaseName, CaseFixture> = {
  has_pending: {
    accounts: [makeAccount([ROW_SIGNAL, ROW_MANUAL_WITH_REASON])],
    pending: [ROW_SIGNAL, ROW_MANUAL_WITH_REASON],
    missingReason: null,
  },
  empty: {
    accounts: [makeAccount([])],
    pending: [],
    missingReason: null,
  },
  // 账本读取失败：父层保留**上一次**的 accounts（含挂单），必须不得渲染成当前挂单
  unreadable: {
    accounts: [makeAccount([ROW_SIGNAL, ROW_MANUAL_WITH_REASON])],
    pending: [ROW_SIGNAL, ROW_MANUAL_WITH_REASON],
    missingReason: 'TEST FIXTURE：纸面账本读取失败（后端不可达或未初始化）',
  },
  not_connected: {
    accounts: [],
    pending: [],
    missingReason: null,
  },
};
