# 前端公共源码改动记录 · BT-KLINE-CENTER-01（K 线铺满 / 不居中）

> 用户输入：截图 `截屏2026-10-24 18.24.47.png`（ACHUSDT 1h，18 根挤在左侧、右侧大片空白）+ 文字「K线1小时线居中」。
> 本记录只讲**这一轮**改了什么、凭什么改、怎么验证、怎么回滚。文件整体的历史改动不在本单范围。

---

## 0. 身份（版本只认文件 SHA-256）

| 项 | 值 |
|---|---|
| 文件 | `UI界面/web/src/components/LightweightChart.tsx` |
| 改动后 SHA-256 | `1a461ddfe2350ff5a016a98a59d0a4b5b8a2af29622947407782d10fb3638c65` |
| 改动后行数 | 2,384 |
| 记录时刻（UTC） | 2026-10-24T10:50:41Z |
| 相对 `git HEAD`（`2a13325`） | 750 insertions / 119 deletions —— **含前几轮工单**（UI-LINK-01 / BT-UI-01/02 等），不是本单独有 |
| 类型检查 | `tsc --noEmit` 无输出（通过） |

**为什么没有 `.orig` 对照文件**：该文件在 `git` 里已累计 750 行未提交改动（属前几轮），
拿 `git show HEAD:...` 当「改动前」会把别人的工作算进本单。所以本记录以**精确的 before/after 代码块**
界定本轮范围（共 4 处），SHA 只用于「此刻文件是这一版」的身份核对。

---

## 1. 现象与复现（先证缺陷存在，再动手）

**现象**：ACHUSDT 1h 只有 18 根，右侧却空出一大片，肉眼像「K 线没导全 / 没居中」。

**先排除两个看似合理的解释**（都实测排除）：
- 「数据本身只有 18 根」——对，但首屏是**铺满**的（右空 0.9%），所以不是数据问题；
- 「恢复了旧视窗」——`LabChartPanel` 传的 `initialViewport={null}`，没有旧视窗可恢复。

**复现路径**：视口 `1668 → 1200 → 1668`（拖动窗口 / 开关侧栏 / 页面缩放都会走同一条 `setWidth` 链）。

| 步骤 | 绘图区宽 | 可见根数 | `barSpacing` | 右侧空白 |
|---|---|---|---|---|
| `1_w1668`（刚加载） | 820 | 18 | 46.0px | 6px / **0.9%** |
| `2_w1200` | 402 | 18 | 22.0px | 3px / 0.7% |
| `3_back1668`（**缺陷**） | 820 | **10** | 46.0px | **370px / 45%** |

证据：`out/21_K线居中/resize_test.json`、`resize_1_w1668.png`、`resize_3_back1668.png`。

## 2. 根因（不是猜的，有官方源码 + 中间态读数）

`baseOptions` 里 `lockVisibleTimeRangeOnResize: true`。官方 `lightweight-charts.development.mjs`
的 `_private__setWidth` 里：

```js
if (options.lockVisibleTimeRangeOnResize && oldWidth !== 0) {
    barSpacing = barSpacing * newWidth / oldWidth;      // 锁的是「时间窗」，不是 barSpacing
}
if (options.fixLeftEdge) {
    // 视窗贴住左缘时，用 rightOffset 做补偿
    rightOffset -= Math.round((oldWidth - newWidth) / barSpacing) + 1;
}
```

- 前半段：**锁定的是可见时间范围**（`barSpacing` 按绘图区宽度比例重算）。原注释写「保留 `barSpacing`」是**反的**，本轮一并改正。
- 后半段：`fixLeftEdge: true` 的补偿在**变宽**时（`oldWidth < newWidth`）变成**加**，
  把 `rightOffset` 顶大 —— 这是空白真正的来源。

中间态实测（`[RO]` 临时日志，已删除）：

```
resize 到 1200：绘图区 402px，barSpacing 22.33 = 402/18   ← 18 根刚好铺满，正确
resize 回 1668：绘图区 820px，barSpacing 45.55 = 820/18   ← 比例正确，18 根仍应铺满
                但 getVisibleRange() 只剩末 10 根         ← 多出来的只有 rightOffset（2 → 10）
```

> 注意 `getVisibleRange()` 会被官方库**夹到数据首末**（`Math.min/max`），所以它看起来「变窄」，
> 实际是窗口右移了 8 根。用「可见范围是否覆盖整个数据域」做判据会被这个夹取骗到 —— 本轮最终**不用**该判据。

