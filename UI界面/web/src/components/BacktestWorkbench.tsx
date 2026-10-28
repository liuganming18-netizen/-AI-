/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * **单策略回测工作台**（`WX_OI_RESONANCE_V1`）—— 真实 API 接线版。
 *
 * 六个屏（全部接真数据，缺失一律 `—` 并说明，绝不造数）
 * ──────────────────────────────────────────────────
 * ① 配置与运行：唯一策略 + 版本、交易对（真实池）、交易所/市场/周期（未支持项**禁用并解释**）、
 *    UTC 起止、初始资金、单边手续费、滑点 bps、风险/仓位、策略参数（白名单校验 + 后台快照）。
 * ② 收益指标：收益率 / 最大回撤 / 胜率 / 平均盈亏比 / profit factor / 交易次数 / 手续费 / 资金费，
 *    并展示**所选 run 的实际时间/配置/版本**（表单未运行的新值绝不冒充结果配置）。
 * ③ 资金分析：策略净值 vs 买入持有基准、回撤曲线、月度热力图（见 `LabFundingPanel`）。
 * ④ 执行视图：真实 15m K 线 / 原始成交量 / 信号 / 开平仓 / 保护止损轨迹 + 选中定位（见 `LabChartPanel`）。
 * ⑤ 明细表 + 单笔详情（signal_id / 原始规则 / ATR / 结构证据 / 订单 / 止损轨迹 / 成本 / 资金费）。
 * ⑥ 实验记录 + 质量报告（expected/actual/缺口、OI 门禁、资金费覆盖、对账、快照、hash）。
 *
 * 纪律
 * ────
 * · **不在浏览器重算交易与止损**；所有成交/止损/成本只来自后台账本与 chart 载荷。
 * · 运行完成的「执行状态」与「正式策略放行 / M4 验收」**分开显示**（后者来自 `formal_m4_gate`）。
 * · 非 `succeeded` 的运行**一律不得显示为绿色成功**（即便 HTTP 200）。
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  FlaskConical,
  Info,
  Play,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import {
  LAB_EXCHANGE_OPTIONS,
  LAB_EXEC_TF_OPTIONS,
  LAB_MARKET_OPTIONS,
  LAB_MIN_WINDOW_MS,
  LAB_PARAM_SPECS,
  LAB_SIGNAL_TF_OPTIONS,
  LAB_STRATEGY_ID,
  LAB_UNIT_LABEL,
  LAB_WINDOW_GRID_MS,
  labCreateRun,
  labErrorsOf,
  labFetchRunHashMap,
  labFromWire,
  labGetChart,
  labGetEquity,
  labGetFills,
  labGetMetrics,
  labGetOrders,
  labGetPool,
  labGetQuality,
  labGetRejected,
  labGetRun,
  labGetTrade,
  labGetTrades,
  labListRuns,
  labLoadDefaults,
  labToWire,
  labValidateParams,
  type LabChart,
  type LabChartResponse,
  type LabDefaults,
  type LabEquityPoint,
  type LabEquityResponse,
  type LabFillRow,
  type LabFillsResponse,
  type LabMetricsResponse,
  type LabOrderRow,
  type LabOrdersResponse,
  type LabParamSpec,
  type LabPoolItem,
  type LabQualityResponse,
  type LabRejectedResponse,
  type LabRejectedRow,
  type LabRegistryEntry,
  type LabRetirementInfo,
  type LabRunDetail,
  type LabRunHashes,
  type LabRunSummary,
  type LabTradeDetail,
  type LabTradeRow,
  type LabTradesResponse,
} from '../api/labBacktest';
import { LabChartPanel } from './LabChartPanel';
import { LabFundingPanel } from './LabFundingPanel';
import { ErrorBoundary } from './ErrorBoundary';
import { MetricStrip } from './backtest/MetricStrip';
import { RunInfoCard } from './backtest/RunInfoCard';
import { BacktestSidePanel } from './backtest/BacktestSidePanel';
import { BatchBar } from './backtest/BatchBar';
import { BatchTradesTable } from './backtest/BatchTradesTable';
import { exitReasonLabel, statusLabel } from '../utils/runMeta';
import { createCrosshairBus } from '../utils/crosshairBus';
import {
  fetchBatches,
  fetchBatchRuns,
  fetchBatchSummary,
  fetchBatchTrades,
  type BtBatch,
  type BtBatchRun,
  type BtBatchSummaryResponse,
  type BtBatchTrade,
} from '../api/backtestReview';

/* ══════════════════════════ 格式化（未知一律 '—'）══════════════════════════ */

/**
 * K 线上下文天数（BT-KLINE-CONTEXT-01）。
 *
 * 为什么是 7：策略的前置条件是 **7 天箱体 + 24h 冷却**，而 `FT-ENTRY-09` 那批 run 的窗口
 * 只有 0.25~27 小时（实测最短的 1h 图只有 1 根、`1000XECUSDT` 只有 7 根）⇒ 只画 run 区间
 * 根本看不到生成信号的箱体。7 天 = 刚好够看一个完整箱体 + 冷却。
 *
 * 后端只**加**不改：run 区间内的 K 线原样保留，新增两段单独计数，
 * 界面上必须把「上下文」与「本 run 消费区间」分开讲（见 `LabChartPanel` 的读数行）。
 */
const CONTEXT_DAYS = 7;

const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtUsd = (v: number | null | undefined, digits = 2): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `$${fmtNum(v, digits)}`;

/** 整数（千分位）。未知一律 `—`，不拿 0 冒充。 */
const fmtInt = (v: number | null | undefined): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : Math.round(v).toLocaleString('en-US');

/**
 * 窗口天数：优先取后端给的 `window_days`，取不到就用 `win_start_ms / win_end_ms`
 * 自己算。两个都没有 ⇒ `null`（调用方据此**不写这句话**，而不是打印「— 天」）。
 */
const windowDaysOf = (win: any): number | null => {
  if (!win) return null;
  if (typeof win.window_days === 'number' && Number.isFinite(win.window_days)) {
    return Math.round(win.window_days);
  }
  const a = win.win_start_ms;
  const b = win.win_end_ms;
  if (
    typeof a === 'number' && Number.isFinite(a) &&
    typeof b === 'number' && Number.isFinite(b) &&
    b > a
  ) {
    return Math.round((b - a) / 86_400_000);
  }
  return null;
};

const fmtSignedUsd = (v: number | null | undefined, digits = 2): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : v < 0 ? '-' : ''}$${fmtNum(Math.abs(v), digits)}`;
};

const fmtPct = (v: number | null | undefined, digits = 4): string =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const fmtUtc = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return '—';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

const fmtIsoUtc = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? `${fmtUtc(ms)} UTC` : iso;
};

const shortHash = (h: string | null | undefined, n = 12): string =>
  !h ? '—' : h.length <= n ? h : `${h.slice(0, n)}…`;

/** `YYYY-MM-DDTHH:mm`（按 **UTC** 解释）→ 毫秒；非法 ⇒ null。 */
function parseUtcInput(raw: string): number | null {
  const s = raw.trim().replace(' ', 'T');
  if (!s) return null;
  const norm = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s) ? `${s}:00Z` : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(s) ? `${s}Z` : s;
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(norm)) return null;
  const ms = Date.parse(norm);
  return Number.isFinite(ms) ? ms : null;
}

function toUtcInput(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '';
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

function toIsoUtcZ(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}Z`;
}

/** 运行状态 → 徽标色调（《设计规范》第 09 节：徽标只承载状态，六种色调不外扩）。 */
const STATUS_TONE: Record<string, string> = {
  succeeded: 'ds-badge--pos',
  queued: 'ds-badge--info',
  running: 'ds-badge--info',
  failed: 'ds-badge--neg',
  interrupted: 'ds-badge--warn',
};

const statusTone = (s: string | null | undefined): string =>
  STATUS_TONE[s ?? ''] ?? 'ds-badge--neutral';

/** 数值 → 盈亏色调。`null` / 非有限值一律走中性，不用颜色冒充方向。 */
const pnlTone = (v: number | null | undefined): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? 'ds-badge--neutral'
    : v >= 0
      ? 'ds-badge--pos'
      : 'ds-badge--neg';

/**
 * 退出原因短标签 —— 统一走 `utils/runMeta.exitReasonLabel`（全站一张表）。
 * 一屏并排布局下「退出原因」列只有 ~90px，后端英文原值（`intrabar_stop`）会被截断成
 * `intrabar…`；映射为等义中文短标签，**未知值原样显示**，`title` 始终带后端原值。
 */

/* ══════════════════════════ 只读取数 hook ══════════════════════════ */

interface AsyncState<T> {
  /** **本状态所属的请求键**。返回时只把 `key` 与当前请求键一致的状态交出去。 */
  key: string | null;
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * 只读取数 hook。**身份安全**：状态自带 `key`，返回值只在 `state.key === key` 时给出，
 * 否则一律返回「空数据 + loading」—— 这样**切换 run / 参数时不会有一帧读到旧结果**，
 * 也不会出现 A run 的指标挂到 B run 标题下的串身份显示。
 *
 * 失败语义：取数失败 ⇒ `data = null` + `error`（**清掉旧结果**，不把上一份数据顶上来冒充本次结果）；
 * 运行自身的失败（`status=failed`）不在这里处理，由运行状态块按 `failure_reason` 如实显示。
 */
function useLabFetch<T>(key: string | null, loader: () => Promise<T>): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ key: null, data: null, error: null, loading: false });
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  /** 请求在途标记：轮询节拍据此跳过，避免堆叠请求 */
  const inflightRef = useRef(false);

  useEffect(() => {
    if (key === null) {
      inflightRef.current = false;
      setState({ key: null, data: null, error: null, loading: false });
      return;
    }
    let alive = true;
    inflightRef.current = true;
    // 立刻清掉上一份结果（不保留旧值），保证「切换即清」
    setState({ key, data: null, error: null, loading: true });
    loaderRef
      .current()
      .then((d) => {
        if (alive) {
          inflightRef.current = false;
          setState({ key, data: d, error: null, loading: false });
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          inflightRef.current = false;
          setState({ key, data: null, error: labErrorsOf(e)[0] ?? '取数失败', loading: false });
        }
      });
    return () => {
      alive = false;
      inflightRef.current = false;
    };
  }, [key, tick]);

  const reload = useCallback(() => {
    if (inflightRef.current) return; // 在途则跳过，防止轮询堆叠
    setTick((t) => t + 1);
  }, []);

  const current: AsyncState<T> =
    state.key === key
      ? state
      : { key, data: null, error: null, loading: key !== null };

  return { ...current, reload };
}

/**
 * 响应身份核对：详情 / 指标 / 图表 / 交易等响应必须与**所选 run_id** 一致。
 * 不一致 ⇒ 抛错（页面显示为取数错误），绝不把别的 run 的数字挂到当前标题下。
 */
function expectRunId<T extends { run_id?: string | null }>(env: T, expected: string): T {
  const got = env && typeof env === 'object' ? env.run_id : null;
  if (got && got !== expected) {
    throw new Error(`响应身份不符：run_id=${got} ≠ 所选 ${expected}`);
  }
  return env;
}

/* ══════════════════════════ 小构件 ══════════════════════════ */

/**
 * 设计规范组件（详见《设计规范》第 09 / 10 / 12 节）。
 *
 * 全部改用 `.ds-*` 类：颜色 / 字号 / 间距 / 圆角一律由令牌层决定，页面不再硬编码。
 * 实现要点：`components.css` 是**未分层**规则，优先级高于 Tailwind 的 `@layer utilities`，
 * 因此 `.ds-badge` / `.ds-table` 等会覆盖残留的旧工具类 —— 这正是「统一观感」的机制，
 * 也是为什么这里可以安全地只替换组件类、而不必逐处清理旧 className。
 */
const Chip: React.FC<{ children: React.ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'bad' | 'info' }> = ({
  children,
  tone = 'neutral',
}) => {
  const map: Record<string, string> = {
    neutral: 'ds-badge--neutral',
    ok: 'ds-badge--pos',
    warn: 'ds-badge--warn',
    bad: 'ds-badge--neg',
    info: 'ds-badge--info',
  };
  return <span className={`ds-badge ${map[tone]}`}>{children}</span>;
};

const KV: React.FC<{ k: string; v: React.ReactNode; mono?: boolean }> = ({ k, v, mono = true }) => (
  <div className="ds-kv">
    <span className="ds-kv__k">{k}</span>
    <span
      className="ds-kv__v"
      style={mono ? undefined : { fontFamily: 'var(--ds-font)' }}
      title={typeof v === 'string' ? v : undefined}
    >
      {v}
    </span>
  </div>
);

/**
 * 安全渲染「hash 叶子值」。
 *
 * 后端 `/quality` 的 `hashes` 不是「键 → 字符串」的扁平表，而是**分组对象**：
 *   { code: { combined_sha256, files: {路径: sha}, missing: [...] },
 *     data: { combined_sha256, files: {...}, missing: [...] }, ... }
 * 原先直接 `Object.entries(hashes).map(([k,v]) => <KV v={v}/>)` 把对象当 React
 * 子节点塞进 `<span>` ⇒ React 抛
 * 「Objects are not valid as a React child (found: object with keys
 *  {combined_sha256, files, missing})」⇒ 整个「口径与审计」视图白屏
 * （对账表就在这个视图里，等于任务 5 的取证入口被前端自己打崩）。
 *
 * 这里按分组对象显示「合并摘要前 16 位 · files=N · missing=N」，完整 JSON 放进
 * `title` 悬浮提示，**不丢信息、也不再崩**。非对象一律按原样字符串化。
 */
function fmtHashLeaf(v: unknown): React.ReactNode {
  if (v === null || v === undefined) return '—';
  const t = typeof v;
  if (t === 'string' || t === 'number' || t === 'boolean') return String(v);
  if (t === 'object') {
    const o = v as Record<string, unknown>;
    const sha = typeof o['combined_sha256'] === 'string' ? (o['combined_sha256'] as string) : null;
    const filesObj = o['files'];
    const files = filesObj && typeof filesObj === 'object'
      ? Object.keys(filesObj as Record<string, unknown>).length : null;
    const missing = Array.isArray(o['missing']) ? (o['missing'] as unknown[]).length : null;
    const parts: string[] = [];
    if (sha) parts.push(`${sha.slice(0, 16)}…`);
    if (files !== null) parts.push(`files=${files}`);
    if (missing !== null) parts.push(`missing=${missing}`);
    const text = parts.length ? parts.join(' · ') : JSON.stringify(o);
    return <span title={JSON.stringify(o, null, 1)}>{text}</span>;
  }
  return String(v);
}

const SectionCard: React.FC<{
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  testid?: string;
  /** 区块锚点 id：与 `BacktestView` 的区块导航一一对应（《设计规范》第 17 节）。 */
  section?: string;
  /** 附属区块（⑤b / ⑤c / ⑤d）：标题竖条降级为灰色，视觉上从属于主区块。 */
  sub?: boolean;
  /** 可折叠：用于「重要但不必常驻视野」的附属明细。标题行仍显示条数徽标。 */
  collapsible?: boolean;
  /** 折叠时是否默认展开（默认收起）。 */
  defaultOpen?: boolean;
}> = ({ title, right, children, testid, section, sub = false, collapsible = false, defaultOpen = false }) => {
  const cls = `ds-card${sub ? ' ds-card--sub' : ''}`;
  /**
   * 折叠态用原生 `<details>`，但**不**把 `open` 交给 React 受控 ——
   * 本页有 5s 轮询，受控的 `open` 会在每次轮询渲染时把用户刚展开的面板弹回去。
   * 因此只在挂载时设一次初值，之后完全交给浏览器。
   */
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.open = defaultOpen;
  }, [defaultOpen]);

  if (collapsible) {
    return (
      <details ref={ref} className={cls} data-testid={testid} data-lab-section={section}>
        <summary className="ds-card__head">
          <h3 className="ds-card__title">{title}</h3>
          {right}
        </summary>
        <div className="ds-card__body">{children}</div>
      </details>
    );
  }

  return (
    <section className={cls} data-testid={testid} data-lab-section={section}>
      <div className="ds-card__head">
        <h3 className="ds-card__title">{title}</h3>
        {right}
      </div>
      <div className="ds-card__body">{children}</div>
    </section>
  );
};

/**
 * 「显示全部列」开关。
 * 表格默认只给「决策必需的列」，其余列用 `data-secondary` 由 CSS 隐藏 ——
 * DOM 与数据一个不少（验收脚本仍可查到），只是默认不出现在视野里。
 */
const ColToggle: React.FC<{ on: boolean; onToggle: () => void; total: number }> = ({
  on,
  onToggle,
  total,
}) => (
  <button
    type="button"
    className="ds-btn ds-btn--ghost ds-btn--sm"
    aria-pressed={on}
    onClick={(e) => {
      // 本按钮可能位于可折叠卡片的 <summary> 内 —— 不拦截会连带把卡片开合。
      e.preventDefault();
      e.stopPropagation();
      onToggle();
    }}
    title={on ? '收起次要列' : `展开全部 ${total} 列`}
  >
    {on ? '收起次要列' : '更多列'}
  </button>
);

/* ══════════════════════════ 配置表单 ══════════════════════════ */

interface LabForm {
  symbol: string;
  exchange: string;
  market: string;
  signalTf: string;
  execTf: string;
  windowStart: string;
  windowEnd: string;
  warmup: string;
  /** 参数输入（**显示值**字符串；空 = 沿用后台冻结默认，不发送覆盖） */
  params: Record<string, string>;
}

const EMPTY_FORM: LabForm = {
  symbol: 'BTCUSDT',
  exchange: 'binance',
  market: 'um',
  signalTf: '1h',
  execTf: '15m',
  windowStart: '',
  windowEnd: '',
  warmup: '',
  params: {},
};

/**
 * 紧凑分段选择：标签在左、按钮在右（一行内），未支持项 `disabled` + **简短 tooltip**。
 * 不再为每个禁用项重复打印 ALLOWED_* 长说明（原因统一收进 title，保持首屏紧凑）。
 */
const OptionRow: React.FC<{
  label: string;
  value: string;
  options: { value: string; label: string; enabled: boolean; reason?: string }[];
  onChange: (v: string) => void;
  /** 紧凑模式：标签与按钮同一行（默认 true） */
  inline?: boolean;
}> = ({ label, value, options, onChange, inline = true }) => (
  <div className={inline ? 'ds-optrow' : 'space-y-0.5'}>
    <div className="ds-optrow__label">{label}</div>
    <div className="ds-row" style={{ gap: 4 }}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={!o.enabled}
          title={o.enabled ? `${label} = ${o.value}` : o.reason}
          aria-pressed={value === o.value}
          onClick={() => o.enabled && onChange(o.value)}
          className="ds-opt"
        >
          {o.label}
        </button>
      ))}
    </div>
  </div>
);

/** 参数输入：清楚标出单位（小数 / 百分数 / bps / USD / 根 / 倍）与等价换算；白名单代码名收进 tooltip。 */
const ParamField: React.FC<{
  spec: LabParamSpec;
  value: string;
  onChange: (v: string) => void;
}> = ({ spec, value, onChange }) => {
  const shown = value.trim() === '' ? null : Number(value);
  const wire = shown !== null && Number.isFinite(shown) ? labToWire(spec, shown) : null;
  let equiv: string | null = null;
  if (wire !== null) {
    if (spec.key === 'fee_rate') equiv = `= ${(wire * 10000).toFixed(2)} bps / ${(wire * 100).toFixed(4)}%`;
    else if (spec.unit === 'percent') equiv = `= ${wire}（小数）`;
    else if (spec.unit === 'bps') equiv = `= ${(wire / 10000).toFixed(6)}（小数）`;
    else if (spec.unit === 'usd') equiv = `= ${wire.toLocaleString()}`;
  }
  const bounds =
    spec.min !== null || spec.max !== null ? `[${spec.min ?? '-∞'}, ${spec.max ?? '+∞'}]` : null;
  return (
    <label className="ds-field" title={`${spec.key}${spec.hint ? ` · ${spec.hint}` : ''}`}>
      <span className="ds-field__label">
        <span className="ds-truncate">{spec.label}</span>
        <Chip tone={spec.unit === 'percent' ? 'info' : 'neutral'}>{LAB_UNIT_LABEL[spec.unit]}</Chip>
      </span>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        placeholder={spec.type === 'int' ? '整数' : '数值'}
        onChange={(e) => onChange(e.target.value)}
        className="ds-input"
        data-param-key={spec.key}
      />
      {(equiv || bounds) && (
        <span className="ds-field__hint">
          {equiv}
          {equiv && bounds ? ' · ' : ''}
          {bounds}
        </span>
      )}
    </label>
  );
};

