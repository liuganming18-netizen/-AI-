/**
 * 单个 run 的完整取数
 * ----------------------------------------------------------------------------
 * runId 变化时**取消上一个请求**：chart 有 2.7MB、权益曲线 1.7 万点，
 * 连续点几个 run 而不取消，会让浏览器同时挂着十几兆的响应。
 *
 * 部分失败不算失败：`fetchRunBundle` 用 allSettled，缺哪块就在那块显示原因，
 * 其余部分照常展示（后端某端点 500 不该让整页变白）。
 */

import { useEffect, useRef, useState } from 'react';
import { fetchRunBundle, type BtBundle } from '../../api/backtestReview';

export interface RunDetailState {
  bundle: BtBundle | null;
  loading: boolean;
  /** 整体失败（如 run 不存在）才非空；部分失败看 bundle.errors */
  error: string | null;
}

export function useRunDetail(runId: string | null): RunDetailState {
  const [bundle, setBundle] = useState<BtBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    if (!runId) {
      setBundle(null);
      setLoading(false);
      setError(null);
      return undefined;
    }

    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    // 切换 run 时先清空：否则新 run 的头部会短暂显示上一个 run 的数字
    setBundle(null);

    fetchRunBundle(runId, ac.signal)
      .then((b) => {
        if (ac.signal.aborted) return;
        setBundle(b);
        setLoading(false);
        // 六个子请求全失败才视为整体失败
        const failed = Object.keys(b.errors).length;
        if (failed >= 6) setError(Object.values(b.errors)[0] ?? '取数失败');
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === 'AbortError') return;
        setError((err as Error)?.message || '取数失败');
        setLoading(false);
      });

    return () => ac.abort();
  }, [runId]);

  return { bundle, loading, error };
}
