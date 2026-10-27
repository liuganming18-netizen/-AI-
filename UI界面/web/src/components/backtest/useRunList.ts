/**
 * 回测 run 列表取数
 * ----------------------------------------------------------------------------
 * 一次拉全量（576 条，约 500KB），筛选/搜索在前端做 —— 后端是单线程 HTTPServer，
 * 每次输入都打一次请求会把服务堵住。列表也**绝不**为每一行再拉 detail。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchRunList, type BtRunListItem } from '../../api/backtestReview';
import { parseRunId, splitSymbol } from '../../utils/runMeta';

export interface RunListState {
  runs: BtRunListItem[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** 策略下拉的选项（来自真实数据，不写死） */
  strategies: { id: string; count: number }[];
  /** 币种下拉的选项 */
  symbols: { base: string; count: number }[];
}

export function useRunList(): RunListState {
  const [runs, setRuns] = useState<BtRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);

    fetchRunList(1000, ac.signal)
      .then((env) => {
        if (ac.signal.aborted) return;
        setRuns(Array.isArray(env.items) ? env.items : []);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === 'AbortError') return;
        setError((err as Error)?.message || '回测列表取数失败');
        setLoading(false);
      });

    return () => ac.abort();
  }, [nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  /** 策略 / 币种选项：按出现次数排序，只保留真实存在的。 */
  const { strategies, symbols } = useMemo(() => {
    const sMap = new Map<string, number>();
    const bMap = new Map<string, number>();
    for (const r of runs) {
      const sid = r.strategy_id || parseRunId(r.run_id).strategy || '—';
      sMap.set(sid, (sMap.get(sid) ?? 0) + 1);
      const sym = r.symbols?.[0] ?? parseRunId(r.run_id).symbol;
      const { base } = splitSymbol(sym);
      if (base) bMap.set(base, (bMap.get(base) ?? 0) + 1);
    }
    return {
      strategies: [...sMap.entries()]
        .map(([id, count]) => ({ id, count }))
        .sort((a, b) => b.count - a.count),
      symbols: [...bMap.entries()]
        .map(([base, count]) => ({ base, count }))
        .sort((a, b) => b.count - a.count || a.base.localeCompare(b.base)),
    };
  }, [runs]);

  return { runs, loading, error, reload, strategies, symbols };
}
