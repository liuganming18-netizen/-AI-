/**
 * @license
 *
 * 历史研究信号区（ACCUM-EARLY-09-WORK）
 *
 * 目的：让**已入库的研究信号**在现有信号页上可被明确识别、逐条检查，
 * 同时**绝不**混进「当前可交易区」。
 *
 * 三条硬纪律：
 *   ① 只读：本组件只发一个 GET（`scope=all&origin=historical_research`），
 *      **没有任何开仓/下单/执行按钮**，也不调用 `apiPost`。
 *   ② 不冒充：每条都显式标注来源 `historical_research`、角色 `research_only`、
 *      可执行态 `historical_not_executable`、批准状态；缺字段一律显示「未提供」，
 *      不补默认值、不推断。
 *   ③ 与当前区物理分离：本组件渲染在信号列表**之外**的独立折叠区，
 *      当前区的 `data-testid="signal-row"` 不受影响（不会被本组件的行污染）。
 *
 * 数据来源：`GET /api/v1/signals?scope=all&origin=historical_research`
 *   · `has_more` 决定是否还有下一页（后端 `total` 是**本页条数**，不是库内总数，
 *     所以这里只认 `has_more`，并且在没有 offset/cursor 的前提下用放大 `limit` 取全）。
 *   · `research` 子对象（后端 09-WORK 新增）承载运行/配置身份；
 *     后端进程尚未重载时会缺失 ⇒ 如实显示「未提供（后端进程未重载）」。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { FlaskConical, ShieldAlert } from 'lucide-react';
import { apiGet } from '../api/client';
import type { Envelope, RawSignal } from '../api/types';

/** 后端 `item.research`（只出现在 `origin='historical_research'` 的行上）。 */
interface ResearchMeta {
  run_id?: string | null;
  scheme?: string | null;
  approved?: boolean | null;
  config_kind?: string | null;
  config_sha256?: string | null;
  engine_view_sha256?: string | null;
  code_hashes_n?: number | null;
  code_drift?: unknown;
  approval_blockers?: string[] | null;
  gaps?: string[] | null;
  available_at_status?: string | null;
  available_at_reason?: string | null;
  entry_bar_open_ms?: number | null;
  data_version_note?: string | null;
  source_file?: string | null;
  source_file_sha256?: string | null;
}

type ResearchSignal = RawSignal & { research?: ResearchMeta };

const FIRST_LIMIT = 500;
const MAX_LIMIT = 4000;

const fmtIso = (v?: string | null): string =>
  v ? `${v.replace('T', ' ').replace('Z', '')} UTC` : '—';

const fmtMs = (v?: number | null): string =>
  v ? `${new Date(v).toISOString().slice(0, 16).replace('T', ' ')} UTC` : '—';

const short = (v?: string | null, n = 14): string =>
  v ? (v.length > n ? `${v.slice(0, n)}…` : v) : '—';

const Field: React.FC<{ k: string; v: React.ReactNode; mono?: boolean; title?: string }> = ({
  k, v, mono, title,
}) => (
  <div className="flex items-baseline gap-1 min-w-0">
    <span className="text-[9px] text-neutral-500 shrink-0">{k}</span>
    <span
      title={title}
      className={`text-[10px] text-neutral-300 break-all ${mono === false ? '' : 'font-mono'}`}
    >
      {v}
    </span>
  </div>
);

