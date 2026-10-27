# 26 · BT-KLINE-CENTER-01 收口回执（K 线「没居中 / 右侧大片空白」）

- **工单**：BT-KLINE-CENTER-01 —— 用户截图 `截屏2026-10-24 18.24.47.png`（ACHUSDT 1h）+ 文字「K线1小时线居中」
- **时间**：2026-10-24（UTC 10:50 前后完成）
- **结论**：**缺陷已修，逐像素验证 PASS**（`out/21_K线居中/22_K线居中修复结论.json`，`overall: PASS`）。
  尺寸变化后右侧空白 **45% → 0.9%**，可见根数 **10 → 18/18**；放大后的缩放级别**不被吃掉**（相对变化 0.0%）。
- **顺带答清一个数据问题**：用户随后问「前后可以查看多少天的K线」——可查看范围 = **该 run 自己的消费区间**，
  不是渲染丢数据。ACHUSDT 那个 run 只有 **17 小时 / 18 根 1h**；全库 2,274 个 run 中位 **211 天**。

---

## 1. 一句话回答

「K 线为什么没居中？」——**不是数据少，是窗口尺寸变化后官方库把 `rightOffset` 顶大了。**

## 2. 判据（三态）

| 判据 | 结果 | 依据 |
|---|---|---|
| 缺陷是否真实存在（可复现） | **通过** | 视口 `1668→1200→1668` 后右空 370/820 = **45%**、只剩 10 根（`resize_test.json` + `resize_3_back1668.png`） |
| 首屏是否本来就有缺陷 | **不通过（即本来正常）** | 首屏右空 6/820 = 0.9%、18 根全见 ⇒ 排除「数据只有 18 根」与「恢复旧视窗」两种解释 |
| 修后是否铺满（6 个尺寸步骤） | **通过** | 每步 18/18 根、左空 0.2%~0.6%、右空 0.7%~0.9% |
| 是否吃掉用户缩放级别 | **通过（未吃掉）** | 2548 根场景 9.0px→9.0px、18 根场景 81.0px→81.0px，相对变化 **0.0%** |
| 三图联动是否被带偏 | **通过** | 成交量 / OI 柱边界与蜡烛边界逐条 **≤0.5px** 对齐 |
| 既有交互是否回归 | **通过** | 切标的、切 15m/30m/1h/4h 全生效；`console_errors: []`；`tsc --noEmit` 通过 |
| 前端 15m/30m 周期标签错 | **仍未修（不属本单）** | 属反重力公共前端，§5.3 已给两种修法 |

## 3. 根因（有官方源码，不是推断）

`baseOptions` 的 `lockVisibleTimeRangeOnResize: true` —— 官方 `_private__setWidth`：

```js
if (options.lockVisibleTimeRangeOnResize && oldWidth !== 0) {
    barSpacing = barSpacing * newWidth / oldWidth;   // 锁的是「时间窗」，不是 barSpacing
}
if (options.fixLeftEdge) {
    rightOffset -= Math.round((oldWidth - newWidth) / barSpacing) + 1;
}
```

第二段在**变宽**时符号翻转成「加」，实测把 `rightOffset` 从 **2 顶到 10** ⇒ 右侧凭空 8 根空档（8/18 ≈ 44%）。
原代码注释「保留 `barSpacing`」是**反的**，本轮一并改正。

**中途踩到的坑**：`getVisibleRange()` 会被官方库夹到数据首末（`Math.min/max`），
所以「可见范围是否覆盖数据域」这个判据会被骗（看起来变窄，实际是窗口右移 8 根）。最终改用
`points + desiredOffset <= plotW / barSpacing`。

## 4. 改了什么（4 处，`UI界面/web/src/components/LightweightChart.tsx`）

1. 新增 `NARROW_DATA_BARS = 50`：`rightOffset` 单位是**根**，2 根空档在 18 根数据上就是 11% 空白 ⇒ ≤50 根时给 0。
2. `ChartSyncMember` 增 `points`（K 线 = `candleData.length`，OI/成交量 = `gridData.length`）。
3. `baseOptions` 注释纠正（行为仍是 `true`）。
4. 挂载 effect 增 `ResizeObserver`：follower → `alignToLeader`；leader 且 `followRef` 为真 →
   复位 `rightOffset` → 数据装得下就 `applyTimeRangeTo(domain)` 重新铺满 → 装不下（用户放大过）只贴右缘。
   **用户已拖到历史视窗时一律不碰**（沿用既有 `followRef` 语义）。

