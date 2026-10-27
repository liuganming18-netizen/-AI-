/**
 * 跨图十字线总线
 * ----------------------------------------------------------------------------
 * 回测复盘页有五张独立的图（K 线 / 成交量 / 持仓量 / 净值 / 回撤），每张都是
 * **独立的 lightweight-charts 实例**。官方库只保证「同一个实例内的多个 pane」
 * 十字线贯穿，跨实例不会自动联动 —— 所以鼠标停在 K 线上时，另外四张图毫无反应。
 *
 * 这里用一个极轻的发布订阅把五张图串起来：任一图悬停 → 广播「时点（UTC 秒）」→
 * 其余各图把十字线落到自己的最近数据点上。
 *
 * 三条纪律：
 *   1. **带来源 id**：广播时跳过发起者，避免「自己收到自己的广播」造成回环。
 *   2. **同步中不再广播**：`emitting` 置位期间收到的嵌套调用直接丢弃 ——
 *      程序化 `setCrosshairPosition` 会让官方库派发新的 crosshairMove 事件。
 *   3. **只传时点（毫秒），不传值**：每张图的值域不同（价格 / 张数 / 权益 / 回撤百分比），
 *      由各图自己按最近数据点取值，总线不承担单位换算。
 */

export type CrosshairListener = (timeMs: number | null, source: string) => void;
export type ViewportListener = (fromMs: number, toMs: number, source: string) => void;

export interface CrosshairBus {
  /**
   * 注册监听。`id` 是来源标识（同一张图用同一个 id），广播时会被跳过。
   * 返回注销函数。
   */
  register(id: string, listener: CrosshairListener): () => void;
  /** 广播一个时点；`null` 表示鼠标离开绘图区，各图应清除十字线。 */
  emit(source: string, timeMs: number | null): void;
  /** 最近一次广播的时点（新挂载的图可用它立即对齐）。 */
  current(): number | null;
  /**
   * 视窗联动：主图（K 线区）可见范围变化时，把同一段真实时间推给净值 / 回撤。
   *
   * 为什么必须做：五张图的默认视窗各不相同（K 线只显示最近 150 根，净值图铺满整个
   * run 窗口）。视窗不联动时，鼠标停在 K 线上的时点根本不在净值图的可见范围内，
   * 十字线会被官方库的「时点不可见」守卫挡掉 —— 看起来就像没联动。
   */
  registerViewport(id: string, listener: ViewportListener): () => void;
  emitViewport(source: string, fromMs: number, toMs: number): void;
}

export function createCrosshairBus(): CrosshairBus {
  const entries: { id: string; listener: CrosshairListener }[] = [];
  const viewports: { id: string; listener: ViewportListener }[] = [];
  let last: number | null = null;
  let emitting = false;
  let emittingViewport = false;

  return {
    register(id, listener) {
      entries.push({ id, listener });
      return () => {
        const i = entries.findIndex((e) => e.listener === listener);
        if (i >= 0) entries.splice(i, 1);
      };
    },

    emit(source, timeMs) {
      // 程序化定位会再次派发 crosshairMove ⇒ 期间的一切广播都丢弃，防止无限回环
      if (emitting) return;
      emitting = true;
      last = timeMs;
      try {
        for (const entry of entries) {
          if (entry.id === source) continue;
          try {
            entry.listener(timeMs, source);
          } catch {
            /* 单张图定位失败不得影响其它图 */
          }
        }
      } finally {
        emitting = false;
      }
    },

    current: () => last,

    registerViewport(id, listener) {
      viewports.push({ id, listener });
      return () => {
        const i = viewports.findIndex((e) => e.listener === listener);
        if (i >= 0) viewports.splice(i, 1);
      };
    },

    emitViewport(source, fromMs, toMs) {
      if (emittingViewport) return;
      if (!(toMs > fromMs)) return;
      emittingViewport = true;
      try {
        for (const entry of viewports) {
          if (entry.id === source) continue;
          try {
            entry.listener(fromMs, toMs, source);
          } catch {
            /* 同上：单张图同步失败不影响其它图 */
          }
        }
      } finally {
        emittingViewport = false;
      }
    },
  };
}

/**
 * 在按时间升序排列的数据里找「离目标最近」的一项。
 *
 * 用二分而不是线性扫描：净值序列有 1.7 万个点，鼠标移动是每帧触发的，
 * 线性扫一遍等于每秒几百万次比较。
 *
 * `toleranceMs` 之外视为「该图在此处没有数据」⇒ 返回 `null`（调用方清除十字线，
 * 而不是把十字线硬拉到一个相差很远的点上）。
 */
export function nearestByTime<T>(
  sorted: T[],
  targetMs: number,
  getMs: (item: T) => number,
  toleranceMs: number,
): T | null {
  if (sorted.length === 0 || !Number.isFinite(targetMs)) return null;
  let lo = 0;
  let hi = sorted.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (getMs(sorted[mid]) < targetMs) lo = mid + 1;
    else hi = mid;
  }
  const cand = [sorted[lo], sorted[Math.max(0, lo - 1)]];
  let best: T | null = null;
  let bestDiff = Infinity;
  for (const item of cand) {
    const diff = Math.abs(getMs(item) - targetMs);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = item;
    }
  }
  return best && bestDiff <= toleranceMs ? best : null;
}
