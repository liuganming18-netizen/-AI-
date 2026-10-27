/**
 * 回测列表（左栏）
 * ----------------------------------------------------------------------------
 * 576 条，用固定行高的**极简虚拟滚动**：只渲染可视区 + 上下各 4 行缓冲。
 * 卡片高度写死在 CSS（100px）+ 8px 间距 = 108px 步长，两处必须同步改。
 *
 * 列表卡片**不显示 `created_at`**：历史运行大多没有落盘创建时刻（CODE-REVIEW-02
 * 修复后，有 `inputs.json` 的 run 后端会给**真实** `timestamp_created`，没有的给
 * null —— 两种都不如 `run_id` 后缀的 epoch 秒稳定）。只显示后缀推出的「运行时间」，
 * 真实回测窗口在选中后由 `chart.window` 给。
 * CODE-REVIEW-02：`registered === false` 的历史批量产物显示「未登记」小徽标。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Search, Plus, RefreshCw } from 'lucide-react';
import type { BtRunListItem } from '../../api/backtestReview';
import {
  parseRunId,
  runEpochMs,
  statusLabel,
  statusTone,
  symbolBadgeText,
  symbolColor,
  symbolLabel,
} from '../../utils/runMeta';
import { DASH, fmtDateTime, fmtPct } from '../../utils/labFormat';

const ITEM_H = 108;

export const RunListItem: React.FC<{
  run: BtRunListItem;
  selected: boolean;
  onSelect: (runId: string) => void;
}> = React.memo(({ run, selected, onSelect }) => {
  const symbol = run.symbols?.[0] ?? parseRunId(run.run_id).symbol;
  const tf = run.window?.signal_timeframe || DASH;
  const ret = run.net_return_pct;
  const trades = run.trades_count ?? 0;
  const noTrades = trades === 0;
  const tone = statusTone(run.status);

  const retClass = noTrades
    ? 'ds-runitem__ret--muted'
    : (ret ?? 0) > 0
      ? 'ds-runitem__ret--pos'
      : (ret ?? 0) < 0
        ? 'ds-runitem__ret--neg'
        : 'ds-runitem__ret--muted';

  const badgeClass =
    tone === 'ok'
      ? 'ds-badge ds-badge--pos'
      : tone === 'warn'
        ? 'ds-badge ds-badge--warn'
        : tone === 'bad'
          ? 'ds-badge ds-badge--neg'
          : 'ds-badge ds-badge--neutral';

  return (
    <button
      type="button"
      className="ds-runitem"
      aria-selected={selected}
      data-run-id={run.run_id}
      onClick={() => onSelect(run.run_id)}
      title={run.run_id}
    >
      <span className="ds-runitem__top">
        <span
          className="ds-runitem__avatar"
          style={{ background: symbolColor(symbol) }}
          aria-hidden="true"
        >
          {symbolBadgeText(symbol)}
        </span>
        <span className="ds-runitem__title">
          {symbolLabel(symbol)} {tf} 回测
        </span>
        <span className={badgeClass}>{statusLabel(run.status)}</span>
        {/* CODE-REVIEW-02：未登记标记（无 run.json 的历史批量产物）。
            状态徽标是「按产物证据推断」的结果，这枚小徽标说明它不是正式档案。 */}
        {run.registered === false && (
          <span
            className="ds-badge ds-badge--neutral"
            title={run.record_note ?? '该运行无 run.json：历史批量产物，未登记（只读参考）'}
            data-testid="runitem-unregistered"
          >
            未登记
          </span>
        )}
      </span>

      <span className="ds-runitem__meta">
        运行 {fmtDateTime(runEpochMs(run.run_id))}
      </span>
      <span className="ds-runitem__meta">
        策略：{run.strategy_id || DASH}
      </span>

      <span className="ds-runitem__foot">
        <span className="ds-runitem__param">
          {noTrades ? '无成交' : `成交 ${trades} 笔`}
        </span>
        <span className={`ds-runitem__ret ${retClass}`}>
          {noTrades ? '无成交' : fmtPct(ret)}
        </span>
      </span>
    </button>
  );
});
RunListItem.displayName = 'RunListItem';

export interface RunListPanelProps {
  runs: BtRunListItem[];
  total: number;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  loading: boolean;
  error: string | null;
  onReload: () => void;
  query: string;
  onQueryChange: (v: string) => void;
  strategy: string;
  onStrategyChange: (v: string) => void;
  symbol: string;
  onSymbolChange: (v: string) => void;
  strategies: { id: string; count: number }[];
  symbols: { base: string; count: number }[];
  onCreate: () => void;
}

