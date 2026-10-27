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
 * 同值即别名。
 *
 * 实测（ACCUM-EARLY-02 §1）：`retail_account_ls_ratio` 与 `count_long_short_ratio`
 * 逐位相同、`top_position_ls_ratio` 与 `sum_toptrader_long_short_ratio` 逐位相同
 * ⇒ 库里 5 列多空比只有 3 类独立信息。紧凑行只列独立值，避免「5 个数看着很丰富」的错觉；
 * 展开详情里仍逐列给全，并标出哪一列是别名。
 */
const dedupeByValue = (rs: ObserveEvidenceValue[]): ObserveEvidenceValue[] => {
  const out: ObserveEvidenceValue[] = [];
  for (const r of rs) {
    const v = r.value;
    if (v === null) {
      out.push(r);
      continue;
    }
    const dup = out.some((o) => o.value !== null && Math.abs(o.value - v) < 1e-9);
    if (!dup) out.push(r);
  }
  return out;
};

/** 紧凑证据行：始终可见（不折叠），让「这个观察事件当时长什么样」不用点开就能看到。 */
const EvidenceStrip: React.FC<{ ev: ObserveEvidence | null }> = ({ ev }) => {
  if (!ev) {
    return (
      <div data-testid="observe-evidence" data-evidence-state="none" className="text-[9px] text-neutral-600">
        证据：后端未提供
      </div>
    );
  }
  if (ev.pending) {
    return (
      <div
        data-testid="observe-evidence"
        data-evidence-state="pending"
        className="text-[9px] text-neutral-500"
      >
        证据：后台计算中（首次读取约 30 秒，下一轮轮询可见）
      </div>
    );
  }
  const indep = dedupeByValue(ev.ratios);
  const price = ev.phasePrice?.value ?? null;
  const noValue = ev.taker?.value == null && indep.length === 0 && price === null;
  return (
    <div
      data-testid="observe-evidence"
      data-evidence-state={ev.stale ? 'stale' : 'ok'}
      data-evidence-stale-hours={ev.staleHours ?? ''}
      className="flex items-center gap-1.5 flex-wrap text-[9px] leading-snug"
    >
      <span className="text-neutral-600 shrink-0">证据</span>
      <span className="font-mono text-neutral-400">
        主动买卖 <span className="text-neutral-200">{fmtRatio(ev.taker?.value ?? null)}</span>
        <span className="mx-1 text-neutral-700">·</span>
        多空比{' '}
        <span className="text-neutral-200">
          {indep.length === 0 ? '—' : indep.map((r) => fmtRatio(r.value)).join('/')}
        </span>
        <span className="mx-1 text-neutral-700">·</span>
        阶段均价 <span className="text-neutral-200">{fmtPrice(price)}</span>
      </span>
      {ev.stale ? (
        <span
          data-testid="observe-evidence-stale"
          className="text-[9px] px-1 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 shrink-0"
          title={ev.staleNote ?? ''}
        >
          指标停更 {ev.staleHours === null ? '' : `${Math.round(ev.staleHours)}h`}（旧市场）
        </span>
      ) : null}
      {noValue ? (
        <span className="text-[9px] text-rose-300/80">
          读取失败：{ev.missing[0] ?? ev.taker?.missing ?? '原因未记录'}
        </span>
      ) : null}
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
        const aliasOf = rows.findIndex(
          (o) => o !== r && o.value !== null && r.value !== null && Math.abs(o.value - r.value) < 1e-9,
        );
        return (
          <div key={`${r.key}-${i}`} className="text-[9px] leading-snug">
            <span className="text-neutral-400 font-mono">{r.key}</span>{' '}
            <span className="text-neutral-200 font-mono">{fmtRatio(r.value)}</span>{' '}
            <span className="text-neutral-600">
              {r.atUtc ? `@${r.atUtc.slice(5, 16).replace('T', ' ')}` : '时刻未知'}
              {r.fromLatestRow === false ? '（回退值，非最新行）' : ''}
              {aliasOf >= 0 ? `（与 ${rows[aliasOf].key} 同值＝别名）` : ''}
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

const ObserveRow: React.FC<{ item: ObserveItem }> = ({ item }) => {
  const [open, setOpen] = useState(false);
  const expired = item.expired === true;
  const stageCls = STATE_STYLE[item.state] ?? 'border-neutral-700 bg-neutral-800 text-neutral-300';
  return (
    <div
      data-testid="observe-row"
      data-symbol={item.symbol}
      data-state={item.state}
      data-updated-utc={item.updatedUtc ?? ''}
      data-expired={expired ? '1' : '0'}
      className={`rounded border px-2 py-1.5 space-y-1 ${
        expired
          ? 'border-rose-500/40 bg-rose-950/20'
          : 'border-neutral-800 bg-neutral-900'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-mono text-[11px] font-bold text-neutral-100 truncate">
            {item.symbol}
          </span>
          <span className={`text-[9px] px-1 py-0.5 rounded border shrink-0 ${stageCls}`}>
            {item.stateCn || item.state}
          </span>
          {expired ? (
            <span className="text-[9px] px-1 py-0.5 rounded border border-rose-500/50 bg-rose-500/10 text-rose-300 shrink-0">
              已过期
            </span>
          ) : null}
          {!item.dataReady ? (
            <span className="text-[9px] px-1 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 shrink-0">
              数据未就绪
            </span>
          ) : null}
        </div>
        <span className="text-[9px] text-neutral-500 font-mono shrink-0">
          {fmtRemain(item.remainingHours)}
        </span>
      </div>

      <div className="flex items-center justify-between gap-2 text-[9px] text-neutral-500">
        <span>
          最近更新 <span className="font-mono text-neutral-400">{fmtUtc(item.updatedUtc)}</span>
          <span className="mx-1 text-neutral-700">·</span>
          最近处理 1h <span className="font-mono text-neutral-400">{fmtUtc(item.lastBarUtc)}</span>
        </span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          data-testid="observe-row-toggle"
          className="text-[9px] text-sky-400 hover:text-sky-300 shrink-0"
        >
          {open ? '收起条件' : '条件'}
        </button>
      </div>

      <EvidenceStrip ev={item.evidence} />

      {/* 已满足 / 未满足：默认**直接显示未满足**（用户最需要知道还差什么），
          已满足折叠起来避免长列表噪音。 */}
      {item.unmet.length > 0 ? (
        <ul className="space-y-0.5" data-testid="observe-unmet">
          {item.unmet.map((t, i) => (
            <li key={`u${i}`} className="text-[9px] text-amber-300/90 leading-snug">
              未满足：{t}
            </li>
          ))}
        </ul>
      ) : item.dataReady ? (
        <div className="text-[9px] text-emerald-400/90" data-testid="observe-unmet-none">
          下一关条件已全部达到（等待收盘确认；是否成交仍由执行门禁决定）
        </div>
      ) : null}

      {open ? (
        <div className="pt-1 border-t border-neutral-800 space-y-0.5">
          {item.satisfied.length > 0 ? (
            <ul className="space-y-0.5" data-testid="observe-satisfied">
              {item.satisfied.map((t, i) => (
                <li key={`s${i}`} className="text-[9px] text-emerald-400/80 leading-snug">
                  已满足：{t}
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-[9px] text-neutral-500">已满足：无记录</div>
          )}
          {item.pendingEval.length > 0 ? (
            <ul className="space-y-0.5" data-testid="observe-pending">
              {item.pendingEval.map((t, i) => (
                <li key={`p${i}`} className="text-[9px] text-neutral-400 leading-snug">
                  待评估：{t}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="text-[9px] text-neutral-500 font-mono leading-snug">
            箱体 H {item.box?.h ?? '—'} / L {item.box?.l ?? '—'} / A {item.box?.a ?? '—'}
            {item.box?.widthAtr !== null && item.box?.widthAtr !== undefined
              ? ` · 宽 ${item.box.widthAtr}A`
              : ''}
            {item.box?.er !== null && item.box?.er !== undefined ? ` · ER ${item.box.er}` : ''}
          </div>
          <div className="text-[9px] text-neutral-500 font-mono leading-snug">
            建档 {fmtUtc(item.createdUtc)} · 到期 {fmtUtc(item.deadlineUtc)}
            {item.nextGate ? ` · 下一关 ${item.nextGate}` : ''}
          </div>
          <EvidenceDetail ev={item.evidence} />
        </div>
      ) : null}
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
    return items.filter(
      (x) =>
        (filter === 'ALL' || x.state === filter) &&
        (kw === '' || x.symbol.toUpperCase().includes(kw)),
    );
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
        <div className="px-2 pb-2 space-y-1.5 border-t border-neutral-800/80">
          <div className="text-[9px] text-neutral-500 leading-relaxed pt-1.5">
            观察 ≠ 交易信号：这些标的正在等入场条件，本身不构成下单。条件满足且通过执行门禁后，
            才会进入模拟交易；不必等观察期结束。
          </div>

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

          {view?.definition ? (
            <div className="text-[9px] text-neutral-600 leading-relaxed">{view.definition}</div>
          ) : null}
        </div>
      )}
    </div>
  );
};

export default ObservePanel;
