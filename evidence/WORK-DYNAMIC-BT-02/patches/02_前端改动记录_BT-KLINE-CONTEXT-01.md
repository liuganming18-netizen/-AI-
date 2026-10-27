# 前端改动记录｜BT-KLINE-CONTEXT-01（±7 天上下文行情）

> 触发：用户在看到「K 线居中已修」的图之后指出「**只有 7 根 K 线**」。
> 核查成立 —— `FT-ENTRY-09` 那批 run 的窗口只有 0.25~27 小时，`1000XECUSDT` 恰为 **7 根 / 6.75h**；
> 而策略前置条件是 **7 天箱体 + 24h 冷却** ⇒ 只画 run 区间**根本看不到生成信号的那个箱体**。
> 用户在选项里选定「**扩 ±7 天（推荐）**」。
>
> 总纪律：**只加不改**。run 区间内的 K 线一根不动，新增两段单独计数并单独说明。

## 0. 改动文件与身份（2026-10-24T19:40Z 核对，结论绑定该时刻）

| 文件 | 改动前 SHA-256 | 改动后 SHA-256 |
|---|---|---|
| `UI界面/web/src/api/labBacktest.ts` | （见 git 历史，本轮前未记录） | `b03e23497068f8a738da4503f1fb2ef112e6491407e03e1f4b4e81af124eecc0` |
| `UI界面/web/src/components/BacktestWorkbench.tsx` | 同上 | `ed6aa381ee519f21935f2fe375ef77a1550b4c15e3c8996b35fe7aa91326ae3c` |
| `UI界面/web/src/components/LabChartPanel.tsx` | 同上 | `c2fa1b9311a4c9ef4224241b1340c0189f3cf97d93d5d12531cad464c4ea74ff` |
| `UI界面/web/src/components/LightweightChart.tsx` | `1a461ddf…638c65`（CENTER-01 交付时） | `ecf16193e8462027f725060579200c8b2d19fa0bd907f37e56e3c3974e183337` |
| `框架/backend/backtest_api.py` | `b61ecabe2d837454de67409c203c620e44197655f303a8cc2402f91181d81200` | `293dd164d594cfc9940bfb1bcdf42979c2452de3c16f8425e1f0cd0bde431692` |

后端 diff（只含本轮上下文改动，相对上一轮交付态）：
`patches/backtest_api.py.context.diff`（146 行）；本轮交付态副本 `patches/backtest_api.py.patched_v2`。

## 1. 改动 1｜`labBacktest.ts`：类型 + 请求参数

`LabChart` 增 6 个**可选**字段（缺省即「没有上下文」，与改造前等价）：

```ts
candles_context_days?: number;
candles_context_before?: number;
candles_context_after?: number;
candles_context_tf?: string | null;
candles_run_range?: { start_ms: number; end_ms: number } | null;
candles_context_note?: string | null;
```

`labGetChart` 的 `opts` 增 `contextDays`，只在 `> 0` 且有限时才拼参数（上限 90 与后端一致）：

```ts
if (typeof opts.contextDays === 'number' && Number.isFinite(opts.contextDays) && opts.contextDays > 0) {
  p.set('context_days', String(Math.min(90, Math.round(opts.contextDays))));
}
```

**为什么可选**：`LabChartPanel` 等既有调用方不传 `contextDays` ⇒ 不发该参数 ⇒ 后端按 `0` 处理 ⇒
行为与改造前**逐字一致**（已验证：不传时 `candles` 仍为 7 根）。

## 2. 改动 2｜`BacktestWorkbench.tsx`：`CONTEXT_DAYS = 7`

```ts
const CONTEXT_DAYS = 7;
```

fetch key 加上 `:ctx:${CONTEXT_DAYS}`（否则同 run 第二次进入不会重取），请求带 `contextDays: CONTEXT_DAYS`。

**为什么是 7**：策略前置条件 = 7 天箱体 + 24h 冷却 ⇒ 7 天刚好够看一个完整箱体 + 冷却。
常量处已写明，避免后人随手改数字。

## 3. 改动 3｜`LabChartPanel.tsx`：四小项

### 3.1 `viewKey` 加上下文签名

```ts
const viewKey = useMemo(
  () => (chart
    ? `lab:${chart.symbol}:${chart.exec_timeframe}:${tf}:${chart.window?.start_ms ?? 0}`
      + `:ctx${chart.candles_context_days ?? 0}-${chart.candles_context_before ?? 0}`
    : 'lab:empty'),
  [chart, tf],
);
```

**为什么**：载荷从「只有 run 区间」变成「含 ±7 天上下文」时必须当成**新视图**重新首屏落位，
否则会沿用旧的窄视窗 —— 实测就是「1411 根已到位、可见区仍是 05-31 那 17 小时」。

### 3.2 卡片头琥珀徽标

`上下文 ±7 天 · 前672/后671 根(15m) · run 区间 2024-05-31 09:15~16:00`，
`title` 里明写「**不是本 run 消费过的数据**」。**上下文与本 run 消费区间必须在界面上分开讲**，
否则看的人会把上下文当成回测吃过的数据。

### 3.3 `run` 边界标记（`buildRunRangeMarkers`）

只在上下文真的存在、且该侧有根数时才画。**只有起点带文字**：

> 实测教训：默认 1h 视图下 run 窗口 6.75 小时、上下文 14 天，两枚标记在屏幕上只差约 **13px**，
> `run 起` / `run 止` 两段文字必然叠成一团。所以起点标 `run`、终点只留方块；
> 精确起止时间由徽标给出。

### 3.4 **首屏让位**（本轮最关键的一处）

