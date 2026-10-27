/**
 * 媒体查询 hook（响应式布局的唯一 JS 入口）
 * ----------------------------------------------------------------------------
 * 为什么需要它：纯 CSS 能处理「长什么样」，但处理不了「行为」——
 *   · 抽屉关着时不能让 Tab 键跳进看不见的导航项（要 `inert`）；
 *   · 抽屉打开时要锁背景滚动、把焦点移进抽屉、Esc 关闭；
 *   · 桌面端不能把 aside 标成 `inert`（否则整块导航不可点）。
 * 这些都必须知道「当前是不是移动端」，因此保留这一个极小的 hook。
 *
 * 断点与 CSS 严格同源：`useIsMobile()` 用 767.98px（= CSS `max-width: 767px`），
 * `useIsRail()` 用 1023.98px（= CSS `max-width: 1023px`）。改断点必须两处一起改。
 */

import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  // 首帧直接同步读取，避免「先按桌面渲染一帧、再跳成移动端」的闪烁
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    // Safari < 14 只有 addListener；两者都挂会重复触发，故按能力择一
    if (mql.addEventListener) {
      mql.addEventListener('change', handler);
      return () => mql.removeEventListener('change', handler);
    }
    mql.addListener(handler);
    return () => mql.removeListener(handler);
  }, [query]);

  return matches;
}

/** < 768px：导航变成抽屉（off-canvas）。 */
export const useIsMobile = (): boolean => useMediaQuery('(max-width: 767.98px)');

/** < 1024px：导航收成图标导轨 / 抽屉，只显示图标。 */
export const useIsNarrow = (): boolean => useMediaQuery('(max-width: 1023.98px)');