/* ══════════════════════════ 单笔详情 ══════════════════════════ */

interface StopTracePoint {
  ts: number;
  stop: number;
  reason: string;
}

function parseStopTrace(raw: string | null | undefined): StopTracePoint[] {
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr
      // 字段契约兼容：官方 `stop_trace_json` 用 `t`（毫秒），历史行可能仍是 `ts`。
      // 两者都接受 ⇒ 上游往哪边收敛都能显示；都没有的点丢弃（不猜时间）。
      .filter((p) => p && (Number.isFinite(p.t) || Number.isFinite(p.ts)) && Number.isFinite(p.stop))
      .map((p) => ({
        ts: (Number.isFinite(p.t) ? p.t : p.ts) as number,
        stop: p.stop as number,
        reason: String(p.reason ?? ''),
      }));
  } catch {
    return [];
  }
}

const LabTradeDetailPanel: React.FC<{ detail: LabTradeDetail | null; loading: boolean; error: string | null }> = ({
  detail,
  loading,
  error,
}) => {
  if (loading && !detail) return <div className="text-[11px] text-neutral-500 p-3">正在读取单笔详情…</div>;
  if (error) return <div className="text-[11px] text-rose-600 p-3">单笔详情取数失败：{error}</div>;
  if (!detail?.trade) return <div className="text-[11px] text-neutral-400 p-3">点击明细行查看单笔详情</div>;

  const t = detail.trade;
  const sig = detail.original_signal;
  const orders = detail.orders ?? [];
  const funding = detail.funding ?? [];
  const trace = parseStopTrace(t.stop_trace_json);

  return (
    <div className="space-y-2 text-[11px]" data-testid="lab-trade-detail">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-bold text-neutral-100">trade_id = {t.trade_id}</span>
        <Chip tone="info">signal_id = {detail.signal_id ?? '—'}</Chip>
        <Chip>{t.symbol}</Chip>
        <Chip tone={t.net_pnl >= 0 ? 'ok' : 'bad'}>{t.direction}</Chip>
        <Chip>{t.exit_reason}</Chip>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 font-mono text-[11px]">
        <KV k="进场" v={`${fmtUtc(t.entry_time_ms)} @ ${fmtNum(t.entry_price, 4)}`} />
        <KV k="出场" v={`${fmtUtc(t.exit_time_ms)} @ ${fmtNum(t.exit_price, 4)}`} />
        <KV k="数量" v={fmtNum(t.qty, 6)} />
        <KV k="持仓小时" v={fmtNum(t.holding_hours, 2)} />
        <KV k="毛盈亏" v={fmtSignedUsd(t.gross_pnl, 2)} />
        <KV k="手续费" v={`${fmtUsd(t.fees, 2)}（入 ${fmtNum(t.entry_fee, 2)} / 出 ${fmtNum(t.exit_fee, 2)}）`} />
        <KV k="资金费" v={`${fmtUsd(t.funding_fee, 2)}（${t.funding_events ?? '—'} 事件）`} />
        <KV k="净盈亏" v={fmtSignedUsd(t.net_pnl, 2)} />
        <KV k="MFE / MAE" v={`${fmtNum(t.mfe, 6)} / ${fmtNum(t.mae, 6)}`} />
        <KV k="funding_gaps" v={t.funding_gaps_json ?? '—'} />
      </div>

      <div className="border-t border-neutral-100 pt-2">
        <div className="font-bold text-neutral-200 mb-1">原始信号（signal_id 规则 / ATR / 结构证据）</div>
        {sig ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 font-mono">
            <KV k="kind" v={sig.kind} />
            <KV k="direction" v={sig.direction} />
            <KV k="signal_bar_open" v={fmtUtc(sig.signal_bar_open_ms)} />
            <KV k="confirm" v={fmtUtc(sig.confirm_ms)} />
            <KV k="stop_price" v={fmtNum(sig.stop_price, 4)} />
            <KV k="atr" v={fmtNum(sig.atr, 6)} />
            <KV k="oi_score" v={fmtNum(sig.oi_score, 6)} />
            <KV k="oi_available" v={String(sig.oi_available ?? '—')} />
            <KV k="oi_quality" v={sig.oi_quality ?? '—'} />
            <KV k="rank_key" v={sig.rank_key ?? '—'} />
            <KV k="in_window" v={String(sig.in_window ?? '—')} />
          </div>
        ) : (
          <div className="text-neutral-400">后台未返回该 signal_id 的原始信号行（不编造）</div>
        )}
      </div>

      <div className="border-t border-neutral-100 pt-2">
        <div className="font-bold text-neutral-200 mb-1">订单与成交（账本 orders）</div>
        {orders.length === 0 ? (
          <div className="text-neutral-400">无订单行</div>
        ) : (
          <table className="w-full text-left font-mono">
            <thead>
              <tr>
                <th className="py-1 pr-2">时点</th>
                <th className="py-1 px-2">phase</th>
                <th className="py-1 px-2">side</th>
                <th className="py-1 px-2">action</th>
                <th className="py-1 px-2 text-right">价格</th>
                <th className="py-1 px-2 text-right">数量</th>
                <th className="py-1 px-2 text-right">手续费</th>
                <th className="py-1 px-2 text-right">止损价</th>
                <th className="py-1 px-2">原因</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td className="py-1 pr-2 text-neutral-500">{fmtUtc(o.ts_ms)}</td>
                  <td className="py-1 px-2">{o.phase}</td>
                  <td className="py-1 px-2">{o.side}</td>
                  <td className="py-1 px-2">{o.action}</td>
                  <td className="py-1 px-2 text-right">{fmtNum(o.price, 4)}</td>
                  <td className="py-1 px-2 text-right">{fmtNum(o.qty, 6)}</td>
                  <td className="py-1 px-2 text-right">{fmtNum(o.fee, 6)}</td>
                  <td className="py-1 px-2 text-right">{fmtNum(o.stop_price, 4)}</td>
                  <td className="py-1 px-2 text-neutral-500">{o.reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="border-t border-neutral-100 pt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <div className="font-bold text-neutral-200 mb-1">保护止损轨迹（引擎实况 {trace.length} 点）</div>
          {trace.length === 0 ? (
            <div className="text-neutral-400">无止损轨迹记录</div>
          ) : (
            <div className="ds-tablewrap" style={{ maxHeight: '10rem' }}>
              <table className="w-full text-left font-mono">
                <thead>
                  <tr>
                    <th className="py-1 pr-2">时点</th>
                    <th className="py-1 px-2 text-right">止损价</th>
                    <th className="py-1 px-2">原因</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.map((p, i) => (
                    <tr key={`${p.ts}-${i}`}>
                      <td className="py-1 pr-2 text-neutral-500">{fmtUtc(p.ts)}</td>
                      <td className="py-1 px-2 text-right">{fmtNum(p.stop, 4)}</td>
                      <td className="py-1 px-2 text-neutral-500">{p.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div>
          <div className="font-bold text-neutral-200 mb-1">资金费事件（{funding.length} 条）</div>
          {funding.length === 0 ? (
            <div className="text-neutral-400">该笔无资金费事件</div>
          ) : (
            <table className="w-full text-left font-mono">
              <thead>
                <tr>
                  <th className="py-1 pr-2">结算时点</th>
                  <th className="py-1 px-2 text-right">rate</th>
                  <th className="py-1 px-2 text-right">mark</th>
                  <th className="py-1 px-2 text-right">数量</th>
                  <th className="py-1 px-2 text-right">金额</th>
                  <th className="py-1 px-2">来源</th>
                </tr>
              </thead>
              <tbody>
                {funding.map((f) => (
                  <tr key={`${f.funding_time_ms}-${f.amount}`}>
                    <td className="py-1 pr-2 text-neutral-500">{fmtUtc(f.funding_time_ms)}</td>
                    <td className="py-1 px-2 text-right">{f.rate ?? '—'}</td>
                    <td className="py-1 px-2 text-right">{fmtNum(f.mark, 2)}</td>
                    <td className="py-1 px-2 text-right">{fmtNum(f.qty, 6)}</td>
                    <td className="py-1 px-2 text-right">{fmtNum(f.amount, 6)}</td>
                    <td className="py-1 px-2 text-neutral-500">{f.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

/* ══════════════════════════ 质量报告 ══════════════════════════ */

const BoolFlag: React.FC<{ label: string; v: boolean | null | undefined; good?: boolean }> = ({ label, v, good = true }) => {
  if (v === null || v === undefined) return <KV k={label} v="—" />;
  const ok = good ? v : !v;
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-neutral-500">{label}</span>
      <span className={`font-mono font-semibold ${ok ? 'text-emerald-700' : 'text-rose-600'}`}>{v ? 'true' : 'false'}</span>
    </div>
  );
};

const LabQualityPanel: React.FC<{ q: LabQualityResponse | null; loading: boolean; error: string | null }> = ({
  q,
  loading,
  error,
}) => {
  const dq = q?.data_quality ?? null;
  const hashes = q?.hashes ?? null;
  const envStatus = q?.status ?? null;
  const qualityStatus = q?.quality?.status ?? null;
  /**
   * 绿色「通过」的**全部**前提（缺一不可）：
   *   ① 运行 status === 'succeeded'；
   *   ② 信封 quality.status === 'ok'（**不接受 null**：质量未知不得当成功）；
   *   ③ `data_quality` 实际存在（没有质量明细就无法断言通过）；
   *   ④ 本次质量请求没有取数错误。
   * 任何一条不满足 ⇒ 一律按异常显示（HTTP 200 不等于成功）。
   */
  const qualityOk = !error && envStatus === 'succeeded' && qualityStatus === 'ok' && dq !== null;
  const verdict = error
    ? `取数失败（不得视为成功）：${error}`
    : envStatus !== 'succeeded'
      ? `运行未成功（status=${envStatus ?? '—'}）`
      : qualityStatus !== 'ok'
        ? `质量未通过（quality.status=${qualityStatus ?? 'null'}）`
        : dq === null
          ? '缺 data_quality 明细 ⇒ 不能断言通过'
          : '通过（运行成功 + 质量 ok + 有质量明细）';

  if (loading && !q) return <div className="text-[11px] text-neutral-500">正在读取质量报告…</div>;
  if (error && !q) return <div className="text-[11px] text-rose-600">质量报告取数失败：{error}</div>;
  if (!q) return <div className="text-[11px] text-neutral-400">选择一次运行查看质量报告</div>;

  /**
   * 「只报异常」：按 `BoolFlag` 完全相同的判定把**异常项**挑出来抬到视野里，
   * 全部核对项仍完整保留在折叠区（DOM 一个不少，只是默认不占视野）。
   */
  const flag = (label: string, v: boolean | null | undefined, good = true): string | null => {
    if (v === null || v === undefined) return null;
    const ok = good ? v : !v;
    return ok ? null : label;
  };
  const dqBad: string[] = dq
    ? [
        flag('信号全窗覆盖', dq.signal_full_window_ok),
        flag('执行全窗覆盖', dq.exec_full_window_ok),
        flag('信号连续', dq.signal_contiguous),
        flag('执行连续', dq.exec_contiguous),
        flag('全部已收盘', dq.all_closed),
        flag('资金费为假设值', dq.funding_is_assumption, false),
        flag('资金费覆盖 ok', dq.funding_coverage_ok),
        flag('OI 门禁启用', dq.oi_gate_enabled, false),
        flag('对账 ok', dq.reconcile_ok),
        flag('指标与账本一致', dq.metrics_match),
      ].filter((x): x is string => x !== null)
    : [];

  return (
    <div className="space-y-2 text-[11px]" data-testid="lab-quality-panel">
      <div
        className={`ds-note ${qualityOk ? 'ds-note--pos' : 'ds-note--neg'}`}
        style={{ gap: 8, alignItems: 'center' }}
        data-testid="lab-quality-verdict"
      >
        <span className="font-bold flex items-center gap-1">
          {qualityOk ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
          质量结论：{verdict}
        </span>
        <span className="font-mono">
          运行 status {envStatus ?? '—'} · 信封 quality.status {qualityStatus ?? 'null'} · data_quality {dq ? '存在' : '缺失'}
        </span>
        {!qualityOk && (
          <span>（HTTP 200 不等于成功：运行未成功 / 质量非 ok / 缺质量明细一律按异常显示，不得当绿色成功）</span>
        )}
        {q.quality?.note && <span className="text-neutral-300">说明：{q.quality.note}</span>}
        {q.missing_reason && <span className="text-neutral-300">缺失原因：{q.missing_reason}</span>}
      </div>

      {dq ? (
        <>
          {/* 默认只报**异常项**；通过项与对账明细收进折叠区 */}
          {dqBad.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-neutral-500">异常项：</span>
              {dqBad.map((k) => (
                <span key={k} className="ds-badge ds-badge--neg" data-testid="lab-quality-bad">
                  {k}
                </span>
              ))}
            </div>
          ) : (
            <div className="text-emerald-700">核对项无异常</div>
          )}

          <details className="ds-details" data-testid="lab-quality-details">
            <summary>数据质量核对明细（全部核对项 · 现金恒等式对账 · 账本行数）</summary>
            <div className="ds-details__body space-y-2">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 font-mono">
                <BoolFlag label="信号全窗覆盖" v={dq.signal_full_window_ok} />
                <BoolFlag label="执行全窗覆盖" v={dq.exec_full_window_ok} />
                <BoolFlag label="信号连续" v={dq.signal_contiguous} />
                <BoolFlag label="执行连续" v={dq.exec_contiguous} />
                <BoolFlag label="全部已收盘" v={dq.all_closed} />
                <KV k="信号 bar expected/actual" v={`${dq.signal_expected_bars ?? '—'} / ${dq.signal_actual_bars ?? '—'}`} />
                <KV k="执行 bar expected/actual" v={`${dq.exec_expected_bars ?? '—'} / ${dq.exec_actual_bars ?? '—'}`} />
                <KV k="OI 缺根" v={String(dq.oi_bars_missing ?? '—')} />
                <KV k="OI 假填充" v={dq.oi_fake_fill ?? '—'} />
                <KV k="资金费模型" v={dq.funding_model ?? '—'} />
                <BoolFlag label="资金费为假设值" v={dq.funding_is_assumption} good={false} />
                <KV k="资金费 expected/actual" v={`${dq.funding_expected_settlements ?? '—'} / ${dq.funding_actual_settlements ?? '—'}`} />
                <KV k="资金费缺事件" v={String(dq.funding_missing_settlements_count ?? '—')} />
                <BoolFlag label="资金费覆盖 ok" v={dq.funding_coverage_ok} />
                <KV k="资金费 used/recomputed" v={`${dq.funding_events_used ?? '—'} / ${dq.funding_events_recomputed ?? '—'}`} />
                <BoolFlag label="OI 门禁启用" v={dq.oi_gate_enabled} good={false} />
                <KV k="OI 门槛值" v={String(dq.oi_min_resonance ?? '—')} />
                <BoolFlag label="对账 ok" v={dq.reconcile_ok} />
                <BoolFlag label="指标与账本一致" v={dq.metrics_match} />
                <KV k="指标差异" v={dq.metrics_match_diffs && dq.metrics_match_diffs.length ? dq.metrics_match_diffs.join(' | ') : '无'} />
              </div>

              {dq.oi_gate_note && <div className="ds-note ds-note--warn">OI 门禁：{dq.oi_gate_note}</div>}
              {dq.funding_evidence_reconcile && (
                <div className="text-[10px] text-neutral-400">资金费证据对账：{dq.funding_evidence_reconcile}</div>
              )}
              {dq.time_precision_note && <div className="text-[10px] text-neutral-500">时间精度：{dq.time_precision_note}</div>}

              {dq.reconcile && (
                <div className="border-t border-neutral-100 pt-2">
                  <div className="font-bold text-neutral-200 mb-1">现金恒等式对账（reconcile）</div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono">
                    {Object.entries(dq.reconcile).map(([k, v]) => (
                      <KV key={k} k={k} v={typeof v === 'boolean' ? String(v) : fmtNum(v as number, 6)} />
                    ))}
                  </div>
                </div>
              )}

              {dq.ledger_counts && (
                <div className="font-mono text-[10px] text-neutral-400">
                  账本行数：{Object.entries(dq.ledger_counts).map(([k, v]) => `${k}=${v}`).join(' · ')}
                </div>
              )}
            </div>
          </details>
        </>
      ) : (
        <div className="text-neutral-400">后台未返回 data_quality（不编造缺口数字）</div>
      )}

      <details className="ds-details" data-testid="lab-quality-hashes">
        <summary>代码 / 输入 hash · 输入快照（snapshot_files）</summary>
        <div className="ds-details__body grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <div className="font-bold text-neutral-200 mb-1">代码 / 输入 hash</div>
            {hashes ? (
              <div className="space-y-0.5 font-mono">
                {Object.entries(hashes).map(([k, v]) => (
                  <KV key={k} k={k} v={fmtHashLeaf(v)} />
                ))}
              </div>
            ) : (
              <div className="text-neutral-400">无 hash</div>
            )}
          </div>
          <div>
            <div className="font-bold text-neutral-200 mb-1">输入快照（snapshot_files）</div>
            {q.window ? (
              <div className="space-y-0.5 font-mono text-[10px]">
                <KV k="窗口" v={`${fmtUtc(q.window.win_start_ms)} → ${fmtUtc(q.window.win_end_ms)} UTC（${q.window.window_days} 天）`} />
                <KV k="预热" v={`${q.window.warmup_bars ?? '—'} 根信号 bar`} />
                <KV k="约定" v={q.window.convention} mono={false} />
              </div>
            ) : null}
          </div>
        </div>
      </details>
    </div>
  );
};

/* ══════════════════════════ 主组件 ══════════════════════════ */

export const BacktestWorkbench: React.FC = () => {
  /**
   * 五张图的十字线总线（K 线 / 成交量 / 持仓量 / 净值 / 回撤）。
   *
   * 这五张图是**五个独立实例**，官方库不会跨实例贯穿十字线 —— 没有总线时，
   * 鼠标停在 K 线上，另外四张图毫无反应。总线只传「时点」，值域由各图自己换算。
   * 用 ref 固定引用：切 run / 切周期时图表实例会重建，但总线本身不该重建。
   */
  const crosshairBusRef = useRef<ReturnType<typeof createCrosshairBus> | null>(null);
  if (!crosshairBusRef.current) crosshairBusRef.current = createCrosshairBus();
  const crosshairBus = crosshairBusRef.current;
  if (typeof window !== 'undefined') {
    (window as any).__labCrosshairBus = crosshairBus;
  }
  const [form, setForm] = useState<LabForm>(EMPTY_FORM);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [showSignalParams, setShowSignalParams] = useState(false);
  /** 高级设置：交易所 / 市场 / 周期 / 预热 / 成本 / 风险参数。默认收起 —— 这些项
   *  一次定好就很少改，常驻只会把「选标的 + 定窗口 + 点运行」这条主线淹掉。 */
  const [showAdvanced, setShowAdvanced] = useState(false);
  /** 次要列开关（按表 key 记忆）：默认只显示「决策必需的列」，
   *  其余列仍渲染在 DOM 中（`data-secondary` 由 CSS 隐藏），点「更多列」才显示。 */
  const [allCols, setAllCols] = useState<Record<string, boolean>>({});
  const toggleCols = (k: string) => setAllCols((v) => ({ ...v, [k]: !v[k] }));
  /**
   * 底部「更多视图」面板（执行视图 / 结果身份 / 订单 / 拒单 / 资金费 / 口径与审计）。
   * 一屏布局下这些内容不常驻视野，但**一个字段都不删** —— 点开就能看。
   * 单选：同一时间只展开一个，避免把「一屏」又撑回长页。
   */
  const [openPanel, setOpenPanel] = useState<string | null>(null);
  const togglePanel = (k: string) => setOpenPanel((v) => (v === k ? null : k));
  /** 订单/成交点击聚焦时间戳（联动图表） */
  const [focusTimeMs, setFocusTimeMs] = useState<number | null>(null);
  /** 当前选中的订单 ID（支持双向高亮与反查） */
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  /** 用户手动拖拽/缩放后的视窗范围（明确区分恢复手动视窗 vs 自动聚焦订单） */
  const [manualViewport, setManualViewport] = useState<{ fromMs: number; toMs: number } | null>(null);
  /** 当前选中的成交 ID（支持双向高亮与反查） */
  const [selectedFillId, setSelectedFillId] = useState<string | null>(null);
  /**
   * 「更多指标」开关。
   *
   * 指标原先单占一条满宽横条，与顶部「当前结果」条**重复**显示净收益 / 回撤 / 笔数。
   * 现并入同一条：4 个主指标常显，4 个次要指标（盈亏比 / PF / 手续费 / 资金费）默认收起。
   */
  /** 左栏「运行回测」表单是否展开（设计稿把配置收在左栏，缺省收起）。 */
  const [showRunForm, setShowRunForm] = useState(false);
  const [showMoreMetrics, setShowMoreMetrics] = useState(false);
  /**
   * 「更多视图」面板宿主 testid。
   *
   * 一屏布局下页面本身**不滚动**（内容 ≈ 容器高），而 identity 之外的按需面板渲染在
   * ⑤⑥ 之后、即首屏之外 —— 点按钮后若不平移，用户会「点了没反应」。
   * 展开后把该面板滚进视野（`block:'nearest'`：已可见时不动，避免无谓跳动）。
   */
  const PANEL_HOST_TESTID: Record<string, string> = {
    identity: 'lab-run-identity',
    orders: 'lab-orders',
    fills: 'lab-fills',
    rejected: 'lab-rejected',
    funding: 'lab-funding',
    audit: 'lab-audit',
  };
  useEffect(() => {
    if (!openPanel) return;
    const tid = PANEL_HOST_TESTID[openPanel];
    if (!tid) return;
    const id = window.setTimeout(() => {
      document.querySelector(`[data-testid="${tid}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, 60);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openPanel]);
  const [creating, setCreating] = useState(false);
  const [createNotice, setCreateNotice] = useState<string | null>(null);
  const [createErrors, setCreateErrors] = useState<string[]>([]);
  const [defaultsNote, setDefaultsNote] = useState<string | null>(null);
  /** **本页提交**的 run（用于「运行按钮在该 run 进入终态前保持禁用」）。 */
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);

  /* ── 列表 / 注册表 ── */
  const listKey = 'list';
  // BACKFILL-01：原为 600 条，而运行根已有 2127 个 run ⇒ 1500+ 条永远看不到。
  // 后端上限已提到 5000，这里一次取全量，搜索在本地过滤（零延迟、不额外压后端）。
  const list = useLabFetch<Awaited<ReturnType<typeof labListRuns>>>(listKey, () => labListRuns(3000));
  const runs: LabRunSummary[] = useMemo(() => list.data?.items ?? [], [list.data]);
  const registry: LabRegistryEntry | null = list.data?.registry_entry ?? null;
  /** 记录搜索词：按标的 / 运行 ID 过滤（本地，见 displayedRuns） */
  const [runQuery, setRunQuery] = useState('');

  /* ── 实验记录过滤（与批次无关的既有开关） ── */
  const [runsFilter, setRunsFilter] = useState<'all' | 'trades_only'>('trades_only');

  /* ── 批次（BT-UI-01）：列表 / 汇总 / 逐笔 / 图表全部锚定同一个 batch_id ──
   *
   * 旧实现用 `fetch('/all_trades.json')` 把一份**旧批次的 70 行静态快照**当「全市场成交」，
   * 与当前 run 的逐笔并排显示 ⇒ 批次列表、汇总、逐笔、图表不是同一批数据。
   * 现在全市场视图改为「本批次逐笔」（`/batches/{id}/trades`，逐 run 读账本现算）。
   */
  const [batchId, setBatchId] = useState<string | null>(null);
  /** true = 列表/逐笔按批次；false = 按单 run（既有视图，保留） */
  const [batchMode, setBatchMode] = useState<boolean>(true);
  const [batches, setBatches] = useState<BtBatch[]>([]);
  const [batchesLoading, setBatchesLoading] = useState(true);
  const [batchesError, setBatchesError] = useState<string | null>(null);
  const [batchSummary, setBatchSummary] = useState<BtBatchSummaryResponse | null>(null);
  const [batchSummaryLoading, setBatchSummaryLoading] = useState(false);
  const [batchSummaryError, setBatchSummaryError] = useState<string | null>(null);
  const [batchRuns, setBatchRuns] = useState<BtBatchRun[]>([]);
  const [batchRunsLoading, setBatchRunsLoading] = useState(false);
  const [batchTrades, setBatchTrades] = useState<BtBatchTrade[]>([]);
  const [batchTradesLoading, setBatchTradesLoading] = useState(false);
  const [batchTradesError, setBatchTradesError] = useState<string | null>(null);
  const [batchTradesMeta, setBatchTradesMeta] = useState<{
    missingInitialStop: number | null;
    unlinkedSignalTime: number | null;
  }>({ missingInitialStop: null, unlinkedSignalTime: null });
  const [batchNonce, setBatchNonce] = useState(0);

  /* 批次列表（轻量：只数目录，几十毫秒） */
  useEffect(() => {
    const ac = new AbortController();
    setBatchesLoading(true);
    setBatchesError(null);
    fetchBatches(ac.signal)
      .then((env) => {
        if (ac.signal.aborted) return;
        const items = Array.isArray(env.items) ? env.items : [];
        setBatches(items);
        setBatchesLoading(false);
        // 默认选「非默认根」里 run 最多的批次（= 最近一次完整批量运行），没有就选第一个。
        // BACKFILL-01：排除清单型批次（manifest_batch，如全币种压测）——它没有 run 目录，
        // 选中后清单与逐笔必然为空，不能让它抢占默认视图。
        setBatchId((prev) => {
          if (prev && items.some((b) => b.batch_id === prev)) return prev;
          const real = items.filter((b) => !b.is_default_root && !b.manifest_batch);
          const pool = real.length ? real : items;
          const best = [...pool].sort((a, b) => (b.runs_total ?? 0) - (a.runs_total ?? 0))[0];
          return best?.batch_id ?? null;
        });
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === 'AbortError') return;
        setBatchesError((err as Error)?.message || '批次列表取数失败');
        setBatchesLoading(false);
      });
    return () => ac.abort();
  }, [batchNonce]);

  /* 选中批次 ⇒ 汇总 + run 清单 + 逐笔（三者同 batch_id，一次刷新一起换） */
  useEffect(() => {
    if (!batchId || !batchMode) return;
    const ac = new AbortController();
    setBatchSummaryLoading(true);
    setBatchSummaryError(null);
    setBatchRunsLoading(true);
    setBatchTradesLoading(true);
    setBatchTradesError(null);

    fetchBatchSummary(batchId, ac.signal)
      .then((d) => {
        if (ac.signal.aborted) return;
        setBatchSummary(d);
        setBatchSummaryLoading(false);
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === 'AbortError') return;
        setBatchSummary(null);
        setBatchSummaryError((err as Error)?.message || '批次汇总取数失败');
        setBatchSummaryLoading(false);
      });

    fetchBatchRuns(batchId, ac.signal)
      .then((d) => {
        if (ac.signal.aborted) return;
        setBatchRuns(Array.isArray(d.items) ? d.items : []);
        setBatchRunsLoading(false);
      })
      .catch(() => {
        if (ac.signal.aborted) return;
        setBatchRuns([]);
        setBatchRunsLoading(false);
      });

    fetchBatchTrades(batchId, 5000, ac.signal)
      .then((d) => {
        if (ac.signal.aborted) return;
        setBatchTrades(Array.isArray(d.items) ? d.items : []);
        setBatchTradesMeta({
          missingInitialStop: d.missing_initial_stop ?? null,
          unlinkedSignalTime: d.unlinked_signal_time ?? null,
        });
        setBatchTradesLoading(false);
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === 'AbortError') return;
        setBatchTrades([]);
        setBatchTradesError((err as Error)?.message || '批次逐笔取数失败');
        setBatchTradesLoading(false);
      });

    return () => ac.abort();
  }, [batchId, batchMode, batchNonce]);

  const displayedRuns = useMemo(() => {
    let base: LabRunSummary[];
    if (batchMode) {
      // 批次 run 清单 → 复用实验记录表的行结构（缺的字段如实留空，不造 created_at）
      base = batchRuns.map((r) => ({
        run_id: r.run_id,
        status: r.status ?? 'unknown',
        symbols: r.symbol ? [r.symbol] : [],
        created_at: null,
        net_return_pct: r.net_return_pct,
        trades_count: r.trades_count,
        max_drawdown_pct: null,
        failure_reason: null,
        // FT-REPLAY-01：研究期重建的 run 不是当前策略的成绩 ⇒ 归属如实标成 research_period，
        // 不能一律写成 current_strategy（那会把别人的回测算到本策略头上）。
        attribution: r.research_period ? 'research_period' : 'current_strategy',
        research_period: r.research_period ?? null,
        // CODE-REVIEW-02：登记标记必须透传（批次 run 多数无 run.json ⇒ 未登记）
        registered: r.registered,
        record_note: r.record_note,
      })) as unknown as LabRunSummary[];
    } else {
      base = runs;
    }
    if (runsFilter === 'trades_only') {
      const filtered = base.filter((r) => (r.trades_count ?? 0) > 0);
      if (filtered.length > 0) base = filtered;
    }
    // BACKFILL-01：本地搜索（标的 / 运行 ID），全量在内存里过滤，无网络往返
    const kw = runQuery.trim().toLowerCase();
    if (kw) {
      base = base.filter((r) =>
        `${r.run_id} ${(r.symbols ?? []).join(' ')}`.toLowerCase().includes(kw),
      );
    }
    return base;
  }, [batchMode, batchRuns, runs, runsFilter, runQuery]);

  /* 自动选中最新/有成交的 run（仅首次）。声明在批次 effect 之前，供两者共用。 */
  const didPick = useRef(false);

  /**
   * 批次模式下：当前 run 若不属于本批次 ⇒ 自动切到批次内第一个有成交的 run。
   *
   * 为什么需要：切换批次时 `selectedRunId` 还是上一批的标的，若不重置，
   * 页面会显示「批次 A 的汇总 + 批次 B 的图表」—— 正是本工单要消灭的混批。
   */
  useEffect(() => {
    if (!batchMode || batchRuns.length === 0) return;
    if (selectedRunId && batchRuns.some((r) => r.run_id === selectedRunId)) return;
    const first = batchRuns.find((r) => (r.trades_count ?? 0) > 0) ?? batchRuns[0];
    if (first) {
      didPick.current = true;
      setSelectedRunId(first.run_id);
      setSelectedTradeId(null);
    }
  }, [batchMode, batchRuns, selectedRunId]);

  /* ── 默认值（来自后台真实 run）── */
  const defaults = useLabFetch<LabDefaults>('defaults', () => labLoadDefaults());
  const didInitForm = useRef(false);
  useEffect(() => {
    if (didPick.current || !defaults.data) return;
    const d = defaults.data;
    /**
     * 用函数式更新**保留已有窗口**：批次窗口 effect 可能比 defaults 先到
     * （批次汇总 2–5 秒、defaults 一次 `GET /backtests?limit=50` 可能更慢），
     * 若这里用 `EMPTY_FORM` 整体覆盖，就会把批次窗口清空 ⇒ 一进页面顶一条
     * 红色「窗口起点未填写」，看起来像页面坏了。
     */
    setForm((prev) => {
      const next: LabForm = { ...EMPTY_FORM, params: {} };
      if (d.symbol) next.symbol = d.symbol;
      if (d.exchange) next.exchange = d.exchange;
      if (d.market) next.market = d.market;
      if (d.signal_timeframe) next.signalTf = d.signal_timeframe;
      if (d.exec_timeframe) next.execTf = d.exec_timeframe;
      next.windowStart = prev.windowStart.trim() || toUtcInput(d.window_start_ms);
      next.windowEnd = prev.windowEnd.trim() || toUtcInput(d.window_end_ms);
      const params: Record<string, string> = {};
      for (const spec of LAB_PARAM_SPECS) {
        const src = spec.scope === 'signal' ? d.signal_params : d.exec_params;
        const v = src?.[spec.key];
        if (v === undefined || v === null || !Number.isFinite(v)) continue;
        params[spec.key] = String(labFromWire(spec, v));
      }
      next.params = params;
      return next;
    });
    didInitForm.current = true;
    if (!d.signal_params || !d.exec_params) {
      setDefaultsNote(
        '未能从后台取到冻结参数默认值（最近的成功运行均含参数覆盖）⇒ 参数输入框留空表示「沿用后台冻结值」；如需覆盖请显式填写。',
      );
    } else {
      setDefaultsNote(`参数默认值取自后台 run ${d.params_from_run}（该 run 无参数覆盖 ⇒ 即候选包冻结参数）。`);
    }
  }, [defaults.data]);

  /**
   * 批次窗口 ⇒ 运行配置的窗口默认值（**只在用户还没填时**）。
   *
   * 为什么需要：`labLoadDefaults` 的窗口来自「最近一次成功 run 的 window 字段」，
   * 而多数 run 的 summary.json 没有 `data_summary` ⇒ 该字段为 null ⇒ 表单留空 ⇒
   * 一进页面就顶一条红色「窗口起点未填写」的校验失败条（看起来像页面坏了）。
   * 批次窗口是真实数据（批次内 run 的 `inputs.json.data_summary.time_range_1h`），
   * 语义也对：想复跑这一批，就该用这批的窗口。
   */
  useEffect(() => {
    const ws = batchSummary?.batch?.window_start_ms ?? null;
    const we = batchSummary?.batch?.window_end_ms ?? null;
    if (ws == null || we == null) return;
    setForm((f) =>
      f.windowStart.trim() && f.windowEnd.trim()
        ? f
        : { ...f, windowStart: toUtcInput(ws), windowEnd: toUtcInput(we) },
    );
  }, [batchSummary]);

  /* ── 标的池（真实工作台活动池）── */  const pool = useLabFetch<Awaited<ReturnType<typeof labGetPool>>>('pool', () => labGetPool());
  const poolSymbols: LabPoolItem[] = useMemo(
    () => (pool.data?.items ?? []).filter((i) => i.exchange === 'binance'),
    [pool.data],
  );

  /* ── 运行 hash 补全（列表响应不含 hash）──
   * 批次模式下跳过：批次 run 不在全局列表里，补了也显示不出来；
   * 而每个补全都是一次 `GET /backtests/{id}`（后端单次 ~5ms，30 个会挤占图表请求）。 */
  const [hashMap, setHashMap] = useState<Record<string, LabRunHashes>>({});
  const hashedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (batchMode) return;
    const todo = runs.map((r) => r.run_id).filter((id) => !hashedRef.current.has(id)).slice(0, 30);
    if (todo.length === 0) return;
    todo.forEach((id) => hashedRef.current.add(id));
    let alive = true;
    void labFetchRunHashMap(todo, 4).then((m) => {
      if (alive) setHashMap((prev) => ({ ...prev, ...m }));
    });
    return () => {
      alive = false;
    };
  }, [runs, batchMode]);

  /* ── 轮询：只要还有 queued/running 的 run，就**持续**2s 拉列表直到全部进入终态 ── */
  const activeRun = useMemo(
    () => runs.find((r) => r.status === 'queued' || r.status === 'running') ?? null,
    [runs],
  );
  useEffect(() => {
    if (!activeRun) return;
    // 以 run_id 为依赖用 interval（而非一次性 timeout）：状态字符串在 queued→running 变化时
    // 若只调度一次就会漏掉后续轮询。interval 持续到该 run 离开非终态、activeRun 变 null 为止。
    const timer = window.setInterval(() => void list.reload(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.run_id]);

  /* ── 持久重开与深链恢复（BT-CHART-FIX-05）──
   * 从 URL 或 localStorage 恢复选中的回测组/Run、币种、订单、交易与周期，
   * 关闭页面重开后仍恢复正确记录，满足持久重开验收标准。 */
  const didRestoreRef = useRef(false);
  const [activeTimeframe, setActiveTimeframe] = useState<string>('1h');
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null);

  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;
    let urlRun: string | null = null;
    let urlSym: string | null = null;
    let urlOrder: string | null = null;
    let urlTrade: string | null = null;
    let urlTf: string | null = null;
    try {
      const q = new URLSearchParams(window.location.search);
      urlRun = q.get('run');
      urlSym = q.get('symbol');
      urlOrder = q.get('order');
      urlTrade = q.get('trade');
      urlTf = q.get('tf');
      const qVf = q.get('vf');
      const qVt = q.get('vt');
      if (!urlOrder && !urlTrade && qVf && qVt && Number.isFinite(Number(qVf)) && Number.isFinite(Number(qVt))) {
        setManualViewport({ fromMs: Number(qVf), toMs: Number(qVt) });
      }
    } catch {
      /* ignore */
    }

    if (!urlRun) {
      try {
        const saved = localStorage.getItem('wyckoff_backtest_last_selection');
        if (saved) {
          const parsed = JSON.parse(saved);
          urlRun = parsed.run || null;
          urlSym = parsed.symbol || null;
          urlOrder = parsed.order || null;
          urlTrade = parsed.trade || null;
          urlTf = parsed.tf || null;
          if (!urlOrder && !urlTrade && parsed.manualViewport && Number.isFinite(parsed.manualViewport.fromMs) && Number.isFinite(parsed.manualViewport.toMs)) {
            setManualViewport(parsed.manualViewport);
          }
        }
      } catch {
        /* ignore */
      }
    }

    if (urlRun) {
      didPick.current = true;
      setSelectedRunId(urlRun);
      setBatchMode(false);
    }
    if (urlSym) setActiveSymbol(urlSym);
    if (urlTrade) setSelectedTradeId(urlTrade);
    if (urlOrder) setSelectedOrderId(urlOrder);
    if (urlTf && ['15m', '30m', '1h', '4h'].includes(urlTf)) setActiveTimeframe(urlTf);
  }, []);

  /* 状态变化时持久化到 URL 与 localStorage */
  useEffect(() => {
    if (!selectedRunId) return;
    try {
      const q = new URLSearchParams(window.location.search);
      q.set('run', selectedRunId);
      if (activeSymbol) q.set('symbol', activeSymbol);
      else q.delete('symbol');
      if (selectedOrderId) q.set('order', selectedOrderId);
      else q.delete('order');
      if (selectedTradeId) q.set('trade', selectedTradeId);
      else q.delete('trade');
      if (activeTimeframe && activeTimeframe !== '1h') q.set('tf', activeTimeframe);
      else q.delete('tf');
      if (manualViewport && Number.isFinite(manualViewport.fromMs) && Number.isFinite(manualViewport.toMs)) {
        q.set('vf', String(manualViewport.fromMs));
        q.set('vt', String(manualViewport.toMs));
      } else {
        q.delete('vf');
        q.delete('vt');
      }

      const newUrl = `${window.location.pathname}?${q.toString()}`;
      window.history.replaceState(null, '', newUrl);

      localStorage.setItem(
        'wyckoff_backtest_last_selection',
        JSON.stringify({
          run: selectedRunId,
          symbol: activeSymbol,
          order: selectedOrderId,
          trade: selectedTradeId,
          tf: activeTimeframe,
          manualViewport: manualViewport || null,
        }),
      );
    } catch {
      /* ignore */
    }
  }, [selectedRunId, activeSymbol, selectedOrderId, selectedTradeId, activeTimeframe, manualViewport]);

  useEffect(() => {
    if (didPick.current) return;
    /**
     * 批次模式下**不许**从全局 `runs` 里挑 run。
     * 为什么（BT-UI-01 实测）：批次汇总/清单要 2–5 秒才到，而全局列表先到；
     * 一旦在这里选了全局 run，页面会先渲染「批次 A 的汇总 + 全局某个 run 的图表」，
     * 等批次清单到了再被替换 —— 用户看到的就是**混批**，正是本工单要消灭的问题。
     * 批次模式的 run 由上面「当前 run 不属于本批次 ⇒ 自动切换」那个 effect 负责。
     */
    if (batchMode) return;
    if (runs.length === 0) return;
    didPick.current = true;
    const bestRun = runs.find((r) => (r.trades_count ?? 0) > 0) ?? runs[0];
    setSelectedRunId((prev) => prev ?? bestRun.run_id);
  }, [runs, batchMode]);

  /* ── 所选 run 的详情 / 指标 / 曲线 / 逐笔 / 图表 / 质量（响应身份一律核对 run_id）── */
  const detail = useLabFetch<LabRunDetail>(
    selectedRunId ? `detail:${selectedRunId}` : null,
    () => labGetRun(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  /** 本页最近提交的 run 独立追踪；不依赖批次清单或当前选中的其他 run。 */
  const submittedDetail = useLabFetch<LabRunDetail>(
    submittedRunId ? `submitted:${submittedRunId}` : null,
    () => labGetRun(submittedRunId as string).then((d) => expectRunId(d, submittedRunId as string)),
  );
  const submittedDetailStatus = submittedDetail.data?.status ?? null;
  const submittedDetailPending = submittedRunId !== null &&
    !['succeeded', 'failed', 'cancelled', 'canceled'].includes(submittedDetailStatus ?? '');

  // 即使用户切换所选 run，本页提交的 job 也持续通过详情接口轮询到终态。
  useEffect(() => {
    if (!submittedRunId || !submittedDetailPending) return;
    const timer = window.setInterval(() => void submittedDetail.reload(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submittedRunId, submittedDetailPending]);

  const detailStatus = detail.data?.status ?? null;
  const isPending = detailStatus === 'queued' || detailStatus === 'running';

  // 运行未完成时**持续**轮询详情（interval 依赖 isPending，queued→running 期间不会漏拍）
  useEffect(() => {
    if (!selectedRunId || !isPending) return;
    const timer = window.setInterval(() => void detail.reload(), 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId, isPending]);

  const done = detailStatus === 'succeeded';
  const subKey = selectedRunId && done ? `${selectedRunId}:done` : null;

  const metrics = useLabFetch<LabMetricsResponse>(subKey, () =>
    labGetMetrics(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  const equity = useLabFetch<LabEquityResponse>(subKey, () =>
    labGetEquity(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  const trades = useLabFetch<LabTradesResponse>(subKey, () =>
    labGetTrades(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  /** 全局订单/成交明细（与逐笔同源账本 orders 表）。 */
  const orders = useLabFetch<LabOrdersResponse>(subKey, () =>
    labGetOrders(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  /** 逐次成交明细（账本 fills.jsonl 事件）。 */
  const fills = useLabFetch<LabFillsResponse>(subKey, () =>
    labGetFills(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  /** 拒单明细（账本 rejected 表）。 */
  const rejected = useLabFetch<LabRejectedResponse>(subKey, () =>
    labGetRejected(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );
  /**
   * FT-REPLAY-01：图表按「选中那一笔」聚焦。
   *
   * 为什么需要：一个 run 装的是该「参数组 × 币种」的全部成交，成交可能分散在几年里
   * （实测标记跨度中位 3.2 万根 15m、最长 10.7 万根）⇒ 一次画全只能给末段，早期成交
   * 的四个点（信号/确认/入场/出场）落在图外。把选中那一笔的入场时刻作为 `focus_ms`
   * 传给后端，K 线就取该笔前后各半，四个点必然都在图内。
   * 没选中任何一笔时传 null（不聚焦），行为与原来一致。
   */
  const chartFocusMs = useMemo(() => {
    if (!selectedTradeId) return null;
    const list = (trades.data?.items ?? []) as LabTradeRow[];
    const hit = list.find((x) => x?.trade_id === selectedTradeId);
    return typeof hit?.entry_time_ms === 'number' ? hit.entry_time_ms : null;
  }, [trades.data, selectedTradeId]);

  useEffect(() => {
    if (!selectedOrderId || focusTimeMs !== null) return;
    const hit = (orders.data?.items ?? []).find((o) => o.order_id === selectedOrderId);
    if (hit && hit.ts_ms) {
      setFocusTimeMs(hit.ts_ms);
      if (hit.symbol && !activeSymbol) {
        setActiveSymbol(hit.symbol.replace('USDT', ''));
      }
    }
  }, [orders.data, selectedOrderId, focusTimeMs, activeSymbol]);

  const chart = useLabFetch<LabChartResponse>(
    selectedRunId ? `${subKey}:sym:${activeSymbol || 'default'}:tf:${activeTimeframe}:focus:${chartFocusMs || 0}:ctx:${CONTEXT_DAYS}` : null,
    () =>
      labGetChart(selectedRunId as string, {
        focusMs: chartFocusMs ?? undefined,
        symbol: activeSymbol ?? undefined,
        timeframe: activeTimeframe,
        contextDays: CONTEXT_DAYS,
      }).then((d) => expectRunId(d, selectedRunId as string)),
  );
  const quality = useLabFetch<LabQualityResponse>(
    selectedRunId ? `quality:${selectedRunId}:${detailStatus ?? '?'}` : null,
    () => labGetQuality(selectedRunId as string).then((d) => expectRunId(d, selectedRunId as string)),
  );


  const tradeDetail = useLabFetch<LabTradeDetail>(
    selectedRunId && selectedTradeId ? `trade:${selectedRunId}:${selectedTradeId}` : null,
    () =>
      labGetTrade(selectedRunId as string, selectedTradeId as string).then((d) =>
        expectRunId(d, selectedRunId as string),
      ),
  );

  /* ── 表单校验与提交 ── */
  const windowStartMs = parseUtcInput(form.windowStart);
  const windowEndMs = parseUtcInput(form.windowEnd);

  const windowIssues = useMemo(() => {
    const errs: string[] = [];
    if (!form.windowStart.trim()) errs.push('窗口起点（UTC）未填写');
    else if (windowStartMs === null) errs.push('窗口起点无法解析为 UTC 时间（示例 2026-10-09T00:00）');
    if (!form.windowEnd.trim()) errs.push('窗口终点（UTC）未填写');
    else if (windowEndMs === null) errs.push('窗口终点无法解析为 UTC 时间（示例 2026-10-16T00:00）');
    if (windowStartMs !== null && windowEndMs !== null) {
      if (windowEndMs <= windowStartMs) errs.push('窗口终点必须晚于起点（后台为半开区间 [start, end)）');
      if (windowStartMs % LAB_WINDOW_GRID_MS !== 0) errs.push('窗口起点须对齐 15m 网格（分钟为 00/15/30/45）');
      if (windowEndMs % LAB_WINDOW_GRID_MS !== 0) errs.push('窗口终点须对齐 15m 网格（分钟为 00/15/30/45）');
      if (windowEndMs - windowStartMs < LAB_MIN_WINDOW_MS) errs.push('窗口跨度不足 1 天');
    }
    if (form.warmup.trim() !== '') {
      const wb = Number(form.warmup);
      if (!Number.isInteger(wb) || wb < 20) errs.push('预热根数须为 >=20 的整数（留空 = 后台自动）');
    }
    if (!form.symbol.trim()) errs.push('交易对未填写');
    return errs;
  }, [form.windowStart, form.windowEnd, form.warmup, form.symbol, windowStartMs, windowEndMs]);

  /** 解析表单参数（显示值 → 数值；空 = 沿用后台默认）。 */
  const parsedParams = useMemo(() => {
    const shown: Record<string, number> = {};
    const errs: string[] = [];
    for (const spec of LAB_PARAM_SPECS) {
      const raw = (form.params[spec.key] ?? '').trim();
      if (raw === '') continue;
      const n = Number(raw);
      if (!Number.isFinite(n)) {
        errs.push(`${spec.label}（${spec.key}）不是合法数值：${raw}`);
        continue;
      }
      shown[spec.key] = n;
    }
    errs.push(...labValidateParams(shown));
    return { shown, errs };
  }, [form.params]);

  /** 只发送**相对冻结默认有变化**的参数（真实覆盖）；全部参数的实际生效值由后台快照。 */
  const wireParams = useMemo(() => {
    const out: Record<string, number> = {};
    const overridden: string[] = [];
    for (const spec of LAB_PARAM_SPECS) {
      const n = parsedParams.shown[spec.key];
      if (n === undefined) continue;
      const wire = labToWire(spec, n);
      const def = (spec.scope === 'signal' ? defaults.data?.signal_params : defaults.data?.exec_params)?.[spec.key];
      if (def !== undefined && Math.abs(def - wire) < 1e-12) continue;
      out[spec.key] = wire;
      overridden.push(spec.key);
    }
    return { out, overridden };
  }, [parsedParams, defaults.data]);

  const blockingErrors = useMemo(
    () => [...windowIssues],
    [windowIssues, parsedParams.errs],
  );

  const symbolInPool = useMemo(
    () => poolSymbols.some((i) => i.symbol === form.symbol.trim().toUpperCase()),
    [poolSymbols, form.symbol],
  );

  const setParam = useCallback((key: string, v: string) => {
    setForm((f) => ({ ...f, params: { ...f.params, [key]: v } }));
  }, []);

  /** 提交状态来自该 run 自己的详情接口，与旧批次/全局列表解耦。 */
  const submittedStatus = submittedRunId ? (submittedDetailStatus ?? 'queued') : null;
  const submittedPending = submittedDetailPending;
  const runLocked = creating || submittedPending;

  const onSubmit = useCallback(async () => {
    if (runLocked) return; // 禁重复点击：本页 run 进入终态前不再受理
    setCreateErrors([]);
    setCreateNotice(null);
    if (blockingErrors.length > 0) {
      setCreateErrors(blockingErrors);
      return;
    }
    if (windowStartMs === null || windowEndMs === null) return;
    setCreating(true);
    try {
      const res = await labCreateRun({
        strategy_id: LAB_STRATEGY_ID,
        exchange: form.exchange,
        market: form.market,
        symbols: [form.symbol.trim().toUpperCase()],
        signal_timeframe: form.signalTf,
        exec_timeframe: form.execTf,
        window_start: toIsoUtcZ(windowStartMs),
        window_end: toIsoUtcZ(windowEndMs),
        params: {},
        note: 'GM 单策略工作台',
      });
      const rid = res.run_id;
      setCreateNotice(
        `已受理：run_id = ${rid} · status = ${res.status}（后台线程执行中；GET 只读不触发计算）。` +
          `沿用已批准 FT-LOW4H 配置，无参数覆盖。`,
      );
      if (rid) {
        setSubmittedRunId(rid);
        setSelectedRunId(rid);
        setBatchMode(false);
      }
      didPick.current = true;
      void list.reload();
      void defaults.reload();
    } catch (e: unknown) {
      setCreateErrors(labErrorsOf(e));
    } finally {
      setCreating(false);
    }
  }, [runLocked, blockingErrors, windowStartMs, windowEndMs, form, wireParams, list, defaults]);

  /* ── 指标卡 ── */
  const m = metrics.data?.metrics ?? null;
  const wins = m?.wins ?? null;
  /**
   * 指标卡数据。三处与旧版不同（《设计规范》第 10 节）：
   *   ① `tone` 取代原来的 `color`（Tailwind 类）—— 色调只由**数据方向**决定，
   *      缺失一律中性，不用颜色冒充方向；修饰类由令牌层给色值。
   *   ② `lead` 标出主指标（净收益 / 回撤），加左侧色条区分主次。
   *   ③ 平均盈亏比 / Profit Factor 改用 `fmtNum(..., 4)` 限长 ——
   *      旧版直接 `${m.avg_win_loss_ratio}` 会输出 1.29048909413528 把卡片撑变形。
   */
  const metricCards = useMemo(() => {
    const noWin = wins === 0;
    const toneOf = (v: number | null | undefined): 'pos' | 'neg' | 'muted' =>
      v === null || v === undefined || !Number.isFinite(v) ? 'muted' : v >= 0 ? 'pos' : 'neg';
    return [
      /* ── 主指标（4 张卡）：决定「这次回测值不值得继续往下看」 ── */
      {
        title: '净收益率',
        value: fmtPct(m?.net_return_pct, 4),
        sub: `净盈亏 ${fmtSignedUsd(m?.net_pnl, 2)} / 初始 ${fmtUsd(m?.initial_equity, 2)}`,
        tone: toneOf(m?.net_return_pct),
        lead: true,
        primary: true,
      },
      {
        title: '最大回撤',
        value: m?.max_drawdown_pct === null || m?.max_drawdown_pct === undefined ? '—' : `-${m.max_drawdown_pct.toFixed(4)}%`,
        sub: '峰值动态水线',
        tone: (m?.max_drawdown_pct === null || m?.max_drawdown_pct === undefined ? 'muted' : 'neg') as 'neg' | 'muted',
        lead: true,
        primary: true,
      },
      {
        title: '胜率',
        value: m?.win_rate_pct === null || m?.win_rate_pct === undefined ? '—' : `${m.win_rate_pct.toFixed(2)}%`,
        sub: m ? `盈 ${m.wins ?? '—'} / 亏 ${m.losses ?? '—'} / 平 ${m.flats ?? '—'}` : '后台未返回',
        tone: 'plain' as const,
        lead: false,
        primary: true,
      },
      {
        title: '交易次数',
        value: m?.trades_count === null || m?.trades_count === undefined ? '—' : String(m.trades_count),
        sub: m?.open_positions_remaining && m.open_positions_remaining.length > 0
          ? `期末未平仓 ${m.open_positions_remaining.length} 笔`
          : '已平仓笔数',
        tone: 'plain' as const,
        lead: false,
        primary: true,
      },
      /* ── 次要指标（紧凑条）：需要核对时再扫一眼，不占卡片 ── */
      {
        title: '平均盈亏比',
        value: fmtNum(m?.avg_win_loss_ratio, 4),
        sub:
          m?.avg_win_loss_ratio === null || m?.avg_win_loss_ratio === undefined
            ? noWin
              ? '无盈利单 ⇒ 未定义（不填假盈利比）'
              : '后台未给出'
            : '平均盈利 / 平均亏损（均值口径）',
        tone: 'plain' as const,
        lead: false,
        primary: false,
      },
      {
        title: 'Profit Factor',
        value: fmtNum(m?.profit_factor, 4),
        sub: noWin ? '0：无盈利单；Σ盈 / |Σ亏|（聚合口径）' : 'Σ盈 / |Σ亏|（聚合口径）',
        tone: 'plain' as const,
        lead: false,
        primary: false,
      },
      {
        title: '手续费',
        value: fmtUsd(m?.total_fees, 2),
        sub: `入场 ${fmtNum(m?.total_entry_fee, 2)} + 出场 ${fmtNum(m?.total_exit_fee, 2)}`,
        tone: 'muted' as const,
        lead: false,
        primary: false,
      },
      {
        title: '资金费',
        value: fmtUsd(m?.total_funding_fee, 2),
        sub: `${m?.funding_events_total ?? '—'} 笔真实结算事件（settled）`,
        tone: 'muted' as const,
        lead: false,
        primary: false,
      },
    ];
  }, [m, wins]);

  /** 「质量通过」的判定（与质量报告面板同一口径，缺一不可）。 */
  const qualityStatusOfRun = quality.data?.quality?.status ?? null;
  const qualityOkOfRun =
    !quality.error &&
    detailStatus === 'succeeded' &&
    qualityStatusOfRun === 'ok' &&
    quality.data?.data_quality != null;

  /**
   * 逐笔数据入口。
   * 【2026-10-18】必须在这里剔除 `null` 元素：`/trades` 的 `items` 实测可能含空元素，
   * 而下游有 5 处直接读 `t.entry_time_ms` / `t.trade_id`（选中、定位、时间轴、渲染），
   * 任何一处抛错都会被**页面级**错误边界接住 ⇒ 整个回测页替换成报错卡（列表和 K 线一起消失）。
   * 在入口过滤一次，比在 5 个下游各打一遍补丁可靠。
   */
  const tradeRows: LabTradeRow[] = useMemo(
    () => (trades.data?.items ?? []).filter((t): t is LabTradeRow => t != null),
    [trades.data],
  );
  /** 逐笔明细只显示**当前 run 自己账本里的成交**。
   *  旧实现的「全市场成交」读的是 `public/all_trades.json`（旧批次静态快照，70 行），
   *  与当前 run 不同源 ⇒ 已移除；跨 run 的批次视图改走 `BatchTradesTable`。 */
  const displayTrades: LabTradeRow[] = tradeRows;

  // 切换 run ⇒ 旧选中交易若不属于新 run，清空（等新 run 的逐笔到位后再自动选中）
  useEffect(() => {
    if (!selectedTradeId) return;
    const currentTrade = displayTrades.find((t) => t.trade_id === selectedTradeId);
    if (currentTrade && currentTrade.run_id && currentTrade.run_id !== selectedRunId) {
      setSelectedTradeId(null);
    }
  }, [selectedRunId, selectedTradeId, displayTrades]);

  /**
   * 「这一行能不能拿去查单笔详情」——只认**本 run 账本逐笔里真实存在**的 `trade_id`。
   *
   * 为什么必须硬校验（2026-10-18 故障）：`public/all_trades.json` 的 70 行
   * `trade_id` 全部写成了**订单号**（`ord_…`），而单笔详情接口只认 `trd_…`。
   * 于是「点实验记录 → 自动选中第一笔 → 请求 /trades/ord_xxx」必然 404，
   * 且会串到**另一个标的**的成交上（AEROUSDT 的 run 里显示了 METISUSDT 的订单号）。
   * 这里不猜、不兼容：不在本 run 逐笔里就返回 null，交给「自动选中本 run 第一笔」。
   */
  const queryableTradeId = useCallback(
    (row: LabTradeRow | undefined, runId: string | null): string | null => {
      const id = row?.trade_id;
      if (!id || !runId || row?.run_id !== runId) return null;
      return tradeRows.some((r) => r.trade_id === id) ? id : null;
    },
    [tradeRows],
  );

  // 当有成交明细且当前未选中任何交易时，自动选中**本 run** 的第一笔真实成交
  // （旧实现回退到 displayTrades[0] ⇒ 会选中别的标的的成交，再拿它的号去查本 run ⇒ 404）
  useEffect(() => {
    if (!selectedTradeId && displayTrades.length > 0) {
      const targetTrade = displayTrades.find(
        (t) => t.run_id === selectedRunId && !!queryableTradeId(t, selectedRunId),
      );
      if (targetTrade?.trade_id) {
        setSelectedTradeId(targetTrade.trade_id);
      }
    }
  }, [selectedRunId, displayTrades, selectedTradeId, queryableTradeId]);

  /**
   * 逐笔明细 → ③ 资金分析（水下回撤）联动载荷。
   * 只吃逐笔账本里的**真实** entry/exit 毫秒时间戳；缺失即不给定位（不推断、不用 K 线时刻顶替）。
   */
  const focusTrade = useMemo(() => {
    if (!selectedTradeId) return null;
    const t = displayTrades.find(
      (x) => x && x.trade_id === selectedTradeId && x.run_id === selectedRunId,
    );
    if (!t) return null;
    if (!Number.isFinite(t.entry_time_ms) || !Number.isFinite(t.exit_time_ms)) return null;
    return { tradeId: t.trade_id, entryMs: t.entry_time_ms, exitMs: t.exit_time_ms };
  }, [selectedTradeId, displayTrades]);

  /**
   * 逐笔明细行索引（真实 trade_id + 进出场毫秒）。
   * 图载荷 `chart.trades[].trade_id` 实测为 `null` ⇒ 执行视图靠它把 id 换回来，
   * 否则「选中高亮 / 图上点击定位」全失效。
   *
   * 【2026-10-18】`tradeRows` 里可能混进 `null` 元素（实测 `/trades` 返回
   * `{items:[null]}` 时 `t.entry_time_ms` 直接抛 TypeError ⇒ 整个回测页被页面级
   * 错误边界替换，列表与 K 线一起消失）。这里先剔除空元素再判时间戳。
   */
  const tradeIndex = useMemo(
    () =>
      (tradeRows ?? [])
        .filter((t) => t != null && Number.isFinite(t.entry_time_ms) && Number.isFinite(t.exit_time_ms))
        .map((t) => ({ tradeId: t.trade_id, entryMs: t.entry_time_ms, exitMs: t.exit_time_ms })),
    [tradeRows],
  );
  const equityPoints: LabEquityPoint[] = useMemo(() => equity.data?.items ?? [], [equity.data]);
  /** 研究期登记的 run：只按上游落盘成交重建，没有引擎写的净值序列 ⇒ 资金分析给「未记录」而不是「取数错误」。 */
  const isResearchRun = detail.data?.research_period === true;
  const chartPayload: LabChart | null = chart.data?.chart ?? null;
  const sharedGridTimes = useMemo(() => {
    const set = new Set<number>();
    if (chartPayload?.candles) {
      for (const c of chartPayload.candles) {
        const ms = Array.isArray(c) ? c[0] : c.t;
        set.add(Math.floor(ms / 1000));
      }
    }
    if (equityPoints && equityPoints.length > 0) {
      for (const p of equityPoints) {
        set.add(Math.floor(p.ts_ms / 1000));
      }
    }
    if (set.size === 0) return undefined;
    return Array.from(set).sort((a, b) => a - b);
  }, [chartPayload?.candles, equityPoints]);
  const orderRows: LabOrderRow[] = useMemo(() => orders.data?.items ?? [], [orders.data]);
  const fillRows: LabFillRow[] = useMemo(() => fills.data?.items ?? [], [fills.data]);
  const rejectedRows: LabRejectedRow[] = useMemo(() => rejected.data?.items ?? [], [rejected.data]);

  /** 导出数据助手 */
  const downloadFile = useCallback((filename: string, content: string, type: string) => {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  const exportOrdersCsv = useCallback(() => {
    if (!orderRows.length) return;
    const strat = detail.data?.engine?.version ?? 'WX_EVENT_TREND_V0_1';
    const header = [
      'run_id', 'strategy_version', 'symbol', 'signal_id', 'order_id', 'trade_id',
      'side', 'order_type', 'phase', 'status', 'action', 'price', 'qty', 'fee',
      'stop_price', 'reason', 'ts_ms', 'time_utc'
    ].join(',');
    const rows = orderRows.map((o) => [
      `"${o.run_id || selectedRunId || ''}"`,
      `"${strat}"`,
      `"${o.symbol || ''}"`,
      `"${o.signal_id || ''}"`,
      `"${o.order_id || ''}"`,
      `"${o.trade_id || ''}"`,
      `"${o.side || ''}"`,
      `"${(o as any).order_type || 'MARKET'}"`,
      `"${o.phase || ''}"`,
      `"${(o as any).status || 'FILLED'}"`,
      `"${o.action || ''}"`,
      o.price ?? '',
      o.qty ?? '',
      o.fee ?? '',
      o.stop_price ?? '',
      `"${o.reason || ''}"`,
      o.ts_ms ?? '',
      `"${fmtUtc(o.ts_ms)}"`,
    ].join(','));
    downloadFile(`orders_${selectedRunId}.csv`, [header, ...rows].join('\n'), 'text/csv;charset=utf-8;');
  }, [orderRows, selectedRunId, detail.data, downloadFile]);

  const exportFillsCsv = useCallback(() => {
    if (!fillRows.length) return;
    const strat = detail.data?.engine?.version ?? 'WX_EVENT_TREND_V0_1';
    const header = [
      'run_id', 'strategy_version', 'symbol', 'fill_id', 'order_id', 'trade_id',
      'action', 'fill_price', 'quantity', 'fee', 'liquidity', 'sequence',
      'fill_time_ms', 'fill_time_utc'
    ].join(',');
    const rows = fillRows.map((f) => [
      `"${f.run_id || selectedRunId || ''}"`,
      `"${strat}"`,
      `"${f.symbol || ''}"`,
      `"${f.fill_id || ''}"`,
      `"${f.order_id || ''}"`,
      `"${f.trade_id || ''}"`,
      `"${f.action || ''}"`,
      f.fill_price ?? '',
      f.quantity ?? '',
      f.fee ?? '',
      `"${f.liquidity || ''}"`,
      f.sequence ?? '',
      f.fill_time_ms ?? '',
      `"${fmtUtc(f.fill_time_ms)}"`,
    ].join(','));
    downloadFile(`fills_${selectedRunId}.csv`, [header, ...rows].join('\n'), 'text/csv;charset=utf-8;');
  }, [fillRows, selectedRunId, detail.data, downloadFile]);

  const exportTradesCsv = useCallback(() => {
    if (!displayTrades.length) return;
    const header = [
      'run_id', 'trade_id', 'symbol', 'direction', 'signal_id', 'entry_order_id',
      'exit_order_id', 'entry_time_ms', 'entry_time_utc', 'entry_price', 'qty',
      'exit_time_ms', 'exit_time_utc', 'exit_price', 'exit_reason', 'initial_stop',
      'net_pnl', 'gross_pnl', 'fees', 'funding_fee', 'holding_hours', 'mae', 'mfe'
    ].join(',');
    const rows = displayTrades.map((t) => [
      `"${selectedRunId || ''}"`,
      `"${t.trade_id || ''}"`,
      `"${t.symbol || ''}"`,
      `"${t.direction || 'long'}"`,
      `"${t.signal_id || ''}"`,
      `"${(t as any).entry_order_id || ''}"`,
      `"${(t as any).exit_order_id || ''}"`,
      t.entry_time_ms ?? '',
      `"${fmtUtc(t.entry_time_ms)}"`,
      t.entry_price ?? '',
      t.qty ?? '',
      t.exit_time_ms ?? '',
      `"${fmtUtc(t.exit_time_ms)}"`,
      t.exit_price ?? '',
      `"${t.exit_reason || ''}"`,
      t.initial_stop ?? '',
      t.net_pnl ?? '',
      (t as any).gross_pnl ?? '',
      t.fees ?? '',
      (t as any).funding_fee ?? '',
      t.holding_hours ?? '',
      (t as any).mae ?? '',
      (t as any).mfe ?? '',
    ].join(','));
    downloadFile(`trades_${selectedRunId}.csv`, [header, ...rows].join('\n'), 'text/csv;charset=utf-8;');
  }, [displayTrades, selectedRunId, downloadFile]);

  /**
   * 同一候选跨模式对照（FT-REPLAY-01）。
   *
   * 为什么单独一块：一个候选在 C0 / C1 / C2 各参数组下可能是**三种不同结果** ——
   * 成交 / 拒单 / 未成交。三者混成一个数字就等于把「没做」说成「做了亏」。
   * 事件 ID 取当前选中那一笔的 `event_id`（研究期回测的候选 ID 就落在这里）。
   */
  const candidateMatrix = trades.data?.candidate_matrix ?? null;
  const candidateMatrixNote = trades.data?.candidate_matrix_note ?? null;
  const candidateRows = useMemo(() => {
    if (!candidateMatrix) return null;
    const evId =
      displayTrades.find((t) => t != null && t.trade_id === selectedTradeId)?.event_id ?? null;
    if (!evId) return null;
    const rows = candidateMatrix[evId];
    if (!rows || rows.length === 0) return null;
    const cnt: Record<string, number> = { filled: 0, rejected: 0, not_filled: 0 };
    for (const r of rows) {
      const k = String(r.status ?? '');
      cnt[k] = (cnt[k] ?? 0) + 1;
    }
    return { evId, rows, cnt };
  }, [candidateMatrix, displayTrades, selectedTradeId]);

  /** 拒单原因聚合（按 `reason` 计数；仅统计真实返回的行，缺失不填 0）。 */
  const rejectedByReason = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of rejectedRows) {
      const k = r.reason || '（原因缺失）';
      out[k] = (out[k] ?? 0) + 1;
    }
    return Object.entries(out).sort((a, b) => b[1] - a[1]);
  }, [rejectedRows]);

  /** 资金费结算事件（chart 载荷内真实账本 funding 事件；与 /metrics 资金费合计同源）。 */
  const fundingEvents = useMemo(() => chartPayload?.funding_events ?? [], [chartPayload]);
  const fundingSum = useMemo(() => {
    if (fundingEvents.length === 0) return null;
    return fundingEvents.reduce((s, f) => s + (Number.isFinite(f.amount) ? f.amount : 0), 0);
  }, [fundingEvents]);

  const exportAllJson = useCallback(() => {
    const payload = {
      run_id: selectedRunId,
      exported_at_utc: new Date().toISOString(),
      strategy_version: String((detail.data?.engine as any)?.version || 'WX_EVENT_TREND_V0_1'),
      summary: (detail.data as any)?.summary ?? null,
      metrics: metrics.data?.metrics ?? null,
      trades: displayTrades,
      orders: orderRows,
      fills: fillRows,
      rejected: rejectedRows,
      funding: fundingEvents,
    };
    downloadFile(`backtest_${selectedRunId}_all.json`, JSON.stringify(payload, null, 2), 'application/json;charset=utf-8;');
  }, [selectedRunId, detail.data, metrics.data, displayTrades, orderRows, fillRows, rejectedRows, fundingEvents, downloadFile]);

  /** 运行列表新旧归属（来自列表响应 `retirement`；缺失 ⇒ 不推断）。 */
  const retirement: LabRetirementInfo | null = list.data?.retirement ?? null;
  const excludedLegacy = list.data?.excluded_legacy ?? [];

  /**
   * 逐笔为空时，用**本 run 自己的真实载荷**解释「为什么空」。
   *
   * 事件驱动策略的绝大多数 run 本来就不成交：4h 箱体没发生有效突破 ⇒ 无候选 ⇒ 无成交。
   * 这是链条**真实地停在了前面某一环**，不是「记录丢了」。区分这两件事，
   * 才能让「K 线 → 候选 → 逐笔」这条链在空结果时也看得懂。
   */
  const tradesEmptyHint = useMemo(() => {
    const nSig = (chartPayload?.signals ?? []).filter(
      (s) => Number.isFinite(s?.t) && s.t > 0,
    ).length;
    if (nSig > 0) {
      return `本 run 有 ${nSig} 个候选信号，但未触发成交（执行环为空）——链条止于「候选 → 执行」之间，不是记录缺失`;
    }
    return '本 run 无候选信号（4h 箱体未发生有效突破）⇒ 无逐笔成交。这是策略的真实产出，不是记录缺失';
  }, [chartPayload]);

  /** 该月平仓笔数（按**真实**平仓时间；仅作旧热力图的「补充事实」列，非收益口径）。 */
  const monthCloseCounts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const t of tradeRows) {
      if (!Number.isFinite(t.exit_time_ms)) continue;
      const d = new Date(t.exit_time_ms);
      const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      out[key] = (out[key] ?? 0) + 1;
    }
    return out;
  }, [tradeRows]);

  const formVersions = registry?.signal_versions ?? null;

  /* 「结果身份」面板显示的是**已生效参数**，不是表单里待运行的值。
   * 新契约把它放在 `config.config.actual_config`（外层 config 装 spec/config 两段），
   * 旧形状才在 `config.actual_config`。原先这里只读后者 ⇒ 后端明明返回
   * `initial_equity=2000 / fee_rate=0.0005 / slippage_bps=3`，面板却三格全显示「—」，
   * 看起来像"缺字段"，实际是路径少了一层。这里与 RunHeader / RunInfoCard
   * 用同一条兜底链，避免三处口径分叉。
   * 取不到就保持 null ⇒ 由 fmtUsd / fmtNum 渲染成「—」，不补默认值。 */
  const cfgWrap = (detail.data?.config ?? null) as Record<string, unknown> | null;
  const cfgInner = (cfgWrap?.['config'] ?? null) as Record<string, unknown> | null;
  const actualCfg = (cfgInner?.['actual_config'] ?? cfgWrap?.['actual_config'] ?? null) as Record<string, unknown> | null;

  return (
    <div className="ds-page" data-testid="lab-workbench">
      {/* ══ 结果身份：所选 run 的实际时间 / 配置 / 版本（不是表单未运行的值）══ */}
      {openPanel === 'identity' && selectedRunId && detail.data && (
        <SectionCard title="结果身份" testid="lab-run-identity">
          {/* 只留 4 个「对不上就会出事」的字段：run_id / 窗口 / 初始资金 / engine 版本 */}
          <div className="ds-kvgrid ds-kvgrid--4">
            <KV k="run_id" v={selectedRunId} />
            <KV k="窗口（UTC）" v={`${fmtUtc(detail.data.window?.win_start_ms)} → ${fmtUtc(detail.data.window?.win_end_ms)}`} />
            <KV k="初始资金（生效）" v={fmtUsd((actualCfg?.['initial_equity'] as number) ?? null, 2)} />
            <KV
              k="engine"
              v={`${(detail.data.engine?.['signal_version'] as string) ?? '—'} / ${(detail.data.engine?.['core_version'] as string) ?? '—'} / ${(detail.data.engine?.['exec_core_version'] as string) ?? '—'}`}
            />
          </div>

          <details className="ds-details" style={{ marginTop: 8 }} data-testid="lab-run-identity-more">
            <summary>其余身份字段（状态 / 时间 / 标的 / 周期 / 费率 / 滑点）</summary>
            <div className="ds-details__body">
              <div className="ds-kvgrid ds-kvgrid--4">
                <KV k="status" v={detail.data.status ?? '—'} />
                <KV k="created_at" v={fmtIsoUtc(detail.data.created_at)} />
                <KV k="finished_at" v={fmtIsoUtc(detail.data.finished_at)} />
                <KV
                  k="标的 / 所 / 市场"
                  v={`${detail.data.window?.symbol ?? '—'} · ${detail.data.window?.exchange ?? '—'} / ${detail.data.window?.market ?? '—'}`}
                />
                <KV
                  k="周期（信号 / 执行）"
                  v={`${detail.data.window?.signal_timeframe ?? '—'} / ${detail.data.window?.exec_timeframe ?? '—'}`}
                />
                <KV
                  k="窗口天数 / 预热"
                  v={`${fmtNum(detail.data.window?.window_days, 2)} 天 / ${detail.data.window?.warmup_bars ?? '—'} 根`}
                />
                <KV k="手续费率（生效）" v={String((actualCfg?.['fee_rate'] as number) ?? '—')} />
                <KV k="滑点 bps（生效）" v={String((actualCfg?.['slippage_bps'] as number) ?? '—')} />
              </div>
            </div>
          </details>

          {/* 审计明细：hash / 账本行数 / 原始参数 JSON / 输入快照 —— 默认折叠，不挡指标与曲线 */}
          <details className="ds-details" style={{ marginTop: 8 }} data-testid="lab-run-audit">
            <summary>代码 / 输入 hash · 账本行数 · 生效参数原始 JSON · 输入快照</summary>
            <div className="ds-details__body space-y-1">
              <div className="ds-kvgrid ds-kvgrid--3">
                <KV k="signal_params_hash" v={detail.data.hashes?.['signal_params_hash'] ?? '—'} />
                <KV k="exec_params_hash" v={detail.data.hashes?.['exec_params_hash'] ?? '—'} />
                <KV k="spec_sha256" v={detail.data.hashes?.['spec_sha256'] ?? '—'} />
                <KV k="code_sha256" v={detail.data.hashes?.['code_sha256'] ?? '—'} />
                <KV k="input_sha256" v={detail.data.hashes?.['input_sha256'] ?? '—'} />
                <KV k="signal_frame_sha256" v={detail.data.hashes?.['signal_frame_sha256'] ?? '—'} />
                <KV k="experiment_id" v={(detail.data.engine?.['experiment_id'] as string) ?? '—'} />
                <KV
                  k="账本行数"
                  v={detail.data.ledger_counts ? Object.entries(detail.data.ledger_counts).map(([k, v]) => `${k}=${v}`).join(' ') : '—'}
                />
              </div>
              {detail.data.config?.config?.signal_params && (
                <div className="text-[10px] text-neutral-400 font-mono break-all">
                  生效 signal_params：{JSON.stringify(detail.data.config.config.signal_params)}
                </div>
              )}
              {detail.data.config?.config?.exec_params && (
                <div className="text-[10px] text-neutral-400 font-mono break-all">
                  生效 exec_params：{JSON.stringify(detail.data.config.config.exec_params)}
                </div>
              )}
              {detail.data.snapshot_files && detail.data.snapshot_files.length > 0 && (
                <div className="text-[10px] text-neutral-400 font-mono break-all">
                  输入快照：{detail.data.snapshot_files.join(' · ')}
                </div>
              )}
            </div>
          </details>
        </SectionCard>
      )}

      {/* ══ ③a 指标条（8 格）：净收益 / 回撤 / 夏普 / 收益因子 / 成交笔数 / 胜率 / 盈亏比 / 总费用 ══
          设计稿 gm-layout.html 把这八张卡放在页面最上方。这里只在**选中 run** 时出现；
          每个数字都带口径副标，缺数据一律 `—`（夏普后端未算、总费用要求两项齐全）。 */}
      {selectedRunId && (
        <div data-lab-section="lab-metrics" style={{ marginBottom: 12 }}>
          <ErrorBoundary label="指标条" key={`bt-metrics-${selectedRunId}`}>
            <MetricStrip
              metrics={m}
              windowStartMs={detail.data?.window?.win_start_ms ?? null}
              windowEndMs={detail.data?.window?.win_end_ms ?? null}
              loading={metrics.loading}
            />
          </ErrorBoundary>
        </div>
      )}

      {/* ══ ③b 样本不足提示（设计稿 `.warn`）══════════════════════════════════
          设计稿在指标条下面放了一条浅黄横幅提醒「样本太少，结论不可靠」。
          这里同样只在**真的不够**时出现：成交笔数 < 30 且 > 0。
          数字全部来自当前 run（窗口天数取 window_days、笔数取 trades_count），
          缺任一项就不显示 —— 不猜、不写「约正负 N」这种算不出来的区间。 */}
      {selectedRunId &&
        typeof m?.trades_count === 'number' &&
        m.trades_count > 0 &&
        m.trades_count < 30 && (
          <div className="ds-warnbar" data-testid="bt-small-sample-warn">
            <b>样本太少，结论不可靠。</b>
            <span>
              {windowDaysOf(detail.data?.window) !== null
                ? `窗口 ${windowDaysOf(detail.data?.window)} 天、`
                : ''}
              {m.trades_count} 笔交易；不足 30 笔时，胜率与夏普都不能当作结论。
            </span>
          </div>
        )}

      {/* ══ ④ 回测复盘三栏（设计稿 `.bt3`：230 ｜ 自适应 ｜ 260）══════════════
          左＝历史回测配置 + 回测范围（批次 / 运行 / 新建）；
          中＝执行视图 + 资金分析；右＝成本与结果 / 数据与回放限制 / 复盘与样本外检查。
          三栏**常驻**：设计稿把「配置」放左栏、页首只留页头 ——
          不在页首再堆批次条与工具条（那些都在 10-18 被移走了）。 */}
      <div className="ds-bt-3col" data-lab-section="lab-3col">
        <div className="ds-bt-3col__left">
          <RunInfoCard run={null} detail={detail.data} metrics={m} chart={chartPayload} />
          {/* 回测范围（设计稿左栏第二张卡）：批次 / 取数口径 / 新建回测。
              批次汇总压成一行灰字 —— 设计稿的简洁靠「不加卡」，不靠删信息。 */}
          <section className="ds-card" data-testid="bt-scope-card">
            <div className="ds-card__head">
              <span className="ds-card__title">回测范围</span>
            </div>
            <div className="ds-card__body" style={{ paddingTop: 2 }}>
              <select
                className="ds-select"
                style={{ width: '100%' }}
                value={batchId ?? ''}
                onChange={(e) => setBatchId(e.target.value)}
                data-testid="bt-batch-select"
                disabled={batchesLoading && batches.length === 0}
                title="批次 = 一次批量运行的真实目录"
              >
                {batches.length === 0 ? (
                  <option value="">{batchesLoading ? '读取批次…' : '无批次目录'}</option>
                ) : (
                  batches.map((b) => (
                    <option key={b.batch_id} value={b.batch_id}>
                      {b.batch_id} · {b.runs_total} run
                    </option>
                  ))
                )}
              </select>

              <div className="ds-fldnote" data-testid="bt-batch-summary">
                {fmtInt(batchSummary?.batch?.runs_total)} 个 run ·
                有成交 {fmtInt(batchSummary?.batch?.runs_with_trades)} ·
                合计 {fmtInt(batchSummary?.batch?.trades_total)} 笔
                {batchSummary?.batch?.net_pnl_sum != null && (
                  <>
                    {' · 净盈亏 '}
                    <span
                      style={{
                        color:
                          batchSummary.batch.net_pnl_sum >= 0 ? 'var(--ds-pos)' : 'var(--ds-neg)',
                      }}
                    >
                      {batchSummary.batch.net_pnl_sum >= 0 ? '+' : '-'}$
                      {fmtNum(Math.abs(batchSummary.batch.net_pnl_sum), 2)}
                    </span>
                  </>
                )}
              </div>

              <div className="ds-seg" role="tablist" aria-label="取数口径" style={{ marginTop: 8 }}>
                {([[true, '按批次'], [false, '按单 run']] as [boolean, string][]).map(([v, label]) => (
                  <button
                    key={label}
                    type="button"
                    role="tab"
                    aria-selected={batchMode === v}
                    className="ds-seg__item"
                    onClick={() => setBatchMode(v)}
                    data-testid={`bt-batch-mode-${v ? 'batch' : 'run'}`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {(batchesError || batchSummaryError) && (
                <div className="ds-fldnote" style={{ color: 'var(--ds-neg)' }}>
                  {batchesError ? `批次列表失败：${batchesError}` : ''}
                  {batchSummaryError ? ` 汇总失败：${batchSummaryError}` : ''}
                </div>
              )}

              <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button
                  type="button"
                  className="ds-btn ds-btn--primary"
                  style={{ flex: 1 }}
                  aria-expanded={showRunForm}
                  onClick={() => setShowRunForm((v) => !v)}
                  data-testid="lab-run-form-toggle"
                >
                  {showRunForm ? '收起配置' : '运行回测'}
                </button>
                <button
                  type="button"
                  className="ds-btn ds-btn--secondary"
                  onClick={() => { setBatchNonce((n) => n + 1); void list.reload(); }}
                  title="重新读取批次与运行列表"
                >
                  <RefreshCw className={`w-3 h-3 ${list.loading ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>
          </section>

          {/* ══ 运行配置：设计稿里配置是「读」的，本应用要「填」——
              所以缺省收起，点「运行回测」才展开。 */}
          {showRunForm && (
        <div className="ds-card" data-testid="lab-config" data-lab-section="lab-config">
          <div className="ds-bar">
            <span className="ds-bar__label">运行配置</span>
            <label
              className="block w-32"
              title="单标的；建议取自工作台活动池，也可手工输入（后台按真实数据独立校验）"
            >
              <span className="text-[10px] text-neutral-500 flex items-center gap-1 whitespace-nowrap">
                交易对
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    pool.loading ? 'bg-neutral-400' : symbolInPool ? 'bg-emerald-500' : 'bg-amber-500'
                  }`}
                  title={
                    pool.loading
                      ? '活动池读取中'
                      : poolSymbols.length === 0
                        ? '池未取到'
                        : symbolInPool
                          ? '在活动池内'
                          : '不在活动池内（后台仍会独立校验）'
                  }
                />
              </span>
              <input
                list="lab-symbol-options"
                value={form.symbol}
                onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))}
                className="ds-input"
                data-testid="lab-symbol-input"
              />
              <datalist id="lab-symbol-options">
                {poolSymbols.map((i) => (
                  <option key={`${i.exchange}:${i.market_id}`} value={i.symbol} />
                ))}
              </datalist>
            </label>

            <div className="p-3 text-xs">FT-LOW4H 冻结基线：5000 USDT；初始止损 5%；入场立即跟踪上一根已收盘 4h 最低价下方 5%，只升不降；无 2R 门槛，无 240h 强退；OI 门槛关闭。</div>
          {false && showAdvanced && (
              <>
                <OptionRow
                  label="交易所"
                  value={form.exchange}
                  options={LAB_EXCHANGE_OPTIONS}
                  onChange={(v) => setForm((f) => ({ ...f, exchange: v }))}
                />
                <OptionRow
                  label="市场"
                  value={form.market}
                  options={LAB_MARKET_OPTIONS}
                  onChange={(v) => setForm((f) => ({ ...f, market: v }))}
                />
                <OptionRow
                  label="信号周期"
                  value={form.signalTf}
                  options={LAB_SIGNAL_TF_OPTIONS}
                  onChange={(v) => setForm((f) => ({ ...f, signalTf: v }))}
                />
                <OptionRow
                  label="执行周期"
                  value={form.execTf}
                  options={LAB_EXEC_TF_OPTIONS}
                  onChange={(v) => setForm((f) => ({ ...f, execTf: v }))}
                />
              </>
            )}

            <span className="ds-bar__sep" />

            <label className="block w-40" title="半开区间左端（含）；须对齐 15m 网格（分钟 00/15/30/45）">
              <span className="text-[10px] text-neutral-500">窗口起点 UTC</span>
              <input
                value={form.windowStart}
                onChange={(e) => setForm((f) => ({ ...f, windowStart: e.target.value }))}
                placeholder="2026-10-09T00:00"
                className="ds-input"
                data-testid="lab-window-start"
              />
            </label>
            <span className="text-neutral-400 text-[11px] pb-2">→</span>
            <label className="block w-40" title="半开区间右端（不含）；须对齐 15m 网格；跨度至少 1 天">
              <span className="text-[10px] text-neutral-500">窗口终点 UTC</span>
              <input
                value={form.windowEnd}
                onChange={(e) => setForm((f) => ({ ...f, windowEnd: e.target.value }))}
                placeholder="2026-10-16T00:00"
                className="ds-input"
                data-testid="lab-window-end"
              />
            </label>

            {showAdvanced && (
              <label className="block w-24" title="信号 bar 根数；留空 = 后台自动。预热段只喂数据不交易">
                <span className="text-[10px] text-neutral-500">预热根数</span>
                <input
                  value={form.warmup}
                  onChange={(e) => setForm((f) => ({ ...f, warmup: e.target.value }))}
                  placeholder="自动"
                  className="ds-input"
                />
              </label>
            )}

            <button
              type="button"
              onClick={() => void onSubmit()}
              disabled={runLocked || blockingErrors.length > 0}
              className="ds-btn ds-btn--primary"
              data-testid="lab-run-button"
            >
              <Play className="w-3.5 h-3.5" />
              {creating ? '提交中…' : submittedPending ? `运行中（${submittedStatus}）…` : '运行回测'}
            </button>

            <span className="ds-pagehead__spacer" />

            <button
              type="button"
              className="ds-btn ds-btn--ghost ds-btn--sm"
              aria-pressed={showAdvanced}
              onClick={() => setShowAdvanced((v) => !v)}
              data-testid="lab-advanced-toggle"
              title={
                formVersions
                  ? `信号 ${formVersions.signal ?? '—'} · 核心 ${formVersions.core ?? '—'} · 执行 ${formVersions.exec_core ?? '—'}`
                  : undefined
              }
            >
              {showAdvanced ? '收起高级设置' : '高级设置'}
            </button>
            <button
              type="button"
              onClick={() => void list.reload()}
              className="ds-btn ds-btn--secondary ds-btn--sm"
            >
              <RefreshCw className={`w-3 h-3 ${list.loading ? 'animate-spin' : ''}`} /> 刷新
            </button>
          </div>

          {submittedRunId && (
            <div
              className={`ds-note ${submittedPending ? 'ds-note--info' : ''}`}
              style={{ margin: '0 12px 8px' }}
              data-testid="lab-run-progress"
            >
              本页提交：<span className="font-mono">{submittedRunId}</span> · 状态 <b>{submittedStatus ?? '—'}</b>
              {submittedPending ? '（轮询中；进入终态前按钮保持禁用）' : '（已进入终态，可再次运行）'}
            </div>
          )}

          {/* ── 高级设置区（成本 / 风险与策略参数）── */}
          {showAdvanced && (
            <div className="ds-card__body" style={{ paddingTop: 0 }}>
          {/* ── 成本（高级设置内；单位与等价换算已标注）── */}
          <div className="pt-2.5 border-t border-neutral-100 flex flex-wrap items-end gap-x-3 gap-y-2">
              <span className="text-[10px] font-bold text-neutral-300 pb-1 shrink-0" title="参数留空 = 沿用后台冻结默认值">
                成本
              </span>
              {LAB_PARAM_SPECS.filter((s) => s.group === 'exec_cost').map((spec) => (
                <div key={spec.key} className="w-48">
                  <ParamField spec={spec} value={form.params[spec.key] ?? ''} onChange={(v) => setParam(spec.key, v)} />
                </div>
              ))}
            </div>

          {/* ── 风险与策略参数（高级设置内；标题行保留风险边界摘要）── */}
          <div className="mt-2.5 pt-2.5 border-t border-neutral-100">
            <button
              type="button"
              onClick={() => setShowSignalParams((s) => !s)}
              className="ds-btn ds-btn--ghost"
              style={{ width: '100%', justifyContent: 'flex-start', height: 'auto', padding: '4px 6px' }}
              data-testid="lab-params-toggle"
            >
              {showSignalParams ? '▾' : '▸'} 风险与策略参数（白名单校验）
              <span className="ml-2 font-normal text-neutral-500">
                单笔风险 {form.params['risk_per_trade'] || '—'}% · 组合上限 {form.params['max_portfolio_risk'] || '—'}% ·
                杠杆上限 {form.params['max_leverage_per_symbol'] || '—'}× · 本次覆盖 {wireParams.overridden.length} 项
              </span>
            </button>
            {showSignalParams && (
              <div className="mt-2 space-y-3">
                <div>
                  <div className="text-[11px] text-neutral-400 mb-1.5">风险 / 仓位（exec）</div>
                  <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
                    {LAB_PARAM_SPECS.filter((s) => s.group === 'exec_risk').map((spec) => (
                      <ParamField key={spec.key} spec={spec} value={form.params[spec.key] ?? ''} onChange={(v) => setParam(spec.key, v)} />
                    ))}
                  </div>
                </div>
                {(['sig_structure', 'sig_oi'] as const).map((g) => (
                  <div key={g}>
                    <div className="text-[11px] text-neutral-400 mb-1.5">
                      {g === 'sig_structure' ? '结构参数（唐奇安 / ATR / 回踩 / SC）' : 'OI 因子参数'}
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
                      {LAB_PARAM_SPECS.filter((s) => s.group === g).map((spec) => (
                        <ParamField key={spec.key} spec={spec} value={form.params[spec.key] ?? ''} onChange={(v) => setParam(spec.key, v)} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
            </div>
          )}

          {/* 提交后提示一律单行：全文进 title，信息不删；未点击提交时不空报校验错误避免首屏误警 */}
          {createErrors.length > 0 && (
            <div
              className="ds-note ds-note--neg"
              style={{ margin: '6px 12px 8px', alignItems: 'center', columnGap: 8, rowGap: 2 }}
              data-testid="lab-create-errors"
              title={createErrors.map((e, i) => `${i + 1}. ${e}`).join('\n')}
            >
              <span className="font-bold whitespace-nowrap">提交未通过</span>
              <span className="ds-truncate">{createErrors.join(' · ')}</span>
            </div>
          )}

          {createNotice && (
            <div className="ds-note ds-note--info" style={{ margin: '6px 12px 8px' }} data-testid="lab-create-notice">
              {createNotice}
            </div>
          )}
        </div>
          )}
        </div>
        <div className="ds-bt-3col__mid">
          {selectedRunId && detailStatus === 'succeeded' ? (
            <>
              <div data-lab-section="lab-chart">
                <LabChartPanel
                  chart={chartPayload}
                  loading={chart.loading}
                  error={chart.error}
                  missingReason={chart.data?.missing_reason ?? null}
                  selectedTradeId={selectedTradeId}
                  onSelectTrade={(tid) => {
                    setSelectedTradeId(tid);
                    if (tid && trades.data?.items) {
                      const hit = trades.data.items.find((t) => t.trade_id === tid);
                      if (hit?.symbol) setActiveSymbol(hit.symbol.replace('USDT', ''));
                    }
                  }}
                  orders={orderRows}
                  tradeIndex={tradeIndex}
                  crosshairBus={crosshairBus}
                  runId={selectedRunId}
                  strategyVersion={String((detail.data?.engine as any)?.version || 'WX_EVENT_TREND_V0_1')}
                  focusTimeMs={focusTimeMs}
                  selectedOrderId={selectedOrderId}
                  manualViewport={manualViewport}
                  onManualViewportChange={(fromMs, toMs) => setManualViewport({ fromMs, toMs })}
                  onSelectOrder={(oid) => {
                    setSelectedOrderId(oid);
                    setManualViewport(null);
                    if (oid) setOpenPanel('orders');
                    if (oid && orderRows) {
                      const hit = orderRows.find((o) => o.order_id === oid);
                      if (hit?.symbol) setActiveSymbol(hit.symbol.replace('USDT', ''));
                    }
                  }}
                  symbols={(chart.data?.chart as any)?.symbols ?? null}
                  activeSymbol={activeSymbol}
                  onSelectSymbol={(sym) => setActiveSymbol(sym)}
                  timeframe={activeTimeframe}
                  onTimeframeChange={(newTf) => setActiveTimeframe(newTf)}
                  gridTimes={sharedGridTimes}
                />
              </div>
              {/* 设计稿 `.g2`：净值曲线与回撤曲线**两张并排卡**，不是一张卡切页签。
                  两个实例读同一份权益序列，各自只画自己的那组线。 */}
              <div className="ds-g2" data-lab-section="lab-funding">
                <LabFundingPanel
                  variant="equity"
                  points={equityPoints}
                  benchmarkSeries={equity.data?.benchmark_equity ?? null}
                  benchmarkMeta={equity.data?.benchmark ?? metrics.data?.benchmark_buy_hold ?? null}
                  metrics={m}
                  monthly={null}
                  loading={equity.loading}
                  error={equity.error}
                  emptyHint={
                    isResearchRun
                      ? '未记录：研究期 run 按上游落盘成交重建，没有引擎落盘的净值序列（不绘制合成曲线）'
                      : null
                  }
                  monthCloseCounts={null}
                  windowStartMs={detail.data?.window?.win_start_ms ?? null}
                  windowEndMs={detail.data?.window?.win_end_ms ?? null}
                  focusTrade={focusTrade}
                  crosshairBus={crosshairBus}
                  gridTimes={sharedGridTimes}
                />
                <LabFundingPanel
                  variant="drawdown"
                  points={equityPoints}
                  benchmarkSeries={equity.data?.benchmark_equity ?? null}
                  benchmarkMeta={equity.data?.benchmark ?? metrics.data?.benchmark_buy_hold ?? null}
                  metrics={m}
                  monthly={null}
                  loading={equity.loading}
                  error={equity.error}
                  emptyHint={
                    isResearchRun
                      ? '未记录：研究期 run 按上游落盘成交重建，没有引擎落盘的净值序列（不绘制合成曲线）'
                      : null
                  }
                  monthCloseCounts={null}
                  windowStartMs={detail.data?.window?.win_start_ms ?? null}
                  windowEndMs={detail.data?.window?.win_end_ms ?? null}
                  focusTrade={focusTrade}
                  crosshairBus={crosshairBus}
                  gridTimes={sharedGridTimes}
                />
              </div>
            </>
          ) : (
            <section className="ds-card">
              <div className="ds-card__body ds-empty" data-testid="lab-chart-empty">
                左栏「回测范围」里选一个批次，或点「运行回测」新建一次运行。
              </div>
            </section>
          )}

          {/* 留存记录清单（设计稿中栏第 3 块）：一行 = 一次留存运行，点一行回放该 run。
              批次模式下就是本批次的 run 清单，与左栏汇总、下方逐笔同 batch_id。 */}
          <SectionCard
            title="留存记录清单"
            testid="lab-runs"
            section="lab-runs"
            right={
              <div className="flex items-center gap-2 text-[10px] font-mono">
                {/* BACKFILL-01：全量记录搜索（标的 / 运行 ID），本地过滤 */}
                <input
                  value={runQuery}
                  onChange={(e) => setRunQuery(e.target.value)}
                  placeholder="搜标的 / 运行 ID"
                  data-testid="lab-runs-search"
                  title="按标的或运行 ID 过滤记录（全量在内存里过滤，不用等网络）"
                  className="w-36 px-2 py-0.5 rounded bg-neutral-800 text-neutral-200 border border-neutral-700 outline-none focus:border-emerald-500/60 placeholder:text-neutral-500"
                />
                <button
                  type="button"
                  className={`px-2 py-0.5 rounded text-[10px] font-medium border transition-colors ${runsFilter === 'trades_only' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : 'bg-neutral-800 text-neutral-400 border-neutral-700'}`}
                  onClick={() => setRunsFilter((f) => (f === 'trades_only' ? 'all' : 'trades_only'))}
                >
                  {runsFilter === 'trades_only'
                    ? `仅显示有成交 (${displayedRuns.length})`
                    : `全部运行 (${displayedRuns.length})`}
                </button>
                <Chip>
                  共 {displayedRuns.length} 条
                  {runQuery.trim() || runsFilter === 'trades_only'
                    ? ` / 全部 ${batchMode ? batchRuns.length : runs.length} 条`
                    : ''}
                </Chip>
                <ColToggle on={!!allCols.runs} onToggle={() => toggleCols('runs')} total={10} />
                {!batchMode && activeRun && <Chip tone="info">轮询中：{activeRun.run_id} · {activeRun.status}</Chip>}
                {!batchMode && list.error && <span className="text-rose-600">列表取数错误：{list.error}</span>}
                {batchMode && batchRunsLoading && <span className="text-neutral-500">读取批次 run…</span>}
              </div>
            }
          >
            {/* 批次模式下这一行改成「本批次同源说明」：归属/退役是全局列表的概念，
                放在批次视图里会让人以为批次内也有旧策略 run。 */}
            {batchMode ? (
              <div
                className="text-[10px] text-neutral-500"
                style={{ marginBottom: 8, fontFamily: 'var(--ds-mono)' }}
                data-testid="lab-runs-batch-note"
                title="本列表 = 该批次目录内的 run（GET /batches/{id}/runs），与左栏汇总、下方整批逐笔同一个 batch_id。该接口不返回创建时间与最大回撤 ⇒ 显示 —（不猜）。"
              >
                批次 <b>{batchId ?? '—'}</b> 内的 run · 与左栏汇总同 batch_id
              </div>
            ) : (
            <details
              className="ds-details"
              style={{ marginBottom: 8 }}
              data-testid="lab-run-attribution"
            >
              <summary
                className={excludedLegacy.length > 0 ? 'text-amber-700' : undefined}
                title={
                  (retirement?.retired_strategy_keys
                    ? `已退役旧策略：${retirement.retired_strategy_keys.join(' / ')}${retirement.retired_at ? `（${retirement.retired_at}）` : ''}\n`
                    : '') +
                  (excludedLegacy.length > 0
                    ? `被排除的旧/身份存疑 run：${excludedLegacy.map((x) => x.run_id).join('、')}`
                    : '无被排除 run')
                }
              >
                归属：仅含当前策略 {retirement?.successor_strategy_id ?? LAB_STRATEGY_ID}
                {excludedLegacy.length > 0
                  ? ` · 已排除旧/存疑 run ${excludedLegacy.length} 条`
                  : ' · 无被排除 run'}
              </summary>
              <div className="ds-details__body text-[10px] text-neutral-400 space-y-0.5">
                <div>
                  已退役旧策略：
                  {retirement?.retired_strategy_keys?.length
                    ? `${retirement.retired_strategy_keys.join(' / ')}${retirement.retired_at ? `（${retirement.retired_at}）` : ''}`
                    : '—'}
                </div>
                <div>
                  被排除的旧 / 身份存疑 run：
                  {excludedLegacy.length > 0 ? excludedLegacy.map((x) => x.run_id).join('、') : '无'}
                  {excludedLegacy.length > 0 ? ' ⇒ 旧结果不得当成新策略成绩' : ''}
                </div>
              </div>
            </details>
            )}

            <div className="ds-tablewrap" style={{ maxHeight: '15rem' }}>
                <table className={`ds-table ds-table--clickable${allCols.runs ? ' ds-table--all' : ''}`}>
                  <thead>
                    <tr>
                      <th className="py-1.5 px-2">标的</th>
                      <th className="py-1.5 px-2">运行 ID</th>
                      <th className="py-1.5 px-2">状态</th>
                      <th className="py-1.5 px-2" data-secondary>归属</th>
                      <th className="py-1.5 px-2" data-secondary>创建时间（UTC）</th>
                      <th className="py-1.5 px-2 text-right">净收益</th>
                      <th className="py-1.5 px-2 text-right" data-secondary>回撤</th>
                      <th className="py-1.5 px-2 text-right">笔数</th>
                      <th className="py-1.5 px-2" data-secondary>配置 hash</th>
                      <th className="py-1.5 px-2" data-secondary>失败原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayedRuns.length === 0 ? (
                      <tr>
                        <td colSpan={allCols.runs ? 10 : 5} className="ds-table__empty">
                          {batchMode ? (batchRunsLoading ? '读取批次 run…' : '该批次没有 run 目录') : list.loading ? '读取中…' : '暂无实验记录'}
                        </td>
                      </tr>
                    ) : (
                      displayedRuns.map((r) => {
                        const sel = r.run_id === selectedRunId;
                        const h = hashMap[r.run_id];
                        const symbolText = (r.symbols ?? []).join(',') || '—';
                        const displayRunId = r.run_id.length > 24 
                          ? `${r.run_id.slice(0, 10)}...${r.run_id.slice(-8)}` 
                          : r.run_id;
                        return (
                          <tr
                            key={r.run_id}
                            data-run-id={r.run_id}
                            onClick={() => {
                              didPick.current = true;
                              setSelectedRunId(r.run_id);
                              /* 只选**该 run 账本里真实存在**的成交号：先清空，
                                 等新 run 的逐笔到位后由「自动选中本 run 第一笔」接管。
                                 不再读 `all_trades.json`（旧批次快照，会串标的）。 */
                              setSelectedTradeId(null);
                            }}
                            aria-selected={sel}
                          >
                            <td className="py-1.5 px-2 font-semibold text-emerald-400 whitespace-nowrap">{symbolText}</td>
                            <td className="py-1.5 px-2 text-neutral-300 font-mono text-xs whitespace-nowrap" title={r.run_id}>
                              {displayRunId}
                            </td>
                            <td className="py-1.5 px-2">
                              {/* CODE-REVIEW-02：状态标签走全站统一的 statusLabel
                                  （unknown=未登记 / interrupted=已中断，不再裸显示 succeeded）；
                                  registered===false 的历史批量产物附「未登记」小徽标。 */}
                              <span className={`ds-badge ${statusTone(r.status)}`}>
                                {statusLabel(r.status)}
                              </span>
                              {r.registered === false && (
                                <span
                                  className="ds-badge ds-badge--neutral"
                                  style={{ marginLeft: 4 }}
                                  title={r.record_note ?? '该运行无 run.json：历史批量产物，未登记（只读参考）'}
                                  data-testid="lab-run-unregistered"
                                >
                                  未登记
                                </span>
                              )}
                              {/* FT-REPLAY-01：研究期结果必须一眼看出来 ——
                                  它是上游回测按落盘 CSV 重建的，不是本引擎跑出来的成绩。
                                  放在状态徽标旁（默认可见列），不用展开「全部列」才看得到。 */}
                              {r.research_period && (
                                <span
                                  className="ds-badge ds-badge--warn"
                                  style={{ marginLeft: 4 }}
                                  title={(r.registration && r.registration.note)
                                    || '研究期结果：由上游落盘 CSV 重建，非本引擎重跑；只读复盘用，不进生产订单 / 模拟实盘账本'}
                                  data-testid="lab-run-research"
                                >
                                  研究期
                                </span>
                              )}
                            </td>
                            <td className="py-1.5 px-2" data-secondary>
                              {r.attribution === 'research_period' ? (
                                <span className="ds-badge ds-badge--warn">
                                  研究期重建
                                </span>
                              ) : r.attribution === 'current_strategy' ? (
                                <span className="ds-badge ds-badge--pos">
                                  当前策略
                                </span>
                              ) : (
                                <span className="ds-badge ds-badge--warn">
                                  身份存疑
                                </span>
                              )}
                            </td>
                            <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap" data-secondary>{fmtIsoUtc(r.created_at)}</td>
                            <td className={`py-1.5 px-2 text-right ${(r.net_return_pct ?? 0) >= 0 ? 'text-emerald-500 font-medium' : 'text-rose-500 font-medium'}`}>
                              {fmtPct(r.net_return_pct, 4)}
                            </td>
                            <td className="py-1.5 px-2 text-right text-rose-500 font-medium" data-secondary>
                              {r.max_drawdown_pct === null || r.max_drawdown_pct === undefined ? '—' : `-${r.max_drawdown_pct.toFixed(4)}%`}
                            </td>
                            <td className="py-1.5 px-2 text-right">
                              {(r.trades_count ?? 0) > 0 ? (
                                <span className="font-bold text-amber-400 bg-amber-950/40 px-1.5 py-0.5 rounded border border-amber-800/60">
                                  {r.trades_count} 笔
                                </span>
                              ) : (
                                <span className="text-neutral-500">0</span>
                              )}
                            </td>
                            <td className="py-1.5 px-2 text-neutral-500" data-secondary title={h?.spec_sha256 ?? '未取到'}>
                              {h ? shortHash(h.spec_sha256, 10) : '—'}
                            </td>
                            <td className="py-1.5 px-2 text-rose-600 max-w-[16rem] truncate" data-secondary title={r.failure_reason ?? undefined}>
                              {r.failure_reason ?? '—'}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
                <details className="ds-details" style={{ marginTop: 6 }} data-testid="lab-runs-hashnote">
                  <summary>「配置 hash」列为何可能为 —（端点缺口说明）</summary>
                  <div className="ds-details__body text-[10px] text-neutral-400">
                    列表端点 `GET /backtests` 的 summary <b>不含配置 hash</b>（精确缺口：`backtest_api._summary()` 未带
                    `spec_sha256`/`*_params_hash`）⇒ 这里用只读 `GET /backtests/{'{id}'}` 补齐（并发上限 4，最多 30 条）；
                    未取到的显示 —，不伪造。
                  </div>
                </details>
            </div>

            {metrics.data?.m4_disclaimer && (
              /* 边界声明：整段黄色文字太占视野 ⇒ 保留一行结论常显，全文一键展开（信息不删） */
              <details className="ds-details" style={{ marginTop: 8 }} data-testid="lab-m4-disclaimer">
                <summary>边界声明 · 本工作台输出不代表正式 M4 通过（展开全文）</summary>
                <div className="ds-details__body text-[10px] text-amber-800">{metrics.data.m4_disclaimer}</div>
              </details>
            )}
          </SectionCard>

          {/* 单条记录回放（设计稿中栏第 4 块）：当前 run 的逐笔，点一行 ⇒ 图表定位 + 单笔详情。 */}
          {selectedRunId && detailStatus === 'succeeded' && (
            <SectionCard
              title="单条记录回放"
              testid="lab-trades"
              section="lab-trades"
              right={
                <div className="flex items-center gap-2 text-[10px] font-mono">
                  <Chip>本 run 共 {displayTrades.length} 笔</Chip>
                  <ColToggle on={!!allCols.trades} onToggle={() => toggleCols('trades')} total={21} />
                  {displayTrades.length > 0 && (
                    <>
                      <button
                        type="button"
                        className="px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700 transition-colors"
                        onClick={exportTradesCsv}
                        title="导出本 run 逐笔交易记录为 CSV"
                      >
                        导出逐笔 CSV
                      </button>
                      <button
                        type="button"
                        className="px-2 py-0.5 rounded bg-indigo-900/60 hover:bg-indigo-800/80 text-indigo-200 border border-indigo-700/60 transition-colors"
                        onClick={exportAllJson}
                        title="导出本 run 完整三层数据（订单/成交/汇总）为 JSON"
                      >
                        导出完整 JSON
                      </button>
                    </>
                  )}
                  {trades.error && <span className="text-rose-600">逐笔取数错误：{trades.error}</span>}
                </div>
              }
            >
            <div className="ds-tablewrap" style={{ maxHeight: '17.25rem' }} title="点击任意行 ⇒ 执行视图定位该笔 + ③ 资金分析切到「水下回撤」并定位该笔区间">
              <table className={`ds-table ds-table--clickable${allCols.trades ? ' ds-table--all' : ''}`}>
                <thead>
                  <tr>
                    <th className="py-1.5 px-2" title="候选信号时刻（chart.signals[].t，UTC）">信号时间 (UTC)</th>
                    <th className="py-1.5 px-2" data-secondary title="确认 K 线收盘时刻（chart.confirms[].t，UTC）；C0 模式无独立确认环节 ⇒ 无此环节">确认时间 (UTC)</th>
                    <th className="py-1.5 px-2" data-secondary title="下单即成交时刻（账本 order_time_ms，UTC）；账本没记就是「未记录」，不用进场时间顶替">下单时间 (UTC)</th>
                    <th className="py-1.5 px-2" data-secondary title="真实成交时刻（账本 trades.jsonl，UTC）">进场时间 (UTC)</th>
                    <th className="py-1.5 px-2" data-secondary>出场时间 (UTC)</th>
                    <th className="py-1.5 px-2">标的</th>
                    <th className="py-1.5 px-2">方向</th>
                    <th className="py-1.5 px-2 text-right">开仓价</th>
                    <th className="py-1.5 px-2 text-right" data-secondary title="实际仓位金额（stake_amount）">仓位金额</th>
                    <th className="py-1.5 px-2 text-right" data-secondary title="箱体/扫单基准价 P0（来自候选确认表）">原始 P0</th>
                    <th className="py-1.5 px-2 text-right" data-secondary title="入场时计划风险（USDT）；上游未落盘 ⇒ 未知，不拿兜底止损值推算">计划风险</th>
                    <th className="py-1.5 px-2 text-right" title="账本 stop_trace 首点（引擎实况，非重算）">初始止损</th>
                    <th className="py-1.5 px-2 text-right" data-secondary>出场价</th>
                    <th className="py-1.5 px-2 text-right" data-secondary>数量</th>
                    <th className="py-1.5 px-2 text-right" data-secondary>毛盈亏</th>
                    <th className="py-1.5 px-2 text-right" data-secondary>双边手续费</th>
                    <th className="py-1.5 px-2 text-right" data-secondary>资金费</th>
                    <th className="py-1.5 px-2 text-right">净盈亏</th>
                    <th className="py-1.5 px-2 whitespace-nowrap">退出原因</th>
                    <th className="py-1.5 px-2" data-secondary>trade_id</th>
                    <th className="py-1.5 px-2" data-secondary>候选 ID</th>
                  </tr>
                </thead>
                <tbody>
                  {displayTrades.length === 0 ? (
                    <tr>
                      <td colSpan={allCols.trades ? 21 : 7} className="ds-table__empty" data-testid="lab-trades-empty">
                        {trades.loading ? '读取中…' : tradesEmptyHint}
                      </td>
                    </tr>
                  ) : (
                    displayTrades.map((t, rowIdx) => {
                      const sel = t.trade_id === selectedTradeId;
                      const dirStr = String(t.direction ?? '');
                      const isLong = dirStr === 'long' || dirStr === '1' || dirStr === 'buy';
                      return (
                        <tr
                          /* key 用 run_id + trade_id + 行号：账本里同一 run 理论上 trade_id 唯一，
                             但历史批次出现过重复/缺失 ⇒ 加行号兜底，避免 React 吞行。 */
                          key={`${t.run_id ?? ''}|${t.trade_id ?? t.order_id ?? ''}|${rowIdx}`}
                          data-trade-id={t.trade_id ?? undefined}
                          onClick={() => {
                            if (t.run_id && t.run_id !== selectedRunId) {
                              didPick.current = true;
                              setSelectedRunId(t.run_id);
                              /* 跨 run：此刻 tradeRows 还是旧 run 的，先不选，
                                 等新 run 的逐笔到位后由「自动选中」接管（避免串号 404）。 */
                              setSelectedTradeId(null);
                              return;
                            }
                            setSelectedTradeId(queryableTradeId(t, selectedRunId));
                            setManualViewport(null);
                            if (t.entry_time_ms) setFocusTimeMs(t.entry_time_ms);
                            if (t.symbol) setActiveSymbol(t.symbol.replace('USDT', ''));
                          }}
                          aria-selected={sel}
                        >
                          <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap" title={t.signal_time_source ?? undefined}>
                            {fmtUtc(t.signal_time_ms)}
                          </td>
                          {/* 确认 K 线收盘：C0 没有独立确认环节 ⇒ 写「无此环节」；
                              其余模式确实没记到才写「未知」。两者不能都写成 —。 */}
                          <td
                            className="py-1.5 px-2 text-neutral-500 whitespace-nowrap"
                            data-secondary
                            title={t.confirm_time_source ?? '账本无确认时刻'}
                          >
                            {t.confirm_time_ms
                              ? fmtUtc(t.confirm_time_ms)
                              : (t.mode ?? '').toUpperCase() === 'C'
                                ? '无此环节'
                                : '未知'}
                          </td>
                          {/* 下单时间：账本没落盘就写「未记录」，绝不拿进场时间顶替 */}
                          <td
                            className="py-1.5 px-2 text-neutral-500 whitespace-nowrap"
                            data-secondary
                            title={t.order_time_source ?? '账本无下单时刻（不用进场时间替代）'}
                          >
                            {t.order_time_ms ? fmtUtc(t.order_time_ms) : '未记录'}
                          </td>
                          <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap" data-secondary>{fmtUtc(t.entry_time_ms)}</td>
                          <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap" data-secondary>{fmtUtc(t.exit_time_ms)}</td>
                          <td className="py-1.5 px-2 font-bold text-neutral-100">{t.symbol}</td>
                          <td className="py-1.5 px-2">
                            <span className={`ds-badge ${isLong ? 'ds-badge--pos' : 'ds-badge--neg'}`}>
                              {isLong ? '做多' : '做空'}
                            </span>
                          </td>
                          <td className="py-1.5 px-2 text-right text-neutral-300">{fmtNum(t.entry_price, 4)}</td>
                          <td className="py-1.5 px-2 text-right" data-secondary title={t.stake_amount_source ?? '账本无仓位金额'}>
                            {t.stake_amount == null ? '未知' : fmtNum(t.stake_amount, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-right" data-secondary title={t.P0_source ?? '候选表无 P0'}>
                            {t.P0 == null ? '未知' : fmtNum(t.P0, 6)}
                          </td>
                          <td className="py-1.5 px-2 text-right" data-secondary title={t.planned_risk_source ?? '上游未落盘计划风险'}>
                            {t.planned_risk_usdt == null ? '未知' : fmtNum(t.planned_risk_usdt, 2)}
                          </td>
                          <td
                            className="py-1.5 px-2 text-right text-amber-600"
                            title={t.initial_stop_source ?? '账本无止损轨迹点（不合成、不用 -99% 兜底值顶替）'}
                          >
                            {t.initial_stop == null ? '未记录' : fmtNum(t.initial_stop, 4)}
                          </td>
                          <td className="py-1.5 px-2 text-right text-neutral-100 font-semibold" data-secondary>{fmtNum(t.exit_price, 4)}</td>
                          <td className="py-1.5 px-2 text-right" data-secondary>{fmtNum(t.qty, 6)}</td>
                          <td
                            className="py-1.5 px-2 text-right"
                            data-secondary
                            title={t.gross_pnl == null ? '上游只落净盈亏，未落毛盈亏（不拿净盈亏倒推）' : undefined}
                          >
                            {t.gross_pnl == null ? '未记录' : fmtSignedUsd(t.gross_pnl, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-right text-neutral-400" data-secondary>{fmtUsd(t.fees, 2)}</td>
                          <td className="py-1.5 px-2 text-right text-neutral-400" data-secondary>{fmtUsd(t.funding_fee, 2)}</td>
                          <td className={`py-1.5 px-2 text-right font-bold ${t.net_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                            {fmtSignedUsd(t.net_pnl, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-neutral-400 whitespace-nowrap" title={t.exit_reason ?? undefined}>
                            {exitReasonLabel(t.exit_reason)}
                          </td>
                          <td className="py-1.5 px-2 text-neutral-400" data-secondary>{t.trade_id ?? '—'}</td>
                          {/* 候选 ID：正式回测走 signal_id（cand_…）；研究期回测没有 cand_ 前缀，
                              上游落盘的是 event_id（ev_…）⇒ 两者都认，都没有才写 —。 */}
                          <td
                            className="py-1.5 px-2 text-neutral-500"
                            data-secondary
                            title={t.signal_id ?? t.event_id ?? undefined}
                          >
                            {t.signal_id
                              ? t.signal_id.replace('cand_ev_WX_EVENT_TREND_V0_1_binance_', '…_')
                              : (t.event_id ?? '—')}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            <details className="ds-details" style={{ marginTop: 8 }} open={false} data-testid="lab-trade-detail-fold">
              <summary>单笔详情（选中某一笔后展开）</summary>
              <div className="ds-details__body">
                <LabTradeDetailPanel
                  detail={tradeDetail.data}
                  loading={tradeDetail.loading}
                  error={tradeDetail.error}
                />
              </div>
            </details>

            {/* 同一候选在 C0 / C1 / C2 的结果对照：成交 / 拒单 / 未成交分开计数、分开列。
                没有候选事件 ID（正式回测）或该候选不在索引里 ⇒ 不渲染，不编造一行。 */}
            {candidateRows && (
              <details className="ds-details" style={{ marginTop: 8 }} open={false} data-testid="lab-candidate-matrix">
                <summary>
                  同一候选在 C0 / C1 / C2 的结果对照
                  <span className="ds-card__hint">
                    成交 {candidateRows.cnt.filled ?? 0} · 拒单 {candidateRows.cnt.rejected ?? 0} · 未成交{' '}
                    {candidateRows.cnt.not_filled ?? 0}
                  </span>
                </summary>
                <div className="ds-details__body">
                  <div className="text-[10px] text-neutral-400" style={{ marginBottom: 6 }}>
                    候选事件 <span className="font-mono">{candidateRows.evId}</span>
                    {candidateMatrixNote ? ` · ${candidateMatrixNote}` : ''}
                  </div>
                  <div className="ds-tablewrap" style={{ maxHeight: '13rem' }}>
                    <table className="ds-table">
                      <thead>
                        <tr>
                          <th className="py-1.5 px-2">模式</th>
                          <th className="py-1.5 px-2">参数组</th>
                          <th className="py-1.5 px-2">结果</th>
                          <th className="py-1.5 px-2 text-right">净盈亏</th>
                          <th className="py-1.5 px-2">入场时间 (UTC)</th>
                          <th className="py-1.5 px-2">出场原因 / 拒单原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {candidateRows.rows.map((r, i) => {
                          const st = String(r.status ?? '');
                          const stLabel = st === 'filled' ? '成交' : st === 'rejected' ? '拒单' : st === 'not_filled' ? '未成交' : '未知';
                          const stCls =
                            st === 'filled' ? 'ds-badge--pos' : st === 'rejected' ? 'ds-badge--neg' : '';
                          return (
                            <tr key={`${r.mode ?? ''}|${r.strategy ?? ''}|${i}`} data-testid="lab-candidate-row">
                              <td className="py-1.5 px-2 font-bold text-neutral-100">{r.mode ?? '未知'}</td>
                              <td className="py-1.5 px-2 text-neutral-300 font-mono">{r.strategy ?? '未知'}</td>
                              <td className="py-1.5 px-2">
                                <span className={`ds-badge ${stCls}`}>{stLabel}</span>
                              </td>
                              <td
                                className={`py-1.5 px-2 text-right ${
                                  r.net_pnl == null
                                    ? 'text-neutral-400'
                                    : r.net_pnl >= 0
                                      ? 'text-emerald-600'
                                      : 'text-rose-600'
                                }`}
                              >
                                {r.net_pnl == null ? '—' : fmtSignedUsd(r.net_pnl, 2)}
                              </td>
                              <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">
                                {r.entry_time_ms == null ? '—' : fmtUtc(r.entry_time_ms)}
                              </td>
                              <td className="py-1.5 px-2 text-neutral-400">
                                {r.status === 'filled'
                                  ? exitReasonLabel(r.exit_reason)
                                  : (r.reason ?? '未记录')}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            )}
            </SectionCard>
          )}

          {/* 整批逐笔（跨 run）：设计稿没有这张表 ⇒ 只留一行折叠，展开才需要。
              与上方「单条记录回放」的区别：那个只看当前 run；这里看整批，并带 run_id /
              候选 ID / 信号时间 / 初始止损。两者都直接读账本，没有静态快照。
              面板级兜底：批次逐笔是跨 run 拼出来的一张表，脏行风险最高；
              key 绑 batchId ⇒ 切批次自动重置。 */}
      {batchMode && batchId && (
        <details className="ds-foldcard" data-testid="bt-batch-trades-fold">
          <summary>
            整批逐笔（跨 run）
            <span className="ds-card__hint">本批次全部 run 的真实成交 · 点一行定位该笔</span>
          </summary>
          <ErrorBoundary label="整批逐笔" key={`bt-batch-${batchId}`}>
        <BatchTradesTable
          trades={batchTrades}
          loading={batchTradesLoading}
          error={batchTradesError}
          missingInitialStop={batchTradesMeta.missingInitialStop}
          unlinkedSignalTime={batchTradesMeta.unlinkedSignalTime}
          selectedRunId={selectedRunId}
          selectedTradeId={selectedTradeId}
          onLocate={(runId, tradeId) => {
            didPick.current = true;
            setSelectedRunId(runId);
            // 目标 run 的逐笔还没到位 ⇒ 先清空，由「自动选中本 run 第一笔」接管；
            // 若目标就是当前 run，直接选中该笔（图表随即定位入场前后）。
            if (runId === selectedRunId && tradeId) {
              setSelectedTradeId(tradeId);
            } else {
              setSelectedTradeId(null);
            }
          }}
          />
          </ErrorBoundary>
        </details>
      )}
        </div>
        <div className="ds-bt-3col__right">
          {selectedRunId && detailStatus === 'succeeded' && (
            <BacktestSidePanel
              metrics={m}
              detail={detail.data}
              chart={chartPayload}
              qualityOk={qualityOkOfRun}
              qualityStatus={qualityStatusOfRun}
              missingReason={chart.data?.missing_reason ?? null}
            />
          )}
        </div>
      </div>


      {/* ══ 更多视图（按需展开，单选）：从页首工具条搬到这里 ══
          设计稿没有这一排；它是审计面板的入口，放在面板正上方最不占视觉。 */}
      {selectedRunId && detailStatus === 'succeeded' && (
        <div className="ds-viewbar" data-testid="lab-viewbar">
          <span className="ds-viewbar__label">更多视图</span>
          {(
            [
              ['identity', '结果身份'],
              ['orders', `委托订单${orderRows.length ? ` (${orderRows.length})` : ''}`],
              ['fills', `逐次成交${fillRows.length ? ` (${fillRows.length})` : ''}`],
              ['rejected', `拒单明细${rejectedRows.length ? ` (${rejectedRows.length})` : ''}`],
              ['funding', `资金费结算${fundingEvents.length ? ` (${fundingEvents.length})` : ''}`],
              ['audit', '口径与审计'],
            ] as [string, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className="ds-btn ds-btn--ghost ds-btn--sm"
              aria-pressed={openPanel === key}
              onClick={() => togglePanel(key)}
              data-testid={`lab-panel-${key}`}
            >
              {openPanel === key ? '▾ ' : '▸ '}
              {label}
            </button>
          ))}
        </div>
      )}

      {/* ══ ⑤b 全局订单 / 委托明细（按需展开）══ */}
      {openPanel === 'orders' && selectedRunId && detailStatus === 'succeeded' && (
        <SectionCard
          title="⑤b 委托订单明细"
          testid="lab-orders"
          sub
          right={
            <div className="flex items-center gap-2 text-[10px] font-mono">
              <Chip>共 {orderRows.length} 行</Chip>
              <ColToggle on={!!allCols.orders} onToggle={() => toggleCols('orders')} total={11} />
              {orderRows.length > 0 && (
                <button
                  type="button"
                  className="px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700 transition-colors"
                  onClick={exportOrdersCsv}
                  title="导出委托订单为 CSV"
                >
                  导出订单 CSV
                </button>
              )}
              {orders.error && <span className="text-rose-600">订单取数错误：{orders.error}</span>}
              {orders.data?.missing_reason && <span className="text-amber-700">{orders.data.missing_reason}</span>}
            </div>
          }
        >
          <div className="ds-tablewrap" style={{ maxHeight: '18rem' }}>
            <table className={`ds-table ds-table--clickable${allCols.orders ? ' ds-table--all' : ''}`}>
              <thead>
                <tr>
                  <th className="py-1.5 px-2">时点</th>
                  <th className="py-1.5 px-2">phase</th>
                  <th className="py-1.5 px-2" data-secondary>side</th>
                  <th className="py-1.5 px-2">action</th>
                  <th className="py-1.5 px-2">标的</th>
                  <th className="py-1.5 px-2 text-right">价格</th>
                  <th className="py-1.5 px-2 text-right" data-secondary>数量</th>
                  <th className="py-1.5 px-2 text-right" data-secondary>手续费</th>
                  <th className="py-1.5 px-2 text-right" data-secondary>止损价</th>
                  <th className="py-1.5 px-2" data-secondary>trade_id</th>
                  <th className="py-1.5 px-2">原因</th>
                </tr>
              </thead>
              <tbody>
                {orderRows.length === 0 ? (
                  <tr>
                    <td colSpan={allCols.orders ? 11 : 6} className="ds-table__empty">
                      {orders.loading ? '读取中…' : '无真实订单记录（账本 orders 为空）——不显示任何演示数据'}
                    </td>
                  </tr>
                ) : (
                  orderRows.map((o) => {
                    const isSelected = selectedOrderId === o.order_id;
                    return (
                      <tr
                        key={o.order_id}
                        data-testid={`lab-order-row-${o.order_id}`}
                        data-order-id={o.order_id}
                        data-ts-ms={o.ts_ms}
                        data-selected={isSelected ? 'true' : 'false'}
                        className={`hover:bg-neutral-800/50 cursor-pointer transition-colors ${
                          isSelected ? 'bg-indigo-950/80 ring-1 ring-indigo-500 font-semibold' : ''
                        }`}
                        title="点击该订单 ⇒ 执行图表定位并高亮发生时点"
                        onClick={() => {
                          setSelectedOrderId(o.order_id);
                          setManualViewport(null);
                          if (o.ts_ms) setFocusTimeMs(o.ts_ms);
                          if (o.trade_id) setSelectedTradeId(o.trade_id);
                          if (o.symbol) setActiveSymbol(o.symbol.replace('USDT', ''));
                        }}
                      >
                      <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">{fmtUtc(o.ts_ms)}</td>
                      <td className="py-1.5 px-2">{o.phase}</td>
                      <td className="py-1.5 px-2" data-secondary>{o.side}</td>
                      <td className="py-1.5 px-2">{o.action}</td>
                      <td className="py-1.5 px-2 font-bold text-neutral-100">{o.symbol}</td>
                      <td className="py-1.5 px-2 text-right">{fmtNum(o.price, 4)}</td>
                      <td className="py-1.5 px-2 text-right" data-secondary>{fmtNum(o.qty, 6)}</td>
                      <td className="py-1.5 px-2 text-right" data-secondary>{fmtNum(o.fee, 6)}</td>
                      <td className="py-1.5 px-2 text-right" data-secondary>{fmtNum(o.stop_price, 4)}</td>
                      <td className="py-1.5 px-2 text-neutral-400" data-secondary>{o.trade_id || '—'}</td>
                      <td className="py-1.5 px-2 text-neutral-500">{o.reason || '—'}</td>
                    </tr>
                  );
                })
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ══ ⑤d 逐次成交明细（按需展开）══ */}
      {openPanel === 'fills' && selectedRunId && detailStatus === 'succeeded' && (
        <SectionCard
          title="⑤d 逐次成交明细 (Fills)"
          testid="lab-fills"
          sub
          right={
            <div className="flex items-center gap-2 text-[10px] font-mono">
              <Chip>共 {fillRows.length} 笔</Chip>
              <ColToggle on={!!allCols.fills} onToggle={() => toggleCols('fills')} total={10} />
              {fillRows.length > 0 && (
                <button
                  type="button"
                  className="px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700 transition-colors"
                  onClick={exportFillsCsv}
                  title="导出逐次成交记录为 CSV"
                >
                  导出成交 CSV
                </button>
              )}
              {fills.error && <span className="text-rose-600">成交取数错误：{fills.error}</span>}
              {fills.data?.missing_reason && <span className="text-amber-700">{fills.data.missing_reason}</span>}
            </div>
          }
        >
          <div className="ds-tablewrap" style={{ maxHeight: '18rem' }}>
            <table className={`ds-table ds-table--clickable${allCols.fills ? ' ds-table--all' : ''}`}>
              <thead>
                <tr>
                  <th className="py-1.5 px-2">成交时刻 (UTC)</th>
                  <th className="py-1.5 px-2">标的</th>
                  <th className="py-1.5 px-2">动作</th>
                  <th className="py-1.5 px-2 text-right">成交价格</th>
                  <th className="py-1.5 px-2 text-right">成交数量</th>
                  <th className="py-1.5 px-2 text-right" data-secondary>手续费</th>
                  <th className="py-1.5 px-2" data-secondary>流动性</th>
                  <th className="py-1.5 px-2" data-secondary>关联订单 (order_id)</th>
                  <th className="py-1.5 px-2" data-secondary>关联交易 (trade_id)</th>
                  <th className="py-1.5 px-2" data-secondary>成交标识 (fill_id)</th>
                </tr>
              </thead>
              <tbody>
                {fillRows.length === 0 ? (
                  <tr>
                    <td colSpan={allCols.fills ? 10 : 5} className="ds-table__empty">
                      {fills.loading ? '读取中…' : '无真实成交记录（账本 fills 为空）——不显示任何演示数据'}
                    </td>
                  </tr>
                ) : (
                  fillRows.map((f, i) => {
                    const rowKey = f.fill_id || `fill-${i}`;
                    const isSelected = selectedFillId === rowKey;
                    return (
                      <tr
                        key={rowKey}
                        data-testid={`lab-fill-row-${f.fill_id || i}`}
                        data-fill-id={rowKey}
                        data-order-id={f.order_id}
                        data-ts-ms={f.fill_time_ms}
                        data-selected={isSelected ? 'true' : 'false'}
                        className={`hover:bg-neutral-800/50 cursor-pointer transition-colors ${
                          isSelected ? 'bg-indigo-950/80 ring-1 ring-indigo-500 font-semibold' : ''
                        }`}
                        title="点击该成交 ⇒ 执行图表定位并高亮发生时点"
                        onClick={() => {
                          setSelectedFillId(rowKey);
                          if (f.fill_time_ms) setFocusTimeMs(f.fill_time_ms);
                          if (f.trade_id) setSelectedTradeId(f.trade_id);
                          if (f.symbol) setActiveSymbol(f.symbol.replace('USDT', ''));
                        }}
                      >
                      <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">{fmtUtc(f.fill_time_ms)}</td>
                      <td className="py-1.5 px-2 font-bold text-neutral-100">{f.symbol}</td>
                      <td className="py-1.5 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] ${f.action === 'open' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'}`}>
                          {f.action === 'open' ? '开仓' : '平仓'}
                        </span>
                      </td>
                      <td className="py-1.5 px-2 text-right font-mono">{fmtNum(f.fill_price, 4)}</td>
                      <td className="py-1.5 px-2 text-right font-mono">{fmtNum(f.quantity, 6)}</td>
                      <td className="py-1.5 px-2 text-right font-mono" data-secondary>{fmtNum(f.fee, 6)}</td>
                      <td className="py-1.5 px-2 text-neutral-400 uppercase text-[9px]" data-secondary>{f.liquidity || 'taker'}</td>
                      <td className="py-1.5 px-2 text-neutral-400 font-mono text-[9px]" data-secondary title={f.order_id}>{f.order_id ? (f.order_id.length > 16 ? `${f.order_id.slice(0, 10)}...` : f.order_id) : '—'}</td>
                      <td className="py-1.5 px-2 text-neutral-400 font-mono text-[9px]" data-secondary title={f.trade_id ?? undefined}>{f.trade_id ? (f.trade_id.length > 16 ? `${f.trade_id.slice(0, 10)}...` : f.trade_id) : '—'}</td>
                      <td className="py-1.5 px-2 text-neutral-500 font-mono text-[9px]" data-secondary title={f.fill_id}>{f.fill_id ? (f.fill_id.length > 16 ? `${f.fill_id.slice(0, 10)}...` : f.fill_id) : '—'}</td>
                    </tr>
                  );
                })
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ══ ⑤c 拒单明细（按需展开）══ */}
      {openPanel === 'rejected' && selectedRunId && detailStatus === 'succeeded' && (
        <SectionCard
          title="⑤c 拒单明细"
          testid="lab-rejected"
          sub
          right={
            <div className="flex items-center gap-2 text-[10px] font-mono">
              <Chip tone={rejectedRows.length > 0 ? 'warn' : 'neutral'}>共 {rejectedRows.length} 条</Chip>
              {rejected.error && <span className="text-rose-600">拒单取数错误：{rejected.error}</span>}
              {rejected.data?.missing_reason && <span className="text-amber-700">{rejected.data.missing_reason}</span>}
            </div>
          }
        >
          {rejectedByReason.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] font-mono">
              <span className="text-neutral-500">原因分布：</span>
              {rejectedByReason.map(([reason, n]) => (
                <span key={reason} className="ds-badge ds-badge--warn">
                  {reason} × {n}
                </span>
              ))}
            </div>
          )}
          <div className="ds-tablewrap" style={{ maxHeight: '16rem' }}>
            <table className="ds-table ds-table--clickable">
              <thead>
                <tr>
                  <th className="py-1.5 px-2">时点</th>
                  <th className="py-1.5 px-2">标的</th>
                  <th className="py-1.5 px-2">phase</th>
                  <th className="py-1.5 px-2">原因</th>
                </tr>
              </thead>
              <tbody>
                {rejectedRows.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="ds-table__empty">
                      {rejected.loading ? '读取中…' : '无真实拒单记录（账本 rejected 为空）——不把「无记录」包装成「无拒单」'}
                    </td>
                  </tr>
                ) : (
                  rejectedRows.map((r, i) => (
                    <tr key={`${r.ts_ms}-${r.symbol}-${i}`} className="hover:bg-neutral-50">
                      <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">{fmtUtc(r.ts_ms)}</td>
                      <td className="py-1.5 px-2 font-bold text-neutral-100">{r.symbol}</td>
                      <td className="py-1.5 px-2">{r.phase}</td>
                      <td className="py-1.5 px-2 text-amber-800">{r.reason}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ══ ⑤d 资金费结算明细（按需展开）══ */}
      {openPanel === 'funding' && selectedRunId && detailStatus === 'succeeded' && (
        <SectionCard
          title="⑤d 资金费结算明细"
          testid="lab-funding"
          sub
          right={
            <div className="flex items-center gap-2 text-[10px] font-mono">
              <Chip>共 {fundingEvents.length} 条</Chip>
              <ColToggle on={!!allCols.funding} onToggle={() => toggleCols('funding')} total={6} />
              {fundingSum !== null && m?.total_funding_fee !== null && m?.total_funding_fee !== undefined && (
                <span
                  className={`ds-badge ${Math.abs(fundingSum - m.total_funding_fee) < 1e-6 ? 'ds-badge--pos' : 'ds-badge--neg'}`}
                  title="逐事件求和 vs /metrics.total_funding_fee"
                >
                  对账：明细Σ {fmtUsd(fundingSum, 4)} vs 指标 {fmtUsd(m.total_funding_fee, 4)} ·{' '}
                  {Math.abs(fundingSum - m.total_funding_fee) < 1e-6 ? '一致' : '不一致'}
                </span>
              )}
              {chart.error && <span className="text-rose-600">资金费取数错误：{chart.error}</span>}
            </div>
          }
        >
          <div className="ds-tablewrap" style={{ maxHeight: '16rem' }}>
            <table className={`ds-table ds-table--clickable${allCols.funding ? ' ds-table--all' : ''}`}>
              <thead>
                <tr>
                  <th className="py-1.5 px-2">结算时点（UTC）</th>
                  <th className="py-1.5 px-2">标的</th>
                  <th className="py-1.5 px-2" data-secondary>trade_id</th>
                  <th className="py-1.5 px-2 text-right">费率</th>
                  <th className="py-1.5 px-2 text-right" data-secondary>标记价 mark</th>
                  <th className="py-1.5 px-2 text-right">金额（USD）</th>
                </tr>
              </thead>
              <tbody>
                {fundingEvents.length === 0 ? (
                  <tr>
                    <td colSpan={allCols.funding ? 6 : 4} className="ds-table__empty">
                      {chart.loading ? '读取中…' : '该 run 无真实资金费结算事件（账本 funding 为空）——不显示任何演示数据'}
                    </td>
                  </tr>
                ) : (
                  fundingEvents.map((f, i) => (
                    <tr key={`${f.t}-${f.trade_id}-${i}`} className="hover:bg-neutral-50">
                      <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">{fmtUtc(f.t)}</td>
                      <td className="py-1.5 px-2 font-bold text-neutral-100">{chartPayload?.symbol ?? '—'}</td>
                      <td className="py-1.5 px-2 text-neutral-400" data-secondary>{f.trade_id || '—'}</td>
                      <td className="py-1.5 px-2 text-right">{fmtNum(f.rate, 8)}</td>
                      <td className="py-1.5 px-2 text-right" data-secondary>{fmtNum(f.mark, 4)}</td>
                      <td className="py-1.5 px-2 text-right">{fmtUsd(f.amount, 6)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ══ 口径与审计（按需展开：指标口径 / 质量核对明细 / M4 门禁 / 边界声明）══ */}
      {openPanel === 'audit' && (
        <SectionCard title="口径与审计 · 指标口径 / 质量核对 / M4 门禁 / 边界声明" testid="lab-audit" section="lab-audit">
          {(m?.note || m?.sharpe_basis) && (
            <div className="ds-kvgrid ds-kvgrid--2" style={{ marginBottom: 10 }}>
              {m?.note && <KV k="指标口径" v={m.note} mono={false} />}
              {m?.sharpe_basis && (
                <KV
                  k="夏普（完整等间隔 UTC 逐日 · 年化 √365）"
                  v={fmtNum(m.sharpe_annualized_sqrt365, 4)}
                />
              )}
            </div>
          )}

        {/* ── 运行配置与审计详情（默认折叠）── */}
        <details className="ds-details" style={{ marginBottom: 10 }} data-testid="lab-audit-details">
          <summary>
            运行配置与审计详情（API 路径 / 请求体 / 后台线程 / 参数代码名 / 原始 JSON）
          </summary>
          <div className="ds-details__body space-y-1">
            <div>
              列表 / 详情：<span className="text-neutral-200">GET /api/v1/backtests</span>、
              <span className="text-neutral-200">GET /api/v1/backtests/{'{id}'}</span>（只读，不触发计算）
            </div>
            <div>
              创建：<span className="text-neutral-200">POST /api/v1/backtests</span> ⇒ 202 + run_id，由后台线程执行
            </div>
            <div>
              窗口端点须对齐 15m 网格（{LAB_WINDOW_GRID_MS} ms），最短 {LAB_MIN_WINDOW_MS} ms（1 天）
            </div>
            <div>epoch 毫秒：起点 {windowStartMs ?? '—'} · 终点 {windowEndMs ?? '—'}</div>
            <div className="break-all">
              本次将发送 payload：
              {JSON.stringify({
                strategy_id: LAB_STRATEGY_ID,
                exchange: form.exchange,
                market: form.market,
                symbols: [form.symbol.trim().toUpperCase()],
                signal_timeframe: form.signalTf,
                exec_timeframe: form.execTf,
                window_start: windowStartMs === null ? null : toIsoUtcZ(windowStartMs),
                window_end: windowEndMs === null ? null : toIsoUtcZ(windowEndMs),
                params: {},
              })}
            </div>
            <div className="break-all">
              白名单参数代码名（{LAB_PARAM_SPECS.length} 项）：{LAB_PARAM_SPECS.map((s) => s.key).join(', ')}
            </div>
            {defaultsNote && (
              <div className="flex items-start gap-1 text-neutral-500">
                <Info className="w-3 h-3 mt-0.5 shrink-0" />
                <span>{defaultsNote}</span>
              </div>
            )}
          </div>
        </details>

          <LabQualityPanel q={quality.data} loading={quality.loading} error={quality.error} />

          <div className="mt-3 border-t border-neutral-100 pt-2 space-y-1">
            <div className="text-[11px] font-bold text-neutral-200 flex items-center gap-1">
              <FlaskConical className="w-3.5 h-3.5 text-amber-600" />
              执行完成 ≠ 正式策略放行 / M4 验收
            </div>
            {metrics.data?.formal_m4_gate ? (
              <details className="ds-details" data-testid="lab-m4-gate">
                <summary>formal_m4_gate 原始字段（门禁逐条判定）</summary>
                <div className="ds-details__body text-[10px] text-neutral-400 font-mono break-all">
                  {JSON.stringify(metrics.data.formal_m4_gate, null, 2)}
                </div>
              </details>
            ) : (
              <div className="text-[10px] text-neutral-400">
                未取到 formal_m4_gate（运行未成功或该 run 无该字段）⇒ 不推断「已放行」。
              </div>
            )}
            <details className="ds-details">
              <summary>边界声明（本工作台只做研究 · Paper / Live 未接通）</summary>
              <div className="ds-details__body text-[10px] text-neutral-500 space-y-1">
                <div>
                  <Clock className="w-3 h-3 inline-block mr-1" />
                  本工作台只做<b>独立候选研究</b>；旧 3a–3d / A / B 已<b>退役</b>（可运行入口已下线），
                  Paper / Live 未接新共享核心 ⇒ 显示 NOT_CONNECTED、禁止新开仓与真实下单。
                </div>
                <div>
                  <AlertCircle className="w-3 h-3 inline-block mr-1" />
                  页面数值全部来自服务端账本；缺失显示 —，不造数。
                </div>
              </div>
            </details>
          </div>
        </SectionCard>
      )}

    </div>
  );
};

export default BacktestWorkbench;