## 3. 改动（4 处，全部在 `LightweightChart.tsx`）

### 3.1 新增常量 `NARROW_DATA_BARS`

```ts
const RIGHT_OFFSET_BARS = 2;

/**
 * 「数据太窄，不给右侧呼吸位」的根数阈值。
 *
 * `rightOffset` 的单位是**根**，不是像素：2 根空档放在 5,000 根的数据上几乎看不见，
 * 放在 18 根（一个 1h 回测窗口）上就是 2/18 ≈ 11% 的右侧空白 —— 视觉上就是「没铺满」。
 * 实测用户截图那一轮 ACHUSDT 1h 只有 18 根，右侧空 12%（= 2 根 + 取整误差）。
 * 因此：数据少于一屏（本阈值的量级）时把呼吸位压到 0，优先「铺满」。
 */
const NARROW_DATA_BARS = 50;
```

### 3.2 `ChartSyncMember` 增加 `points`

```ts
export interface ChartSyncMember {
  chart: IChartApi;
  domain: { from: number; to: number } | null;
  /**
   * 本图**真实数据点数**（蜡烛根数 / 网格点数）。
   * 只用于「尺寸变化后数据还装不装得下一屏」的判断（见 `ResizeObserver` 收口）。
   */
  points: number;
  disposed: boolean;
  role: 'leader' | 'follower';
}
```

初始化 `{ chart, domain: null, points: 0, disposed: false, role }`；三处数据落位同步写入
（K 线 `candleData.length`、OI 与成交量 `gridData.length`）。

### 3.3 `baseOptions` 的注释纠正（行为未改，仍是 `true`）

```ts
/**
 * `true` = 尺寸变化时**锁定可见时间范围**，按宽度比例重算 `barSpacing`。
 * 官方源码：`barSpacing = barSpacing * newWidth / oldWidth;`
 * 好处是缩放级别随宽度自适应、不会「跳一下」；代价是容器变宽时若数据本身
 * 比一屏窄，右侧会重新长出空白 —— 由挂载 effect 里的 `ResizeObserver` 收口。
 */
lockVisibleTimeRangeOnResize: true,
```

### 3.4 挂载 effect 内新增 `ResizeObserver`（核心）

```ts
let lastW = 0;
const ro = new ResizeObserver((entries) => {
  const w = Math.round(entries[0]?.contentRect?.width ?? 0);
  if (!w || w === lastW) return;
  const prevW = lastW;
  lastW = w;
  if (!prevW || member.disposed) return;   // 首帧只记宽度：首屏落位另有其路

  if (member.role === 'follower') {
    alignToLeader(syncHub, member);
    return;
  }
  if (!followRef.current || !member.domain || !member.points) return;
  const domain = member.domain;

  const ts = chart.timeScale();
  const barSpacing = ts.options().barSpacing;
  const plotW = safeCall(() => ts.width()) ?? 0;
  const slots = barSpacing > 0 ? plotW / barSpacing : 0;

  // 复位被 fixLeftEdge 顶大的 rightOffset
  const desiredOffset = member.points <= NARROW_DATA_BARS ? 0 : RIGHT_OFFSET_BARS;
  if (ts.options().rightOffset !== desiredOffset) {
    safeTimeScaleCall(() => ts.applyOptions({ rightOffset: desiredOffset }));
  }

  if (slots > 0 && member.points + desiredOffset <= slots + 0.5) {
    // 当前缩放级别下全部数据装得下 ⇒ 按数据域重新铺满（与首屏落位同一套逻辑）
    applyTimeRangeTo(member, domain.from, domain.to);
  } else {
    // 用户已放大到局部 ⇒ 只把右缘贴回最新根，缩放级别不动
    safeTimeScaleCall(() => ts.scrollToPosition(desiredOffset, false));
  }
});
ro.observe(container);
// 清理里：ro.disconnect();
```

**为什么用 `points + offset <= slots` 而不是「可见范围是否覆盖数据域」**：
后者会被 `getVisibleRange()` 的夹取骗到（见 §2）；前者只依赖两个可读数值
（`timeScale().width()` 与 `options().barSpacing`），并且天然区分「用户放大过」（数据比一屏多）。

**只在 `followRef.current === true` 时动**：用户已拖到历史视窗就一律不碰（这是既有语义，未改）。

## 4. 验证（全部机械复算，脚本在 `code/web_e2e/`）

### 4.1 正例：铺满（`06_resize_center.js` → `07_measure_candles.py` → `10_center_verdict.py`）

