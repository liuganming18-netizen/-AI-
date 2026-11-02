/**
 * 观察区面板（SIGNAL-OBSERVE-FIX-12）
 *
 * 为什么需要这个组件
 * ------------------
 * 修复前页面上**没有任何观察区**：全池 346 个「处于观察状态的事件」
 * （箱体 304 / 已扫低 21 / 待突破 21）被压成一句
 * 「箱体观察 304 · 已扫低 21 · 待突破 21」，还藏在折叠的诊断块里。
 * 用户看不到是**哪些**币、到哪一步、还差什么、什么时候过期
 * ⇒ 表现为「观察区没有反应」。
 *
 * 定义（不是本组件发明）：冻结规则 §3「每个 symbol+venue 最多一个处于观察状态的事件」
 * ⇒ 观察中 = `BOX` / `SWEEP` / `RETEST`。`CANDIDATE` 及之后事件已结束，不算观察中。
 *
 * 纪律
 * ----
 * · 只读展示：不点按钮、不下单、不推进 bar；
 * · 判据与门槛全部来自后端 `observe_events()`，这里**不重算、不补默认**；
 * · 缺数据写「未就绪」，不拿 0 或「无」冒充；
 * · 空态必须区分「没有符合条件的标的 / 数据未就绪 / 扫描或接口异常」，
 *   不能统一显示成「交易信号 0」—— 那是本工单要修的原缺陷。
 */

import React, { useMemo, useState } from 'react';
import type {
  ObserveEvidence,
  ObserveEvidenceValue,
  ObserveItem,
  ObserveView,
} from '../api/liveTrading';
import { classifyObserve } from '../api/liveTrading';

const STATE_STYLE: Record<string, string> = {
  RETEST: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  SWEEP: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  BOX: 'border-neutral-600/60 bg-neutral-800 text-neutral-300',
};

