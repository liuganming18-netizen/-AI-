# BACKTEST-CHART-JITTER-FIX-01 · 回测 K 线拖动抖动修复

- 基线：`wyckoff-v3.1-main`，动手前 HEAD = `71962355b9b6f3e003ecda1ab9bc9d8e2648ee3d`
- 复现页面：`http://127.0.0.1:3000/?view=backtest&run=run_FT_LOW4H_DYN01_Wyckoff_C1_S05_DYN_Vs08_Z06`
  （1h 原始 15698 根、3 笔成交、278 条委托、20000 条 OI 样本；视口 1680×900）
- 验证方式：真实浏览器（Playwright + Chromium 156）驱动真实页面，逐帧采样布局与时间轴写入。
  未使用截图肉眼判断，全部给出计数。

---

## 一、Bug 实际根因（三条，各自独立、各自实测）

### 根因 1：读数条高度随悬停内容变化 ⇒ 图表整块上下跳（页面跳动）

`.ds-hoverbar` 是 `flex-wrap: wrap` 且**没有高度下限**。它的内容随鼠标所指那根 K 线而变：

- 空态一句提示 → 1 行；
- 正常态「时刻/开/高/低/收/量/持仓量/信号」8 段 → 1–2 行；
- 带委托或带信号的柱还会多挂 1–2 个徽标 → 2 行。

它紧贴在 K 线图上方，高度一变就把整块图表往下顶。

实测（`probe5`，鼠标从左到右扫过整幅图 264 帧，不做任何拖动/缩放）：

| 指标 | 改动前 |
|---|---|
| 读数条高度取值 | **28 / 50 / 54 / 56**（4 个值，切换 7 次） |
| 图表容器 `top` 取值 | **450.17 / 472.17 / 476.17 / 478.17**（纵向行程 **28px**） |
| 滚动容器 `scrollHeight` | 2101 → 2151 之间反复变化 |

即：**只移动鼠标，不拖不缩，图表就会上下跳 28px，页面滚动条跟着动。**

### 根因 2：拖动/缩放期间视窗被反复回写，且回写取整到整根 ⇒ K 线横向回跳

链路（闭环）：

```
官方库派发可见范围事件
  → LightweightChart.scheduleReport（250ms 去抖）
  → LabChartPanel.onViewportChange
  → 父组件 BacktestWorkbench: setManualViewport({fromMs,toMs})   ← 每次都新建对象
  → LabChartPanel 两个 effect 各写一次时间轴：
      ① 恢复 effect：handle.setViewport() → timeToIndex() ⇒ 整数逻辑索引 ⇒ setVisibleLogicalRange
      ② 数据 effect（initialViewport 引用变了）：setVisibleRange(previousRange)
  → 官方库再派发范围事件 …… 回到第一行
```

两个致命点：

1. **写的是 250ms 之前的旧窗口**（手指早就往前走了），且 `setViewport` 经 `timeToIndex()`
   **取整到整根**，于是每次回写都把视窗往回拽半根到几根。
2. 这个环**不需要用户操作也能自转**：父组件每次 `setManualViewport({...})` 都新建对象
   （即使数值一模一样）⇒ `computedInitialViewport` 重算成新对象 ⇒ 图表数据 effect 重跑
   ⇒ 再把当前范围写回一次 ⇒ 又派发事件。

实测：

| 场景 | 应用层时间轴写入 | 视窗方向反转 | 单帧最大跳变 |
|---|---|---|---|
| 慢速拖动 3.6s（120 步） | **54** | 0 | — |
| 快速甩动一次（45 步） | **18** | **8**（每次回跳 5–6 根 ≈45–54px） | 5.99 根 |
| 30 秒连续拖动 | **160**（80 组） | **34** | 4.86 根 |
| 纯十字光标扫 90 步（不拖不缩） | **24** | 0 | — |
| **页面完全空闲 10 秒（鼠标不动）** | **52** | — | — |