const ResearchRow: React.FC<{ s: ResearchSignal }> = ({ s }) => {
  const m = s.research;
  const approvedText = m ? (m.approved === false ? 'false（未批准）' : String(m.approved)) : '未提供';
  const blockers = (m?.approval_blockers ?? []).filter(Boolean);
  return (
    <div
      data-testid="research-signal-row"
      data-signal-uid={s.signal_id}
      data-origin={s.origin ?? ''}
      data-role={s.role ?? ''}
      data-executable-state={s.executable_state ?? ''}
      data-approved={m ? String(m.approved) : 'unknown'}
      data-run-id={m?.run_id ?? ''}
      data-event-time={s.event_time ?? ''}
      data-confirmed-at={s.confirmed_at ?? ''}
      data-available-at={s.available_at ?? ''}
      data-available-status={m?.available_at_status ?? ''}
      data-config-hash={s.config_hash ?? ''}
      className="rounded border border-neutral-800 bg-neutral-950 px-2 py-1.5 space-y-1"
    >
      {/* 第 1 行：标的 + 来源 + 角色 + 可执行态（三个标签必须同时可见） */}
      <div className="flex items-start justify-between gap-1">
        <span className="text-[11px] text-neutral-100 font-medium break-all">{s.symbol}</span>
        <span className="flex flex-wrap items-center justify-end gap-1 shrink-0 max-w-[70%]">
          <span className="text-[9px] px-1 rounded bg-sky-500/10 text-sky-300 border border-sky-500/30">
            历史研究
          </span>
          <span className="text-[9px] px-1 rounded bg-neutral-800 text-neutral-400 border border-neutral-700 break-all">
            {s.role ?? '未提供'}
          </span>
          <span className="text-[9px] px-1 rounded bg-rose-500/10 text-rose-300 border border-rose-500/30 break-all">
            {s.executable_state ?? '未提供'}
          </span>
        </span>
      </div>

      {/* 第 2 行：确定性 uid（与 API 逐条对齐的键） */}
      <div className="text-[10px] text-neutral-500 font-mono break-all">{s.signal_id}</div>

      {/* 第 3 行：运行 / 配置 / 方案身份（单列：面板很窄，两列会把值截成省略号） */}
      <div className="grid grid-cols-1 gap-y-0.5">
        <Field k="来源" v={s.origin ?? '未提供'} />
        <Field k="角色" v={s.role ?? '未提供'} />
        <Field k="运行" v={m?.run_id ?? '未提供'} />
        <Field k="方案" v={m?.scheme ?? s.arm ?? '—'} />
        <Field k="策略" v={s.strategy_key ?? '—'} />
        <Field k="规则版本" v={s.strategy_version ?? '—'} />
        <Field k="配置哈希" v={short(s.config_hash)} title={s.config_hash ?? ''} />
        <Field k="配置类型" v={m?.config_kind ?? '未提供'} />
        <Field k="引擎视图" v={short(m?.engine_view_sha256)} title={m?.engine_view_sha256 ?? ''} />
        <Field k="批准" v={approvedText} mono={false} />
      </div>

      {/* 第 4 行：四个时刻（互不替代）+ 可得性 */}
      <div className="grid grid-cols-1 gap-y-0.5 pt-0.5 border-t border-neutral-800/80">
        <Field k="事件" v={fmtIso(s.event_time)} />
        <Field k="确认" v={fmtIso(s.confirmed_at)} />
        <Field k="可得" v={s.available_at ? fmtIso(s.available_at) : `空（${m?.available_at_status ?? '未提供'}）`} />
        <Field k="确认bar" v={`${fmtMs(s.bar_open_time)} → ${fmtMs(s.bar_close_time)}`} />
        <Field k="入场bar开盘" v={m?.entry_bar_open_ms ? fmtMs(m.entry_bar_open_ms) : '未提供'} />
        <Field k="数据版本" v={s.data_version ?? '空（未编造）'} />
      </div>

      {/* 第 5 行：不可执行原因 + 批准阻塞项（逐条可读，不做摘要省略） */}
      <div className="pt-0.5 border-t border-neutral-800/80 space-y-0.5">
        <div className="text-[9px] text-neutral-500">不可执行原因</div>
        <div className="text-[10px] text-rose-300/90 leading-relaxed">{s.actionable_reason ?? '未提供'}</div>
        {blockers.length > 0 && (
          <div className="text-[9px] text-neutral-500 leading-relaxed">
            批准阻塞：{blockers.join('；')}
          </div>
        )}
      </div>
    </div>
  );
};