判据：`left/right_gap_ratio <= 5%` **且** `candles_visible == 18`（18 = `time_range_1h` 跨度 17h + 1）。

| 步骤 | 绘图区宽 | 可见根数 | `barSpacing` | 左空 | 右空 | 判定 |
|---|---|---|---|---|---|---|
| `0_load_1668` | 820 | 18 | 46.0 | 0.6% | 0.9% | PASS |
| `1_narrow_1200` | 402 | 18 | 22.0 | 0.2% | 0.7% | PASS |
| `2_back_1668`（**原缺陷步**） | 820 | **18** | 46.0 | 0.6% | **0.9%** | PASS |
| `3_narrow_1080` | 746 | 18 | 41.0 | 0.5% | 0.8% | PASS |
| `4_widen_1920` | 1072 | 18 | 60.0 | 0.6% | 0.7% | PASS |
| `5_drag_down_to_1560`（连续 6 次 ×60px 拖动） | 712 | 18 | 40.0 | 0.6% | 0.8% | PASS |

修复前同一步：10 根 / 右空 45%。→ 右空 45% → 0.9%，根数 10 → 18。

### 4.2 反向保护：不得吃掉用户的缩放级别（`08_resize_guards.js`）

判据：`|spacing_after_back − spacing_zoomed| / spacing_zoomed <= 10%`。

| 场景 | 放大后间距 | 改窄再改回后 | 相对变化 | 判定 |
|---|---|---|---|---|
| 大数据集（DYN01 / AVAX，2548 根 1h） | 9.0px | 9.0px | 0.0% | PASS |
| 窄数据集（ACHUSDT，18 根 1h） | 81.0px | 81.0px | 0.0% | PASS |

即：放大后改窗口宽度，缩放级别**完全不变**，只贴右缘。

### 4.3 回归（既有脚本，未改）

| 脚本 | 结果 |
|---|---|
| `05_symbol_select.js` | 切标的 → `BCHUSDT`；切周期 15m/30m/1h/4h 全部生效；无 `console_errors` |
| `03_interact.js` | 悬停 / 点笔 / 切标的 / 切周期全部有响应；`console_errors: []` |
| `02_link_pixel_probe.js` | 三图叠放几何正常（3 张 `[data-chart]` 容器） |

### 4.4 三图联动对齐（像素级）

改宽复原后整块面板截图 `out/21_K线居中/align_after_resize_full.png`（896×456），
逐列统计高饱和像素求柱间空隙：

```
K 线   空隙: 39→50, 84→96, 130→141, 175→187, 221→233, 267→278, 312→324, 358→369
成交量 空隙: 43→45, 89→91, 134→136, 180→182, 226→228, 271→273, 317→319, 362→364
OI     空隙: 43→45, 89→91, 134→136, 180→182, 226→228, 271→273, 317→319, 362→364
```

成交量 / OI 的柱边界与蜡烛边界**逐条对齐（≤0.5px）** ⇒ 收口没有把跟随图的时间窗带偏。

> 附注（避免误读）：成交量图里个别浅色柱饱和度低于测量阈值，会被少数 1–2 根，
> 这是**测量方法**的限制，不是缺柱；判据用的是「边界位置」而非「根数」。

## 5. 回滚

按 §3 的 4 处**反向**还原即可（不需要 `git checkout` —— 那会连带回滚前几轮工单的改动）：
1. 删除 `NARROW_DATA_BARS` 常量；
2. 删除 `ChartSyncMember.points` 与三处赋值、初始化里的 `points: 0`；
3. `baseOptions` 的注释可留（它是对官方行为的正确描述），`lockVisibleTimeRangeOnResize` 保持 `true`；
4. 删除挂载 effect 里的 `ResizeObserver` 整块（含清理里的 `ro.disconnect()`）。

回滚后 `tsc --noEmit` 应仍通过；缺陷会回到「变宽后右空 45%」。

## 6. 未做 / 不在本单范围

- **前端 15m/30m 周期标签错误**（属反重力，已在 `接入说明_反重力.md` §7 给出两种修法）—— 本轮未改。
- **数据侧只给 run 自己的窗口**：用户问「前后能看多少天」，答案是「该 run 消费区间」，
  ACHUSDT 这个 run 只有 17 小时 / 18 根（见 `out/21_K线居中/23_可查看K线跨度_汇总.json`）。
  要往前多看历史必须改数据口径（会引入回测没消费过的行情），**需用户决策**，本轮不动。