30 秒拖动那一轮还量到一个更明显的外在表现：**只拖动不滚轮，缩放级别却一路变小**
（`barSpacing` 8.946 → 4.894，越拖越缩），因为每次回写都在整根边界上取整、误差同向累积。

### 根因 3（根因 2 的一部分，单独列出便于复核）：`viewport` 死状态

`LabChartPanel` 里 `const [viewport, setViewport] = useState(...)` 每次上报都写一次，
但**全文件没有任何读取点**（已核对：只有声明和两处无关徽标）⇒ 每次上报白重渲染一遍整个面板。
本次随根因 2 一并移除。

### 明确排除的方向（查过、有证据、不是原因）

| 怀疑项 | 实测 | 结论 |
|---|---|---|
| 图表容器宽高在拖动中变化 | `widthChanges 0`、`heightChanges 0`、`axisWChanges 0`、`plotWChanges 0` | 不是 |
| `ResizeObserver` 反复触发 `resize` | 拖动/缩放/十字光标期间 `RO fires 0` | 不是 |
| 拖动时页面滚动条出现/消失导致宽度变化 | `document.documentElement.clientWidth` 全程不变（macOS 覆盖式滚动条） | 本机不是 |
| 图表实例被反复重建 | 拖动/缩放/光标期间容器 `top` 与实例句柄不变；只在切标的/切周期时重建（预期） | 不是 |
| Tooltip 改变父容器尺寸 | 官方库十字线/价格标签画在 canvas 内，不参与布局 | 不是 |
| 拖动同时触发页面滚动 | 拖动期间 `scrollHeight` 不变、无滚动事件 | 不是 |

---

## 二、修改文件与关键代码

只改两个文件（`git diff --stat`：+139 / −12），不碰策略、回测计算、冻结参数、PIT、历史数据。

### 1) `UI界面/web/src/design/backtest.css` — 读数条钉住高度下限

```css
.ds-hoverbar {
  display: flex;
  align-items: center;
  align-content: center;   /* 新增：多行时整体居中，1 行/2 行观感一致 */
  flex-wrap: wrap;
  gap: 4px 14px;
  min-height: 56px;        /* 新增：实测最大值（2 行），布局不再随悬停内容变化 */
  ...
}
```

用 `min-height` 而不是 `height`：真需要 3 行的极端内容仍可撑高，**不裁剪、不隐藏任何读数**。

### 2) `UI界面/web/src/components/LabChartPanel.tsx` — 断掉「上报即回写」闭环

```ts
const VIEWPORT_SETTLE_MS = 400;          // 停手判定
const VIEWPORT_SAME_TOLERANCE_MS = 500;  // 「同一个视窗」的容差（吸收整秒取整）

// ① 视窗回写改为「停手且确认没再动」才写一次
const onViewportChange = useCallback((v) => {
  if (performance.now() < programmaticQuietUntilRef.current) return;
  crosshairBus?.emitViewport('lab-chart', v.fromMs, v.toMs + barSpanMs);  // 广播仍即时
  if (!onManualViewportChange) return;
  if (Date.now() < programmaticFocusLockRef.current) return;
  pendingManualViewportRef.current = { fromMs: v.fromMs, toMs: v.toMs };
  clearTimeout(manualViewportTimerRef.current);
  manualViewportTimerRef.current = setTimeout(() => {
    const pending = pendingManualViewportRef.current;
    if (!pending) return;
    // ② 此刻范围与待写值不一致 ⇒ 手指还在动 ⇒ 本轮直接丢弃（会再上报，重新起算）
    const cur = candleHandleRef.current?.visibleRange();
    if (cur && (|cur.fromMs - pending.fromMs| >= TOL || |cur.toMs - pending.toMs| >= TOL)) return;
    // ③ 父组件已经持有同一个视窗 ⇒ 不必再说一次（断掉自转的环）
    const known = manualViewportRef.current;
    if (known && |known.fromMs - pending.fromMs| < TOL && |known.toMs - pending.toMs| < TOL) return;
    onManualViewportChange(pending.fromMs, pending.toMs);
  }, VIEWPORT_SETTLE_MS);
}, [...]);

// ④ 恢复 effect 加守卫：已经在目标视窗就一次都不写；真要落位先进入静默窗
useEffect(() => {
  ...
  const cur = handle.visibleRange();
  if (cur && |cur.fromMs - manualViewport.fromMs| < TOL && |cur.toMs - manualViewport.toMs| < TOL) return;
  programmaticQuietUntilRef.current = performance.now() + 500;
  handle.setViewport(manualViewport.fromMs, manualViewport.toMs);
}, [manualViewport, handle, tf, crosshairBus]);

// ⑤ 删除从未被读取的 `viewport` 死状态（每次上报白重渲染一次面板）
```