```ts
const firstPaintRef = useRef<{ key: string; handle: ChartHandle } | null>(null);
const paintKey = `${runId ?? ''}|${chart?.symbol ?? ''}|${tf}`;
const isFirstPaintOf = (key: string, h: ChartHandle): boolean => {
  const cur = firstPaintRef.current;
  if (cur && cur.key === key && cur.handle === h) return false;
  firstPaintRef.current = { key, handle: h };
  return true;
};
```

两个定位 effect（选中交易 / 点击订单）开头都加：

```ts
if (isFirstPaintOf(paintKey, handle)) { setLocateInfo(null); return; }
```

**规矩**：每个新视图（run / 标的 / 显示周期）**首屏一律按完整数据域落位**，自动选中不算用户意图；
只有用户**主动**点某笔成交/订单时才定位收窄。

**两个真 bug 都出在这里**（自查发现）：

| 现象 | 根因 | 修法 |
|---|---|---|
| 上下文 343 根到位，屏幕只框住 **30 根 / 30 小时** | 打开页面时 `selectedTradeId` 被**自动**设成本 run 第一笔，定位 effect 立刻用 `entry ± 12 根` 收窄 | 首屏让位（本节） |
| 切周期又缩回去：15m 落到 **51 根**、4h 落到 **26 根** | 切周期会重建图表实例，但定位 effect 会在「**旧句柄还在、新实例未挂载**」的那一帧先跑一次并消耗门闩，等新句柄 `onReady` 后再跑就变成「不是首屏」 | 门闩键里并入**句柄身份** |

## 4. 改动 4｜`LightweightChart.tsx`：`ResizeObserver` 的「已放大」分支

CENTER-01 交付时该分支是 `scrollToPosition(desiredOffset, false)`。本轮自查发现它是**真回归**：
用户放大看某根之后只要动一下窗口，视窗就被甩到最右边（等于把用户的操作抹掉）。

改为：**不动缩放、不动平移**，只在 `rightOffset` 被官方 `fixLeftEdge` 顶歪时纠正，
且纠正前后用 `getVisibleRange()` 记住/落回原范围：

```ts
if (!allFit) {
  if (curOffset !== desiredOffset) {
    const keep = safeCall(() => ts.getVisibleRange());
    safeTimeScaleCall(() => ts.applyOptions({ rightOffset: desiredOffset }));
    if (keep && Number(keep.to) > Number(keep.from)) {
      applyTimeRangeTo(member, Number(keep.from), Number(keep.to));
    }
  }
  return;
}
```

`lockVisibleTimeRangeOnResize: true` 本身已经把**时间窗**锁住了，这里不需要也不应该再动位置。

## 5. 验证（脚本产出，不手抄）

采集：`code/web_e2e/11_context_verify.js`（playwright，1668×1000）
汇总：`code/web_e2e/12_context_verdict.py` ⇒ `out/21_K线居中/context/23_上下文行情结论.json`

| 判据 | 结果 |
|---|---|
| A 首屏铺满（`visible_bars == payload_bars`，载荷根数由后端 15m **独立按 UTC 边界重算**） | 1h **343/343**、15m **1370/1370**、4h **86/86**；左留白 0%~0.12%、右留白 0%~0.25% |
| B 不吃缩放（放大 40 根 → 1668→1200→1668） | 可见时间窗三次**完全一致**；`barSpacing` 19.854→9.659→19.854，比值 0.4865 == 宽度比值 0.4865，相对偏差 **0.0%** |
| C 定位入口仍在 | 主动 `setViewport` 后可见范围 `1717106400..1717214400`，`barSpacing` 19.85→26.26 ⇒ 真的生效 |
| D 后端只加不改 | `context_days=0` 27 根 / `=7` 1370 根，**run 区间内 27 根逐根完全相同**；声明前/后 672/671 == 实际计数 |
| E 预算 | `context_days=90` 共 17,306 根 < 20,000 ⇒ **裁剪分支不可达、未验证**（已在结论里写明，不计入通过） |
| F 控制台 / 类型 | `console_errors: []`；`tsc --noEmit` 通过 |
| 第二个 run 复验 | ACHUSDT：354/354、1411/1411、89/89，留白 ≤0.24% |

产物：`context/verify_steps_<run_id>.json`、`context/verify_*.png`、`context/panel_<run_id>.png`。

> **证据绑定**：上表读数采集于 **2026-10-24T11:42Z**，对应上表「改动后 SHA-256」这一版代码
> （改过徽标 `title` 文案之后重采过一次，确保读数与交付态同版）。

## 6. 回滚

四处前端改动都可用「删掉新增分支」的方式退回；**最省事的整体回滚**是把 `CONTEXT_DAYS` 改成 `0`
—— 此时不拼 `context_days`，后端按 `0` 处理，载荷回到「只有 run 区间」，其余新增 UI 全部因
`candles_context_days === 0` 自动不渲染。

后端回滚：

```bash
cp evidence/WORK-DYNAMIC-BT-02/patches/backtest_api.py.patched 框架/backend/backtest_api.py
kill -TERM $(lsof -nP -i:8787 -sTCP:LISTEN -t) && sleep 0.6
python 框架/backend/service_manager.py start --enable-paper --arm 3a --symbol all
```

## 7. 未做 / 仍待处理

- **前端 15m/30m 周期标签错**（§5.3）：与本次无关，仍属反重力，本单不改。
- **OI 副图在上下文段为空**：5m OI 归档只覆盖 run 区间，上下文段确实没有数据 ⇒ 按纪律**不补值、不外推**。
- **预算裁剪分支未验证**：见上表 E。
