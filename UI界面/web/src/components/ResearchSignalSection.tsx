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
  const [showDetail, setShowDetail] = useState(false);
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
      className="rounded border border-neutral-800/80 bg-neutral-950 p-2 space-y-1 hover:border-neutral-700 transition-colors"
    >
      {/* 顶栏：标的与状态徽章 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-[12px] text-neutral-100 font-semibold font-mono">{s.symbol}</span>
          <span className="text-[9px] px-1 py-0.2 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20 font-sans">
            历史研究
          </span>
          <span className="text-[9px] px-1 py-0.2 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 font-sans">
            不可执行
          </span>
        </div>
        <div className="text-[10px] text-neutral-400 font-mono">
          {fmtIso(s.event_time)}
        </div>
      </div>

      {/* 中栏：方案与拦截说明 */}
      <div className="flex items-center justify-between text-[10px] text-neutral-400 pt-0.5">
        <span className="truncate max-w-[60%]">
          方案: <span className="text-neutral-300 font-mono">{m?.scheme ?? s.arm ?? '—'}</span>
          {blockers.length > 0 && <span className="text-amber-400/90 ml-1.5">({blockers.join(';')})</span>}
        </span>
        <button
          type="button"
          onClick={() => setShowDetail((v) => !v)}
          className="text-[9px] text-neutral-500 hover:text-neutral-300 font-sans underline"
        >
          {showDetail ? '简收' : '审计参数'}
        </button>
      </div>

      {/* 不可执行原因单行紧凑 */}
      {s.actionable_reason && (
        <div className="text-[9px] text-rose-300/80 truncate" title={s.actionable_reason}>
          原因: {s.actionable_reason}
        </div>
      )}

      {/* 展开的精细审计字段 */}
      {showDetail && (
        <div className="pt-1 mt-1 border-t border-neutral-900 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[9px] text-neutral-500">
          <Field k="信号ID" v={short(s.signal_id, 10)} title={s.signal_id} />
          <Field k="运行ID" v={short(m?.run_id, 10)} title={m?.run_id ?? ''} />
          <Field k="配置类型" v={m?.config_kind ?? '未提供'} />
          <Field k="批准" v={approvedText} mono={false} />
          <Field k="确认时间" v={fmtIso(s.confirmed_at)} />
          <Field k="配置哈希" v={short(s.config_hash, 8)} title={s.config_hash ?? ''} />
        </div>
      )}
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
        {/* 顶部紧凑语义声明 */}
        <div className="flex items-center gap-1.5 rounded border border-neutral-800 bg-neutral-950/80 px-2 py-1 text-[10px] text-neutral-400">
          <ShieldAlert className="w-3 h-3 text-amber-500 shrink-0" />
          <span>历史研究信号仅作回溯比对，不进入当前实盘交易区</span>
        </div>

        {error && (
          <div data-testid="research-signals-error" className="rounded border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[10px] text-rose-300">
            读取失败：{error}
          </div>
        )}

        {identityMissing && (
          <div data-testid="research-identity-missing" className="rounded border border-neutral-850 bg-neutral-950 px-2 py-1 text-[10px] text-neutral-500">
            未加载扩展审计映射（按既有基础字段展示）
          </div>
        )}

        {!loading && loaded && count === 0 && (
          <div className="rounded border border-dashed border-neutral-850 bg-neutral-950 px-2 py-2 text-center text-[10px] text-neutral-500">
            暂无历史研究记录
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