**没有关闭任何功能**：拖动、滚轮缩放、十字光标、图内点击选中、上一笔/下一笔、
深链手动视窗恢复（`?vf=&vt=`）、切标的、切周期、展开大图全部保留并逐项验证。

---

## 三、修复前后对比（同一探针、同一 run、同一视口）

| # | 场景 | 指标 | 改动前 | 改动后 |
|---|---|---|---|---|
| ① | 左右拖动（40+40 步） | 时间轴写入 | **24** | **1** |
| | | 视窗方向反转 | **10** | **0** |
| | | 图表纵向跳动 | 1 | **0** |
| ② | 滚轮缩放（5 上 3 下） | 时间轴写入 | **14** | **1** |
| | | 缩放是否有效 | 有效 | 有效（barSpacing 9.043→10.532） |
| ③ | 十字光标连续移动（90 步） | 时间轴写入 | **24** | **0** |
| | | 图表纵向跳动 | **5** | **0** |
| | | 读数条高度取值 | 56/50/54/28 | **56（唯一值）** |
| ④ | 切换标的（AVAX→BCH） | 时间轴写入 | **16** | **0** |
| | | 是否切换成功 | 成功 2907 根 | 成功 2907 根 |
| ⑤ | 切换周期（1h→4h） | 时间轴写入 | **14** | **0** |
| | | 是否切换成功 | 2907→726 根 | 2907→726 根 |
| ⑥ | 调整窗口（1200→1680） | 时间轴写入 | **12** | **0** |
| | | 图表宽度跟随 | 478→908 | 478→908 |
| ⑦ | **连续拖动 30 秒**（572 步） | 时间轴写入 | **160** | **1** |
| | | 视窗方向反转 | **34** | **0** |
| | | 图表纵向跳动 | **38** | **0** |
| | | 缩放漂移 | 8.946→4.894（越拖越缩） | 10.557→10.400（基本不动） |
| ⑧ | **空闲 10 秒（鼠标不动）** | 时间轴写入 | **52** | **0** |

「方向反转」= 逐帧可见逻辑范围的 `from` 序列出现 `+ → −` 或 `− → +`，即视窗被拽回去一次。
「图表纵向跳动」= 逐帧 `getBoundingClientRect().top` 取值发生变化的次数。

---

## 四、验证结果