const fmtUtc = (iso: string | null): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(
    d.getUTCMinutes(),
  )}`;
};

const fmtRemain = (h: number | null): string => {
  if (h === null) return '—';
  if (h < 0) return `已超 ${Math.abs(h).toFixed(1)}h`;
  if (h < 24) return `剩 ${h.toFixed(1)}h`;
  return `剩 ${(h / 24).toFixed(1)} 天`;
};

/* ---------- 市场证据（ACCUM-EARLY-02）---------- */

const fmtRatio = (v: number | null): string => (v === null ? '—' : v.toFixed(2));

/** 价格量级跨度极大（0.00001 ~ 80000），统一用一套小数位会失真。 */
const fmtPrice = (v: number | null): string => {
  if (v === null) return '—';
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(2);
  if (a >= 1) return v.toFixed(4);
  if (a >= 0.01) return v.toFixed(5);
  if (a === 0) return '0';
  return v.toPrecision(4);
};

/**
 * 多空比**显式别名映射**（PR2-WO-01 修复 B）。
 *
 * 旧实现是「同值即别名」：两列读数只要满足 `|a-b| < 1e-9` 就判为同一个指标。
 * 后果有两层：
 *   1. 紧凑行把**语义不同**的两列合并成一行 ⇒ 让人以为库里少了一类独立信息；
 *   2. 展开详情把其中一列标成「与 X 同值＝别名」⇒ 直接给出**错误结论**。
 * 数值恰好相等只说明这一刻读数撞上了，不说明两列是同一个指标。
 *
 * 但也不能「成对就无条件合并」：后端 `metrics_api.py` 专门统计
 * `retail_account_ls_ratio <> count_long_short_ratio` 与
 * `top_position_ls_ratio <> sum_toptrader_long_short_ratio` 的行数，
 * 说明这两对**可能真的分叉**。若无条件合并，分叉时会把真实差异藏起来。
 *
 * 故判据 = **已声明成对**（下表）**且本次读数相等**，缺一不可；
 * 不在表里的列自成一组，永不与任何列合并 —— 即使数值一模一样。
 */
export const RATIO_ALIAS_GROUPS: ReadonlyArray<readonly string[]> = [
  // 零售账户多空比 ←→ 账户数多空比（同一指标的两个列名）
  ['retail_account_ls_ratio', 'count_long_short_ratio'],
  // 大户持仓多空比 ←→ 大户持仓量多空比
  ['top_position_ls_ratio', 'sum_toptrader_long_short_ratio'],
];

/** 列名 → 别名组代表键（组内第一个）。未声明的列返回自身。 */
const RATIO_CANONICAL: ReadonlyMap<string, string> = new Map(
  RATIO_ALIAS_GROUPS.flatMap((g) => g.map((k) => [k, g[0]] as const)),
);

export const canonicalRatioKey = (key: string): string => RATIO_CANONICAL.get(key) ?? key;

/** 两列是否**被声明**为同一指标的不同列名（不看数值）。 */
export const isDeclaredRatioAlias = (aKey: string, bKey: string): boolean =>
  aKey !== bKey && canonicalRatioKey(aKey) === canonicalRatioKey(bKey);

/** 两个读数是否可判定为同值；「未知」不等于「相同」。 */
const sameReading = (a: number | null, b: number | null): boolean =>
  a !== null && b !== null && Math.abs(a - b) < 1e-9;

/** 两列是否构成别名：**已声明成对 + 本次读数相等**。任一不满足即视为两列独立信息。 */
export const isRatioAliasOf = (a: ObserveEvidenceValue, b: ObserveEvidenceValue): boolean =>
  isDeclaredRatioAlias(a.key, b.key) && sameReading(a.value, b.value);

/**
 * 紧凑行只列独立值。
 *
 * 只折叠「已声明成对且本次同值」的列；其余全部保留 —— 包括值为 null 的列，
 * 因为「这一列没读到」不等于「它与另一列相同」。
 */
export const dedupeRatios = (rs: ObserveEvidenceValue[]): ObserveEvidenceValue[] => {
  const out: ObserveEvidenceValue[] = [];
  for (const r of rs) {
    if (!out.some((o) => isRatioAliasOf(o, r))) out.push(r);
  }
  return out;
};

/** 紧凑证据行：始终可见（不折叠），让「这个观察事件当时长什么样」不用点开就能看到。 */
const EvidenceStrip: React.FC<{ ev: ObserveEvidence | null }> = ({ ev }) => {
  if (!ev) return null;
  if (ev.pending) {
    return (
      <div data-testid="observe-evidence" data-evidence-state="pending" className="text-[9px] text-neutral-500 font-mono">
        微观指标计算中...
      </div>
    );
  }
  const indep = dedupeRatios(ev.ratios);
  const price = ev.phasePrice?.value ?? null;
  return (
    <div
      data-testid="observe-evidence"
      data-evidence-state={ev.stale ? 'stale' : 'ok'}
      data-evidence-stale-hours={ev.staleHours ?? ''}
      className="flex items-center gap-1.5 flex-wrap text-[10px] text-neutral-400 font-mono"
    >
      <span>Taker <span className="text-neutral-200">{fmtRatio(ev.taker?.value ?? null)}</span></span>
      <span className="text-neutral-700">·</span>
      <span>多空 <span className="text-neutral-200">{indep.length === 0 ? '—' : fmtRatio(indep[0]?.value)}</span></span>
      <span className="text-neutral-700">·</span>
      <span>巨鲸成本 <span className="text-neutral-200">{fmtPrice(price)}</span></span>
    </div>
  );
};

/** 展开后的逐项明细：每一列的实际读取值、观测时刻、来源、缺失原因。 */
const EvidenceDetail: React.FC<{ ev: ObserveEvidence | null }> = ({ ev }) => {
  if (!ev || ev.pending) return null;
  const rows = [...(ev.taker ? [ev.taker] : []), ...ev.ratios];
  return (
    <div data-testid="observe-evidence-detail" className="space-y-0.5 pt-1 border-t border-neutral-800/60">
      {rows.map((r, i) => {
        // 只有「已声明成对且本次同值」才标别名；不再仅凭数值相等下结论。
        const aliasOf = rows.findIndex((o) => isRatioAliasOf(o, r));
        return (
          <div key={`${r.key}-${i}`} className="text-[9px] leading-snug">
            <span className="text-neutral-400 font-mono">{r.key}</span>{' '}
            <span className="text-neutral-200 font-mono">{fmtRatio(r.value)}</span>{' '}
            <span className="text-neutral-600">
              {r.atUtc ? `@${r.atUtc.slice(5, 16).replace('T', ' ')}` : '时刻未知'}
              {r.fromLatestRow === false ? '（回退值，非最新行）' : ''}
              {aliasOf >= 0 ? `（与 ${rows[aliasOf].key} 同源且同值＝别名）` : ''}
            </span>
            {r.missing ? <span className="text-amber-300/80"> 缺失：{r.missing}</span> : null}
          </div>
        );
      })}
      {ev.phasePrice ? (
        <div className="text-[9px] leading-snug">
          <span className="text-neutral-400 font-mono">阶段均价</span>{' '}
          <span className="text-neutral-200 font-mono">{fmtPrice(ev.phasePrice.value)}</span>{' '}
          <span className="text-neutral-600">
            {ev.phasePrice.bars ?? '—'} 根 1h（{ev.phasePrice.fromUtc?.slice(5, 16).replace('T', ' ') ?? '—'}
            {' → '}
            {ev.phasePrice.toUtc?.slice(5, 16).replace('T', ' ') ?? '—'}）
          </span>
          {ev.phasePrice.missing ? (
            <span className="text-amber-300/80"> 缺失：{ev.phasePrice.missing}</span>
          ) : null}
        </div>
      ) : null}
      <div className="text-[9px] text-neutral-600 leading-snug">
        时间范围 {ev.rangeFromUtc?.slice(0, 16).replace('T', ' ') ?? '—'}
        {' → '}
        {ev.rangeToUtc?.slice(0, 16).replace('T', ' ') ?? '—'}
        {ev.source ? ` · 来源 ${ev.source}` : ''}
      </div>
      {ev.availability ? (
        <div className="text-[9px] text-neutral-600 leading-snug">
          可得性 {ev.availability}
          {ev.availabilityNote ? `：${ev.availabilityNote}` : ''}
        </div>
      ) : null}
      {ev.tailNulls.length > 0 ? (
        <div className="text-[9px] text-amber-300/70 leading-snug">
          本轮未采到、历史有值：{ev.tailNulls.join(' / ')}
        </div>
      ) : null}
      {ev.missingCols.length > 0 ? (
        <div className="text-[9px] text-rose-300/70 leading-snug">
          整段尾部都无值：{ev.missingCols.join(' / ')}
        </div>
      ) : null}
      {ev.why ? (
        <div className="text-[9px] text-neutral-600 leading-snug">只作证据：{ev.why}</div>
      ) : null}
    </div>
  );
};

/* ---------- 观察区价格口径（PR2-WO-01 修复 C）---------- */

/**
 * 价格来源三态。**只降不升**：确认不到就退回 unknown，绝不默认成实时。
 */
export type ObservePriceSource = 'realtime' | 'candle_close' | 'unknown';

export interface ObservePriceDisplay {
  value: number | null;
  source: ObservePriceSource;
  /** 展示用标签：最新价 / K线收盘价 / 来源未确认 / 价格未知 */
  label: string;
  /** 该价格对应的时刻（UTC）：实时价=报价时刻；K线=该 bar 收盘时刻；未确认=null */
  asOfUtc: string | null;
}

const asNum = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

const asStr = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

/** 校验并解析有效 ISO 时间戳；缺失、空串或非法解析一律返回 null，绝不伪造。 */
const asValidIso = (v: unknown): string | null => {
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) ? s : null;
};

/**
 * 解析观察区「价格」的取值与口径。
 *
 * 为什么必须区分（修复 C & GM-UI-BRANCH-02-R1 补强）：
 *   后端 `unified_runtime.py` 构造观察区载荷时，`last_price` 取的是
 *   最近一根**已处理 1h bar 的 close**（`"last_price": None if last is None else last["close"]`），
 *   `last_bar_utc` 是**同一根** bar 的收盘时刻 —— 两者同源，**都不是实时成交价**。
 *   旧前端一律标「现价」，实时价缺失时还会拿 `last_bar.close` 顶上仍叫「现价」，
 *   读的人会以为那是当下成交价，据此判断「离突破还差多少」就会算错。
 *
 * 判据按「能确认到什么程度」分档，**只降不升**：
 *   1. 载荷**显式**声明实时报价（`price_source` 为 realtime/live，或 `realtime.last_price`）：
 *      - 必须具备**有效且可解析的报价时间证据**（`at_utc` 或 `price_at_utc`）⇒ 最新价，时刻取报价时刻；
 *      - 若时间缺失或无法解析 ⇒ 降级标「报价时间未确认」，不得冒充可确认时效的「最新价」；
 *   2. 只有 bar 收盘价（`last_bar.close` 按字段名即 K 线收盘；
 *      或后端同源的 `last_price` 且带 `last_bar_utc` 佐证）⇒ K线收盘价，时刻取 bar 收盘时刻；
 *   3. 有数值但**确认不到来源**（既无实时声明，也无 bar 时刻佐证）
 *      ⇒ 标「来源未确认」，数值照显（不隐瞒），但**不得**标成实时价；
 *   都没有 ⇒ 「价格未知」。
 *
 * 兼容性：只读既有字段，不要求后端新增任何字段；`realtime` / `price_source`
 * 目前载荷里没有，属**前瞻分支**（出现时才生效），缺失不报错、不改变现有显示。
 */
export function resolveObservePrice(item: ObserveItem): ObservePriceDisplay {
  const raw = item as unknown as Record<string, any>;

  // ① 显式实时来源（需经时间证据校验）
  const declaredSource = asStr(raw.price_source) ?? asStr(raw.priceSource);
  const realtimeObj = raw.realtime && typeof raw.realtime === 'object' ? raw.realtime : null;
  const realtimeValue = asNum(realtimeObj?.last_price);
  const isDeclaredRealtime = declaredSource === 'realtime' || declaredSource === 'live';
  const realtimePrice = isDeclaredRealtime ? asNum(raw.last_price) ?? realtimeValue : realtimeValue;
  if (realtimePrice !== null) {
    const rawTime = asStr(realtimeObj?.at_utc) ?? asStr(raw.price_at_utc);
    const validTime = asValidIso(rawTime);
    if (validTime !== null) {
      return {
        value: realtimePrice,
        source: 'realtime',
        label: '最新价',
        asOfUtc: validTime,
      };
    }
    // 报价时间证据缺失或非法解析：展示保守状态「报价时间未确认」，时效未佐证前不标实时
    return {
      value: realtimePrice,
      source: 'unknown',
      label: '报价时间未确认',
      asOfUtc: null,
    };
  }

  // ② K 线收盘价
  const bar = raw.last_bar && typeof raw.last_bar === 'object' ? raw.last_bar : null;
  const barClose = asNum(bar?.close);
  const barCloseUtc = asStr(bar?.close_utc) ?? asStr(bar?.closeTime) ?? asStr(raw.last_bar_utc);
  if (barClose !== null) {
    return { value: barClose, source: 'candle_close', label: 'K线收盘价', asOfUtc: barCloseUtc };
  }

  const lastPrice = asNum(raw.last_price);
  if (lastPrice !== null) {
    const lastBarUtc = asStr(raw.last_bar_utc);
    // 后端 `last_price` 与 `last_bar_utc` 出自同一根 bar ⇒ 有时刻佐证才算「确认是收盘价」
    if (lastBarUtc !== null) {
      return {
        value: lastPrice,
        source: 'candle_close',
        label: 'K线收盘价',
        asOfUtc: lastBarUtc,
      };
    }
    return { value: lastPrice, source: 'unknown', label: '来源未确认', asOfUtc: null };
  }

  // ③ 没有价格
  return { value: null, source: 'unknown', label: '价格未知', asOfUtc: null };
}

const ObserveRow: React.FC<{ item: ObserveItem }> = ({ item }) => {
  const [open, setOpen] = useState(false);
  const expired = item.expired === true;
  const stageCls = STATE_STYLE[item.state] ?? 'border-neutral-700 bg-neutral-800 text-neutral-300';

  // 价格：取值与口径由 resolveObservePrice 一并给出，**来源与数值不再分开猜**
  // （修复前：`last_price ?? last_bar.close` 一律标「现价」，见该函数注释）。
  const price = resolveObservePrice(item);
  const lastPrice = price.value;
  const targetPrice = (item as any).breakout_target ?? item.box?.h ?? null;
  const rawGap = (item as any).gap_pct ?? (
    (lastPrice && targetPrice && lastPrice > 0)
      ? Number(((targetPrice - lastPrice) / lastPrice * 100).toFixed(1))
      : null
  );

  return (
    <div
      data-testid="observe-row"
      data-symbol={item.symbol}
      data-state={item.state}
      data-updated-utc={item.updatedUtc ?? ''}
      data-expired={expired ? '1' : '0'}
      className={`rounded border p-2 space-y-1.5 transition-colors ${
        expired
          ? 'border-rose-500/40 bg-rose-950/20'
          : 'border-neutral-800/90 bg-neutral-900/90 hover:border-neutral-700'
      }`}
    >
      {/* 第 1 行：标的名称 + 状态徽章 + 剩余到期时间 + 参数按钮 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-mono text-[12px] font-bold text-neutral-100 truncate">
            {item.symbol}
          </span>
          <span className={`text-[9px] px-1.5 py-0.2 rounded border font-medium ${stageCls}`}>
            {item.stateCn || item.state}
          </span>
          {expired && (
            <span className="text-[9px] px-1 py-0.2 rounded bg-rose-500/10 text-rose-300 border border-rose-500/30">
              已到期
            </span>
          )}
          {!item.dataReady && (
            <span className="text-[9px] px-1 py-0.2 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300">
              未就绪
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-neutral-400 font-mono">
            {fmtRemain(item.remainingHours)}
          </span>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            data-testid="observe-row-toggle"
            className="text-[9px] text-neutral-500 hover:text-sky-400 font-sans"
          >
            {open ? '简收' : '参数'}
          </button>
        </div>
      </div>

      {/* 第 2 行：核心量化看盘数据（价格 vs 突破位 vs 距离）
          价格标签按**实际来源**给（最新价 / K线收盘价 / 来源未确认 / 价格未知），
          不再一律写「现价」—— 那是本工单要修的误导性展示。 */}
      <div className="grid grid-cols-3 gap-1 py-1 px-1.5 rounded bg-neutral-950/80 border border-neutral-850 font-mono text-[10px]">
        <div>
          <span
            className="text-[9px] text-neutral-500 font-sans block"
            data-testid="observe-price-label"
            data-price-source={price.source}
            title={
              price.asOfUtc
                ? `${price.label} · 对应时刻 ${fmtUtc(price.asOfUtc)}`
                : price.source === 'unknown'
                  ? (price.label === '报价时间未确认'
                      ? '报价时间缺失或无法解析，时效无法确认'
                      : '无法确认该数值的来源，不按实时价展示')
                  : price.label
            }
          >
            {price.label}
          </span>
          <span className="text-neutral-200 font-medium" data-testid="observe-price-value">
            {fmtPrice(lastPrice)}
          </span>
        </div>
        <div>
          <span className="text-[9px] text-neutral-500 font-sans block">
            {item.state === 'RETEST' ? '突破位' : item.state === 'SWEEP' ? '扫低位' : '箱体上沿'}
          </span>
          <span className="text-sky-400 font-medium">{fmtPrice(targetPrice)}</span>
        </div>
        <div>
          <span className="text-[9px] text-neutral-500 font-sans block">距突破</span>
          <span className={`font-semibold ${rawGap !== null && rawGap <= 3 ? 'text-emerald-400' : 'text-amber-400'}`}>
            {rawGap !== null ? (rawGap > 0 ? `+${rawGap}%` : `${rawGap}%`) : '—'}
          </span>
        </div>
      </div>

      {/* 第 3 行：多因子微观数据（Taker / 大户多空比 / 巨鲸成本） */}
      <EvidenceStrip ev={item.evidence} />

      {/* 隐藏的详细审计参数 */}
      {open && (
        <div className="pt-1.5 border-t border-neutral-800/80 space-y-1 text-[9px]">
          <div className="text-neutral-400 font-mono">
            箱体: [{item.box?.l ?? '—'} ~ {item.box?.h ?? '—'}] · ATR: {item.box?.a ?? '—'} · 宽 {item.box?.widthAtr ?? '—'}A
          </div>
          <div className="text-neutral-500 font-mono">
            更新: {fmtUtc(item.updatedUtc)} · 推进K线: {fmtUtc(item.lastBarUtc)}
          </div>
          {item.unmet.length > 0 && (
            <div data-testid="observe-unmet" className="text-amber-400/80">
              {item.unmet.join('; ')}
            </div>
          )}
          <EvidenceDetail ev={item.evidence} />
        </div>
      )}
    </div>
  );
};

/** 空态三态说明（工单要求：不能统一显示成「交易信号 0」）。 */
const EMPTY_TEXT: Record<
  Exclude<ReturnType<typeof classifyObserve>, 'has_items'>,
  { title: string; hint: string; cls: string }
> = {
  no_candidate: {
    title: '没有符合观察条件的标的',
    hint: '全池已扫描、数据正常，当前没有任何标的处于箱体/扫低/回踩观察状态。这是策略结论，不是故障。',
    cls: 'border-neutral-800 bg-neutral-900 text-neutral-400',
  },
  data_not_ready: {
    title: '数据未就绪',
    hint: '观察明细还没生成（运行时未启动、正在预热，或全池没有已处理的闭合 K 线）。此时不能判定「没有观察标的」。',
    cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  },
  fault: {
    title: '观察区读取异常',
    hint: '扫描或接口出问题，或读到的是过期残留。此时「0 个观察标的」不可信，需要排查。',
    cls: 'border-rose-500/40 bg-rose-950/20 text-rose-300',
  },
};

const ObservePanel: React.FC<{ view: ObserveView | null; pending?: boolean }> = ({
  view,
  pending = false,
}) => {
  const [filter, setFilter] = useState<'ALL' | 'BOX' | 'SWEEP' | 'RETEST'>('ALL');
  const [q, setQ] = useState('');
  const [collapsed, setCollapsed] = useState(false);
  /**
   * 渲染上限：**数据不截断**（后端全量返回，符合观察条件的都在 items 里），
   * 只限制一次画多少行 —— 全池 500+ 条同时挂 DOM 会让面板卡顿。
   * 用户点「显示全部」或搜索/筛选后自然看到其余。
   */
  const [cap, setCap] = useState(60);

  const reason = classifyObserve(view);
  const items = view?.items ?? [];

  const shown = useMemo(() => {
    const kw = q.trim().toUpperCase();
    const list = items.filter(
      (x) =>
        (filter === 'ALL' || x.state === filter) &&
        (kw === '' || x.symbol.toUpperCase().includes(kw)),
    );
    return list.sort((a, b) => {
      const ga = (a as any).gap_pct ?? 999;
      const gb = (b as any).gap_pct ?? 999;
      return ga - gb;
    });
  }, [items, filter, q]);

  // 计数只来自后端 counts；接口没给就写「—」，不自己数 items（items 可能被截断）
  const cnt = view?.counts ?? null;
  const total = view?.observingTotal;
  const totalText = typeof total === 'number' ? String(total) : '—';

  if (!view && pending) {
    return (
      <div
        data-testid="observe-loading"
        className="mx-2 mt-1.5 px-2 py-1.5 rounded border border-neutral-800 bg-neutral-900 text-[10px] text-neutral-400"
      >
        正在读取观察区（处于观察状态的事件）…
      </div>
    );
  }

  return (
    <div
      data-testid="observe-panel"
      data-observe-kind={view?.kind ?? 'none'}
      data-observe-reason={reason}
      data-observe-total={typeof total === 'number' ? String(total) : ''}
      className="mx-2 mt-1.5 rounded border border-neutral-800 bg-neutral-900/70 overflow-hidden"
    >
      {/* 标题行：计数永远可见（即使折叠），保证「观察区有反应」 */}
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 bg-neutral-900">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          data-testid="observe-toggle"
          className="flex items-center gap-1.5 min-w-0 text-left"
        >
          <span className="text-[10px] font-semibold text-neutral-200 shrink-0">观察区</span>
          <span
            data-testid="observe-total"
            className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-sky-500/40 bg-sky-500/10 text-sky-300 shrink-0"
          >
            观察中 {totalText}
          </span>
          {cnt ? (
            <span
              data-testid="observe-counts"
              className="text-[9px] text-neutral-400 font-mono truncate"
            >
              箱体 {cnt.BOX ?? 0} · 已扫低 {cnt.SWEEP ?? 0} · 待突破 {cnt.RETEST ?? 0}
            </span>
          ) : null}
          <span className="text-[9px] text-neutral-600 shrink-0">{collapsed ? '▼' : '▲'}</span>
        </button>
        <span className="text-[9px] text-neutral-500 shrink-0">
          {view?.generatedAt ? `更新于 ${fmtUtc(view.generatedAt)}` : '—'}
        </span>
      </div>

      {collapsed ? null : (
        <div className="px-2 pb-2 space-y-1.5 border-t border-neutral-800/80 pt-1.5">
          {reason === 'has_items' ? (
            <>
              <div className="flex items-center gap-1.5">
                <div className="flex gap-1">
                  {(['ALL', 'RETEST', 'SWEEP', 'BOX'] as const).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setFilter(k)}
                      className={`text-[9px] px-1.5 py-0.5 rounded border ${
                        filter === k
                          ? 'border-sky-500/50 bg-sky-500/15 text-sky-300'
                          : 'border-neutral-800 text-neutral-400 hover:text-neutral-200'
                      }`}
                    >
                      {k === 'ALL'
                        ? `全部 ${items.length}`
                        : `${k === 'RETEST' ? '待突破' : k === 'SWEEP' ? '已扫低' : '箱体'} ${
                            cnt?.[k] ?? 0
                          }`}
                    </button>
                  ))}
                </div>
                <input
                  type="text"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜币种"
                  className="flex-1 min-w-0 bg-neutral-850 border border-neutral-800 focus:border-neutral-700 rounded px-1.5 py-0.5 text-[9px] text-neutral-300 placeholder-neutral-500 outline-none font-mono"
                />
              </div>

              <div
                data-testid="observe-list"
                className="max-h-64 overflow-y-auto space-y-1 pr-0.5"
              >
                {shown.slice(0, cap).map((x) => (
                  <ObserveRow key={x.symbol} item={x} />
                ))}
                {shown.length === 0 ? (
                  <div className="text-[9px] text-neutral-500 py-1">
                    当前筛选下没有匹配的观察标的
                  </div>
                ) : null}
                {shown.length > cap ? (
                  <button
                    type="button"
                    data-testid="observe-show-more"
                    onClick={() => setCap((v) => v + 200)}
                    className="w-full text-[9px] text-sky-400 hover:text-sky-300 py-1"
                  >
                    还有 {shown.length - cap} 条未显示，点此继续加载
                  </button>
                ) : null}
              </div>

              {view?.truncated ? (
                <div className="text-[9px] text-amber-300/80">
                  后端仅返回前 {view.shown ?? '—'} 条（共 {totalText} 条）—— 明细被截断，不代表只有这些
                </div>
              ) : null}
            </>
          ) : (
            <div
              data-testid="observe-empty"
              data-observe-reason={reason}
              className={`rounded border px-2 py-1.5 space-y-0.5 ${
                EMPTY_TEXT[reason as Exclude<typeof reason, 'has_items'>].cls
              }`}
            >
              <div className="text-[10px] font-medium">
                {EMPTY_TEXT[reason as Exclude<typeof reason, 'has_items'>].title}
              </div>
              <div className="text-[9px] leading-relaxed opacity-90">
                {view?.message ??
                  EMPTY_TEXT[reason as Exclude<typeof reason, 'has_items'>].hint}
              </div>
              <div className="text-[9px] text-neutral-500 font-mono">
                全池 {view?.universe ?? '—'} 个标的
                {typeof view?.dataNotReady === 'number'
                  ? ` · 数据未就绪 ${view.dataNotReady}`
                  : ''}
                {typeof view?.scanErrorsTotal === 'number'
                  ? ` · 扫描异常 ${view.scanErrorsTotal}`
                  : ''}
                {typeof view?.ageSeconds === 'number' ? ` · 明细距今 ${view.ageSeconds}s` : ''}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ObservePanel;
