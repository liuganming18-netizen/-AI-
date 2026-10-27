/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 实时信号区的**状态分类器**（SIGNAL-UI-LINK-07-R1）。
 *
 * 为什么必须有它：`pullSignals()` 原来只要 HTTP 200 就 `setSignalsError(null)`，
 * 于是「后端明确说读不到」「接口 500」「网络断了」全被压成同一张脸 —— 空列表。
 * 用户看到的是「暂无信号」，而真相可能是「有效下限读不到，根本没法判定当前信号」。
 *
 * 契约（与 `框架/backend/live_api.py`、`adapter.py` 对齐）：
 *   `/api/v1/signals` 的 `quality.status` 取值集合固定为四种：
 *     · `ok`            —— 正常。**条数为 0 也是正常**，不是故障；
 *     · `no_watermark`  —— 有效下限（`max(live_start, paper epoch)`）缺失 ⇒ 无法判定「当前」；
 *     · `not_connected` —— 实时信号层不可用（模块导入失败 / 数据源未接通）；
 *     · `error`         —— 实时信号层读取过程中抛异常。
 *   另有 `missing_reason`：**只在出问题时非空**，用来携带人话原因。
 *   另有 `quality.note`：**恒有值**，是口径说明（如「origin∈{live,recovered} + enterable…」），
 *   **单独出现绝不构成故障** —— 这正是「不能把所有非空说明字段一律当故障」的那条线。
 *
 * 分类顺序（严格）：
 *   1. 先看 `quality.status` 是否落在已知故障集合；
 *   2. 再看 `missing_reason` 是否非空（状态未知但后端明确报了缺什么 ⇒ 降级）；
 *   3. 都不是 ⇒ 正常（`note` 不参与判断）。
 */

import type { Envelope } from './types';

/** 故障类型：每一种在页面上有不同标题与不同「下一步」，不合并成一句话。 */
export type SignalsFaultKind =
  /** 有效下限缺失（paper epoch / live_start 读不到）⇒ 无法判定当前有效信号 */
  | 'no_watermark'
  /** 实时信号层未接通（模块不可用 / 数据源未连接） */
  | 'not_connected'
  /** 实时信号层读取失败（后端内部异常） */
  | 'error'
  /** 后端明确给出 `missing_reason`，但状态不在已知集合内 ⇒ 如实降级 */
  | 'degraded'
  /** HTTP 非 2xx */
  | 'http'
  /** 连不上后端（fetch 抛错，含代理/端口不可达） */
  | 'network';

export type SignalsState =
  | { kind: 'loading' }
  | { kind: 'ok'; total: number }
  | {
      kind: 'fault';
      fault: SignalsFaultKind;
      /** 给人看的一句话原因（优先用 `missing_reason`） */
      message: string;
      /** HTTP 状态码；业务级故障为 `null` */
      status: number | null;
      /** 后端 `quality.note` 原文（口径说明，用于展开查看，不作为判定依据） */
      detail: string | null;
    };

/** `quality.status` → 故障类型。**只列已知故障**；`ok`/未知/缺失都不在此表。 */
const FAULT_BY_STATUS: Record<string, SignalsFaultKind> = {
  no_watermark: 'no_watermark',
  not_connected: 'not_connected',
  error: 'error',
  unavailable: 'error',
};

const isNonEmptyString = (v: unknown): v is string =>
  typeof v === 'string' && v.trim().length > 0;

/**
 * 把一次 200 响应分类成「正常」或「业务故障」。
 *
 * **`quality.note` 永不单独构成故障** —— 它恒非空，是口径说明；把它当故障会让每次
 * 正常轮询都亮红灯。
 */
export function classifySignalsEnvelope(env: Envelope<unknown> | null | undefined): SignalsState {
  const q = (env?.quality ?? null) as Record<string, unknown> | null;
  const statusRaw = q && typeof q.status === 'string' ? q.status.trim().toLowerCase() : '';
  const note = isNonEmptyString(q?.note) ? (q!.note as string) : null;
  const missing = isNonEmptyString(env?.missing_reason) ? env!.missing_reason! : null;
  const total =
    typeof env?.total === 'number'
      ? env.total
      : Array.isArray(env?.items)
        ? env!.items!.length
        : 0;

  const fault = statusRaw ? FAULT_BY_STATUS[statusRaw] : undefined;
  if (fault) {
    return {
      kind: 'fault',
      fault,
      message: missing ?? note ?? `后端状态为 ${statusRaw}`,
      status: null,
      detail: note,
    };
  }
  if (missing) {
    // 状态没落在已知集合，但后端明确写了「缺什么」⇒ 如实降级，不当作正常空列表
    return { kind: 'fault', fault: 'degraded', message: missing, status: null, detail: note };
  }
  return { kind: 'ok', total };
}

/** 把一次抛出的异常（`ApiError` / 网络错误 / 其他）分类成故障态。 */
export function classifySignalsError(err: unknown): SignalsState {
  const status =
    err && typeof err === 'object' && typeof (err as { status?: unknown }).status === 'number'
      ? ((err as { status: number }).status as number)
      : null;
  const message =
    err instanceof Error && err.message
      ? err.message
      : typeof err === 'string' && err
        ? err
        : '实时信号读取失败（未知错误）';

  let fault: SignalsFaultKind;
  if (status === 0) fault = 'network';
  else if (status !== null && status >= 400) fault = 'http';
  else fault = 'error'; // 例如「后端响应不是合法 JSON」（HTTP 200 但内容不可用）

  return { kind: 'fault', fault, message, status, detail: null };
}

/** 面板根节点的 `data-signals-state`，供验收脚本确定性读取（不靠文案匹配）。 */
export function signalsStateKey(s: SignalsState | null | undefined): string {
  if (!s) return 'unknown';
  if (s.kind === 'ok') return 'ok';
  if (s.kind === 'loading') return 'loading';
  return `fault:${s.fault}`;
}

/** 每种故障的中文标题与「这不是什么」提示 —— 三句话必须不同，不许合并。 */
export const SIGNALS_FAULT_TEXT: Record<
  SignalsFaultKind,
  { title: string; hint: string }
> = {
  no_watermark: {
    title: '当前信号无法判定：有效下限缺失',
    hint: '这不是「没有信号」，而是判定「当前」所必需的水位线（实时起点 / 模拟盘启用时点）读不到，接口拒绝猜测。',
  },
  not_connected: {
    title: '实时信号层未接通',
    hint: '这不是「没有信号」，而是后端实时信号模块不可用（数据源未连接）。接通后本区会自动恢复。',
  },
  error: {
    title: '实时信号层读取失败',
    hint: '这不是「没有信号」，而是后端在读取实时信号时抛了异常。原因见下方原文。',
  },
  degraded: {
    title: '实时信号层降级',
    hint: '后端明确报告了缺失原因，但状态未落在已知集合内；按降级处理，不当作正常空列表。',
  },
  http: {
    title: '实时信号接口返回错误',
    hint: '这不是「没有信号」，而是接口以非 2xx 响应。恢复后本区会自动重新加载。',
  },
  network: {
    title: '无法连接后端',
    hint: '这不是「没有信号」，而是浏览器连不上 /api/v1（后端未启动或端口不可达）。',
  },
};