- 文件 SHA-256（改动后）：`1a461ddfe2350ff5a016a98a59d0a4b5b8a2af29622947407782d10fb3638c65`（2,384 行）
- 完整 before/after、验证表、回滚步骤：`evidence/WORK-DYNAMIC-BT-02/patches/01_前端改动记录_BT-KLINE-CENTER-01.md`

## 5. 「前后能看多少天 K 线」的机械答案

可查看范围 = `inputs.json.data_summary.time_range_1h`（该 run 实际消费区间）。
后端先按「run 窗口 ∪ 全部标记点 ± 48 小时」取，再统一裁回该区间（`candles_clipped_to_run_data`）
⇒ **不往前多给历史、也不给回测之后的行情**。单次上限 `FT_REPLAY_CANDLE_CAP = 20000` 根；
带 `focus_ms` 聚焦某一笔时再收窄到「该时刻前后各 10000 × 15 分钟」。

| run | 区间（UTC） | 跨度 | 1h 根数（跨度 ÷ 1h + 1，理论值） |
|---|---|---|---|
| `…_ACHUSDT`（截图那个） | 2023-04-21 00:15 ~ 17:15 | **17 小时** | **18**（与像素实测一致） |
| `…_AUCTIONUSDT` | 2024-02-23 20:15 ~ 22:30 | 2.25 小时 | 3 |
| `…_BCHUSDT` | 2021-12-13 22:15 ~ 12-23 22:15 | 10 天 | 241 |
| `run_FT_LOW4H_DYN01_…`（AVAX） | 2021-12-01 ~ 2025-01-01 | 1,127 天 | 27,049 |

> 「理论值」的含义：跨度 ÷ 周期 + 1。实际返回根数还会受 20,000 根上限与 `focus_ms` 收窄影响
> （实测该 AVAX run 全 run 视图页面显示 2,548 根、点某一笔后 5,000 根）。

全库 2,274 个 run：最短 0.01 天 / P25 97 天 / **中位 211 天** / P75 365 天 / 最长 2,116 天。
台账：`out/21_K线居中/23_可查看K线跨度_全库.csv` + `_汇总.json`（`code/09_kline_span_audit.py` 可复算）。

**要往前多看历史 = 改数据口径**（会画进回测没消费过的行情），需用户决策，本轮未动。

## 6. 产物

```
evidence/WORK-DYNAMIC-BT-02/
├── code/web_e2e/06_resize_center.js        尺寸变化复现 + 6 步截图
├── code/web_e2e/07_measure_candles.py      逐像素测量（根数/间距/左右留白）
├── code/web_e2e/08_resize_guards.js        反向保护（不得吃掉缩放级别）
├── code/web_e2e/10_center_verdict.py       机械汇总结论（不手抄数字）
├── code/09_kline_span_audit.py             可查看跨度台账
├── out/21_K线居中/                         截图 + 测量 JSON + 22_修复结论 + 23_跨度台账
└── patches/01_前端改动记录_BT-KLINE-CENTER-01.md
```

## 7. 边界

- 本单只修「铺满/居中」。**前端 15m/30m 周期标签错仍在**（属反重力），整体仍标「部分完成」。
- 未改任何回测结果、未改冻结规则、未改数据口径。
- 进程身份：前端 dev server 走 HMR 即时生效；后端 `pid=59747` 未重启（本单未改后端）。

---

## 后续（2026-10-24 晚）

用户在本单修完后指出「**只有 7 根 K 线**」⇒ 新开 `BT-KLINE-CONTEXT-01`（±7 天上下文），见
`27_BT-KLINE-CONTEXT-01_收口回执.md`。

对本单的影响：`22_K线居中修复结论.json` 的「18 根」是 **`context_days=0`（上下文上线前）** 的实测。
当前默认载荷已带 ±7 天上下文 ⇒ 同一 run 可见根数变成 343 根；**判据不变**（可见根数 == 载荷根数、留白 ≤5%），
只是分母变了。两份结论各自绑定各自的载荷，**互不矛盾**。
本单的 `ResizeObserver`「已放大」分支在 `CONTEXT-01` 里被修正为「不动缩放、不动平移」（原实现会把人甩到最右缘）。