| 验证项 | 结果 | 证据文件 |
|---|---|---|
| 1. 左右拖动 K 线 | 通过：平移正常（净位移 −2h），0 次回跳，图表不位移 | `probe/out/final_probe6_verify.json` |
| 2. 鼠标滚轮缩放 | 通过：barSpacing 9.043→10.532，缩放平滑无回弹 | 同上 |
| 3. 十字光标连续移动 | 通过：0 次时间轴写入、0 次纵向位移、读数条恒定 56px | 同上 / `final_probe5_hoverheight.json` |
| 4. 切换不同回测标的 | 通过：AVAX→BCH，2907 根，0 次多余写入 | 同上 |
| 5. 切换时间周期 | 通过：1h→4h，2907→726 根，0 次多余写入 | 同上 |
| 6. 调整浏览器窗口大小 | 通过：宽度 478→908 跟随，`ResizeObserver` 只按实际尺寸变化触发 2 次 | 同上 |
| 7. 连续拖动 30 秒 | 通过：1 次写入、0 次回跳、0 次纵向跳动 | 同上 |
| 8. 深链手动视窗恢复（回归） | 通过：`?vf=1639353600000&vt=1639612800000` 落位误差 **0 小时**，「手动已恢复」徽标正常 | `final_probe8_regression.json` |
| 9. 读数条内容不被裁剪（回归） | 通过：填充态/空态均 `scrollHeight == clientHeight == 56`，`clipped: false` | 同上 + `final_hover_filled.png` |
| 10. 前端 TypeScript 检查 | 通过：`npx tsc --noEmit` 退出码 **0** | — |
| 11. 前端生产构建 | 通过：`npm run build` → `✓ built in 1.02s`（941.75 kB / gzip 274.34 kB；仅体积提示，非错误） | — |

### 已知残余（如实记录，未隐藏）

1. 每次「停手」仍会有 **1 次**时间轴写入（把当前窗口落一次位），这是设计行为，
   且写的是**当前窗口本身**（实测 `before == args`），不产生可见变化。
2. `min-height: 56px` 是按实测最大值（2 行）定的。若某根 K 线同时挂着跨币并发委托等多枚
   长徽标、内容真需要 3 行，本条仍会撑高一次（不裁剪内容，宁可再动一次也不藏读数）。
3. 未覆盖：Windows/Linux 经典滚动条环境（本机 macOS 为覆盖式滚动条，
   `clientWidth` 全程不变，因此不存在「滚动条出现→宽度变化→图表 resize」这条链路；
   本次修复已消除布局高度变化这个上游触发点，该链路在当前代码上无从触发）。

---

## 五、探针清单（可复跑）

```
probe/00_reach_chart.mjs          到达图表页并截图
probe/01_jitter_probe.mjs         三阶段（光标/拖动/滚轮）布局与写入计数
probe/02_drag_range_probe.mjs     慢速拖动逐帧视窗采样 + 写入入参
probe/03_axis_hover_probe.mjs     纵向位移 / 价格轴宽度 / 绘图区宽度逐帧采样
probe/04_flick_probe.mjs          快速甩动：方向反转与单帧跳变
probe/05_hover_height_probe.mjs   读数条高度分布（扫过整幅图）
probe/06_verify_seven.mjs         交付验证 7 项（本次主证据）
probe/07_hover_calls_probe.mjs    纯悬停期间写入的入参（证明是幂等写）
probe/08_regression_probe.mjs     深链恢复 + 读数条不裁剪 + 截图
probe/09_idle_probe.mjs           空闲 10 秒写入计数（证明自转环已断）
probe/10_idle_stack_probe.mjs     空闲写入的调用栈（定位到 LightweightChart 数据 effect）
```

跑法（在 `UI界面/web` 下，脚本用绝对路径引 `node_modules/playwright`）：

```bash
LABEL=final node ../../evidence/BACKTEST-CHART-JITTER-FIX-01/probe/06_verify_seven.mjs
```

`LABEL` 只是输出文件名前缀，`out/` 下同时留有 `baseline_*`（改动前）与 `final_*`（改动后）。

---

## 六、保护范围自查

- 未修改策略、回测计算、冻结参数、PIT 门槛、历史数据：`git status` 里本次只有 2 个前端文件 + 本目录。
- 未执行 `reset --hard`、未强推、未 `git add .`（提交按显式路径逐个 add）。
- 未提交工作区里既有的未跟踪/已修改成果（`框架/backend/*`、行情快照等一律未动）。
- 复跑基线时用 `git checkout --` 临时还原的正是本次这 2 个文件（改前它们与 HEAD 一致，
  已用 `md5` 备份到 `/tmp/jitter_fix_backup` 并在测完后原样恢复、复核 diff 与 `tsc`）。