export const RunListPanel: React.FC<RunListPanelProps> = ({
  runs,
  total,
  selectedRunId,
  onSelect,
  loading,
  error,
  onReload,
  query,
  onQueryChange,
  strategy,
  onStrategyChange,
  symbol,
  onSymbolChange,
  strategies,
  symbols,
  onCreate,
}) => {
  const listRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(640);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setViewH(el.clientHeight));
    ro.observe(el);
    setViewH(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  // 换筛选条件后回到顶部，避免停在一个空白区间
  useEffect(() => {
    listRef.current?.scrollTo({ top: 0 });
    setScrollTop(0);
  }, [query, strategy, symbol]);

  const count = runs.length;
  const start = Math.max(0, Math.floor(scrollTop / ITEM_H) - 4);
  const end = Math.min(count, Math.ceil((scrollTop + viewH) / ITEM_H) + 4);
  const slice = runs.slice(start, end);

  return (
    <aside className="ds-bt-side">
      <div className="ds-bt-side__head">
        <span className="ds-bt-side__title">回测复盘</span>
        <button type="button" className="ds-btn ds-btn--primary ds-btn--sm" onClick={onCreate}>
          <Plus className="w-3.5 h-3.5" />
          新建回测
        </button>
      </div>

      <div className="ds-bt-side__filters">
        <label className="ds-bt-search">
          <Search />
          <input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="搜索策略、币种或时间范围…"
            aria-label="搜索回测"
          />
        </label>

        <div className="ds-bt-side__selects">
          <select
            className="ds-select"
            value={strategy}
            onChange={(e) => onStrategyChange(e.target.value)}
            aria-label="按策略筛选"
          >
            <option value="">全部策略</option>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}（{s.count}）
              </option>
            ))}
          </select>
          <select
            className="ds-select"
            value={symbol}
            onChange={(e) => onSymbolChange(e.target.value)}
            aria-label="按币种筛选"
          >
            <option value="">全部币种</option>
            {symbols.map((s) => (
              <option key={s.base} value={s.base}>
                {s.base}（{s.count}）
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="ds-runlist" ref={listRef} onScroll={onScroll}>
        {loading ? (
          <div className="ds-empty">
            <div className="ds-skeleton" style={{ height: 100, marginBottom: 8 }} />
            <div className="ds-skeleton" style={{ height: 100, marginBottom: 8 }} />
            <div className="ds-skeleton" style={{ height: 100 }} />
          </div>
        ) : error ? (
          <div className="ds-empty">
            <div className="ds-empty__title">列表取数失败</div>
            <div className="ds-empty__hint">{error}</div>
            <button type="button" className="ds-btn ds-btn--secondary ds-btn--sm" onClick={onReload}>
              <RefreshCw className="w-3.5 h-3.5" />
              重试
            </button>
          </div>
        ) : count === 0 ? (
          /* 空态三问：空的是什么 / 为什么空 / 怎么让它不空。
           * 「一条都没有」和「筛掉了」是两种完全不同的处境，必须分开写 ——
           * 统一写「暂无数据」会让用户以为后端挂了。 */
          <div className="ds-empty">
            <div className="ds-empty__title">
              {total === 0 ? '还没有任何回测记录' : '没有匹配的回测'}
            </div>
            <div className="ds-empty__hint">
              {total === 0
                ? '这个回测根目录下还没有 run。先在工作台发起一次回测，或确认后端回测根目录是否指对。'
                : `共 ${total} 个 run，当前搜索 / 策略 / 币种条件下为空。`}
            </div>
            {total === 0 ? (
              <button type="button" className="ds-btn ds-btn--primary ds-btn--sm" onClick={onCreate}>
                <Plus className="w-3.5 h-3.5" />
                新建回测
              </button>
            ) : (
              <button
                type="button"
                className="ds-btn ds-btn--secondary ds-btn--sm"
                onClick={() => {
                  onQueryChange('');
                  onStrategyChange('');
                  onSymbolChange('');
                }}
              >
                清空筛选
              </button>
            )}
          </div>
        ) : (
          <div className="ds-runlist__spacer" style={{ height: count * ITEM_H }}>
            <div style={{ transform: `translateY(${start * ITEM_H}px)` }}>
              {slice.map((r) => (
                <RunListItem
                  key={r.run_id}
                  run={r}
                  selected={r.run_id === selectedRunId}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
};