export const ResearchSignalSection: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<ResearchSignal[]>([]);
  const [meta, setMeta] = useState<{ hasMore: boolean | null; usedLimit: number; fetchedAt: string | null }>(
    { hasMore: null, usedLimit: 0, fetchedAt: null },
  );
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 没有 offset/cursor ⇒ 用放大 limit 取全；`has_more` 决定是否再放大。
      let limit = FIRST_LIMIT;
      let env = await apiGet<Envelope<ResearchSignal>>(
        `/signals?scope=all&origin=historical_research&limit=${limit}`,
      );
      let guard = 0;
      while (env?.has_more && limit < MAX_LIMIT && guard < 3) {
        limit = Math.min(limit * 4, MAX_LIMIT);
        guard += 1;
        env = await apiGet<Envelope<ResearchSignal>>(
          `/signals?scope=all&origin=historical_research&limit=${limit}`,
        );
      }
      const items = Array.isArray(env?.items) ? env.items : [];
      setRows(items);
      setMeta({ hasMore: env?.has_more ?? null, usedLimit: limit, fetchedAt: env?.as_of ?? null });
      setLoaded(true);
    } catch (err) {
      // 传输/业务失败：清空列表 + 如实显示原因，绝不把错误当「没有研究信号」
      setRows([]);
      setLoaded(false);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && !loaded && !loading && !error) void load();
  }, [open, loaded, loading, error, load]);

  const identityMissing = rows.length > 0 && rows.every((r) => !r.research);
  const count = rows.length;

  return (
    <details
      data-testid="research-signals"
      data-research-count={count}
      data-research-loaded={loaded ? '1' : '0'}
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="border-t border-neutral-800 bg-neutral-900 shrink-0"
    >
      <summary className="px-3 py-2 cursor-pointer select-none flex items-center gap-1.5 text-[11px] text-neutral-300 hover:text-neutral-100">
        <FlaskConical className="w-3.5 h-3.5 text-sky-400 shrink-0" />
        <span className="font-medium">历史研究（不可交易）</span>
        <span data-testid="research-signals-meta" className="text-[10px] text-neutral-500 font-mono">
          {loading
            ? '读取中…'
            : loaded
              ? `${count} 条 · has_more=${String(meta.hasMore)} · limit=${meta.usedLimit}`
              : '未读取'}
        </span>
        <span className="ml-auto text-[9px] text-neutral-600">
          {open ? '收起 ▲' : '展开 ▼'}
        </span>
      </summary>

      <div className="px-2 pb-2 space-y-1.5 max-h-[46vh] overflow-y-auto">
        {/* 语义声明：这一段不是交易信号，不能被当成当前可交易区 */}
        <div className="flex items-start gap-1.5 rounded border border-amber-500/25 bg-amber-500/10 px-2 py-1">
          <ShieldAlert className="w-3 h-3 text-amber-400 shrink-0 mt-0.5" />
          <div className="text-[10px] text-amber-300/90 leading-relaxed">
            研究信号：来源 <span className="font-mono">historical_research</span>、角色{' '}
            <span className="font-mono">research_only</span>、可执行态{' '}
            <span className="font-mono">historical_not_executable</span>、批准{' '}
            <span className="font-mono">false</span>。
            历史记录不冒充自然实时信号，不进入当前交易区，也不构成下单依据。
          </div>
        </div>

        {error && (
          <div data-testid="research-signals-error" className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[10px] text-rose-300">
            研究信号读取失败：{error}
          </div>
        )}

        {identityMissing && (
          <div data-testid="research-identity-missing" className="rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-[10px] text-neutral-400 leading-relaxed">
            运行/配置身份字段未提供：当前后端进程尚未加载本单新增的字段映射（进程未重启）。
            其余字段（uid/来源/角色/可执行态/时刻/原因）来自既有字段，可直接核对。
          </div>
        )}

        {!loading && loaded && count === 0 && (
          <div className="rounded border border-dashed border-neutral-800 bg-neutral-950 px-2 py-3 text-center text-[10px] text-neutral-500">
            库里没有历史研究信号（空是真实结果，不是读取失败）
          </div>
        )}

        {rows.map((s) => (
          <ResearchRow key={s.signal_id} s={s} />
        ))}
      </div>
    </details>
  );
};

export default ResearchSignalSection;
