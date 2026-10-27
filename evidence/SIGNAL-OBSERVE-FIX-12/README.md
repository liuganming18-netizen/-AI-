# SIGNAL-OBSERVE-FIX-12｜观察期无响应：核查、修复与证据

> 工单要求：**观察期与模拟实盘并行。没有满足入场条件，不应导致观察区没有反应。**
> 本报告逐项列出：根因、修改位置、修复前后行为、完成／部分完成／阻塞、剩余未验证项。
> 所有数字都来自本轮实测脚本，脚本与原始输出都在 `probes/` 与 `out/`。

---

## 0. 一句话结论

观察区**从来不是没数据**：状态机一直在产出「处于观察状态的事件」（修复前实测全池 **346 个**），
但接口只给了 `{币种: 阶段名}`，前端**连这个字段都没解析**，页面上只剩一句
「箱体观察 304 · 已扫低 21 · 待突破 21」，还折叠在诊断块里 ⇒ 用户看到的是「观察区没有反应」。

本轮把这条链补通：状态机（已有）→ 接口逐条明细 → 前端常显观察区（币种/阶段/最近更新/已满足/未满足），
并且**观察与执行仍然分开判定**（观察展示不产生任何订单，实测账本行数不变）。

**完成**：观察区展示与更新、四态区分、观察与执行分离、隔离历史回放。
**未验证**：自然行情下「观察 → 突破 → 真实模拟成交」这一段（当前无自然候选，见 §7）。

---

## 1. 先核查：观察期的权威定义（不是本轮发明的）

**定义来源**：`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` §3 原文
> 「每个 symbol+venue 最多一个处于观察状态的事件。」

据此：

| 是否算「观察中」 | 状态 |
|---|---|
| ✅ 算 | `BOX`（建箱体）、`SWEEP`（已扫低）、`RETEST`（待突破） |
| ❌ 不算 | `CANDIDATE` 及之后（`VOLUME_REJECTED` / `OI_REJECTED` / `OI_UNAVAILABLE` / `INVALIDATED` / `EXPIRED` / `MISSED_WITHOUT_RETEST` / `CLOSED`）——事件已结束 |
| ❌ 不算 | `IDLE`（没有事件；它为什么没进观察由 `idle_breakdown()` 回答） |

- **进入**：事件建档（`BOX` 成立）即进入观察。
- **退出**：升 `CANDIDATE`，或按既有规则失效/过期（`INVALIDATED` / `EXPIRED` / `MISSED_WITHOUT_RETEST` / 各 `REJECTED`）。
- 判定**没有放宽任何门槛**：所有条件表达式逐条照抄 `EventTrendConfig` 与
  `EventStateMachine.process_1h_bar`，只是把「下一关还差什么」如实读出来。
- 冻结规则 §3.E 本来就要求「记录每个痕迹、状态迁移、拒绝、过期、候选和结果」——
  本轮做的是**把已有状态导出来**，不是新造观察规则。

> 关于「不能擅自把所有箱体状态都当观察信号」：本实现只认 `active_event.current_state ∈ {BOX, SWEEP, RETEST}`，
> `IDLE` 一个都不算。实测 526 个标的里 392 个观察中、134 个 `IDLE`，两者严格分开（见 §4 断言②）。

---

## 2. 故障发生在哪一段（修复前证据）

探针：`probes/10_before_fix.py`（只读），原始输出 `out/10_before_fix.json`。

```
①状态机产出观察中事件: 346 个（BOX 304 / SWEEP 21 / RETEST 21）        ⇒ 正常
②接口暴露逐币明细:     未暴露（只有 {symbol: 阶段名}）                 ⇒ 故障
③前端类型层接收:       未接收（ExecutionDaemonView 无 perSymbolState，
                        解析函数也没读 per_symbol_state）              ⇒ 故障
④前端渲染:             未渲染（只用 stateCounter 拼一句计数串）        ⇒ 故障
结论: 故障在 ②③④ 三段（后端有数据、接口有阶段名、前端全丢），不是状态机没产出
```

页面实拍（`probes/20_shot_before.js` → `shots/signal_panel_before.png`）：

```
信号区出现: true（754 ms）   诊断已渲染: true
空态面板原文: 暂无交易信号 / 查看诊断与历史候选明细 ▼
「观察」出现 0 次；逐币行 0 行；控制台错误 0 条
```

即：**整页没有任何观察区**，右上却写着「模拟盘实仓 0 仓」——正是工单描述的现象。

---

## 3. 修改位置

### 3.1 后端（`框架/backend/`）

| 位置 | 改动 | 为什么 |
|---|---|---|
| `unified_runtime.py::MultiSymbolSignalEngine.observe_events()` | **新增**（约 240 行） | 把观察中事件逐条导出：币种、阶段、建档/最近更新时间、箱体 H/L/A/ER/宽、已满足、未满足（带当前实值）、到期时刻、剩余小时、是否过期。判据与状态机同源 |
| 同上 `observe_summary()` | **新增** | 只给计数与就绪度的汇总，供每 2 秒重写的状态文件使用 |
| `unified_runtime.py::observe_file_path()` | **新增** | 观察明细独立文件路径 `locks/paper_runtime_<arm>_observe.json` |
| `unified_runtime.py::UnifiedRuntime._save_observe_file()` | **新增** | 落盘节流：**内容摘要不变且未超 5 分钟就不写**；失败时写一条可被接口读到的 `generation_error` 记录（不静默） |
| `unified_runtime.py::UnifiedRuntime.__init__` | 加 `_observe_digest` / `_observe_written_at` | 节流状态挂在运行时上（写入由 `step_once` 触发） |
| `unified_runtime.py::step_once()` | 末尾调 `_save_observe_file()` | 与状态文件同一轮次，但独立文件 |
| `unified_runtime.py::get_full_status()` | `observe_events` → `observe_summary` | 明细不进 2 秒热文件（体积，见 3.3） |
| `unified_runtime.py::get_observe_events()` | **新增** | 读明细文件并判定**过期残留**：`age_seconds` / `stale` / `writer_pid_alive`，四态归一 |
| `unified_runtime.py::bootstrap()` | 末尾清观察缓存 | 预热会整体重建状态，旧缓存必须作废 |
| `api/routes.py::_signals_observe()` + `Route("/api/v1/signals/observe")` | **新增** | 加性只读端点；静态段优先匹配，不会被 `/signals/{signal_id}` 抢走 |

### 3.2 前端（`UI界面/web/src/`）

| 位置 | 改动 |
|---|---|
| `api/liveTrading.ts` | 新增 `ObserveItem` / `ObserveView` / `ObserveKind` / `classifyObserve()` / `toObserveView()` / `fetchObserveEvents()`；`ExecutionDaemonView` 补 `perSymbolState` 与 `observeSummary`，并在解析函数里真正读 `per_symbol_state` / `observe_summary`（此前声明都没有） |
| `components/ObservePanel.tsx` | **新增**：观察区面板。标题行恒显「观察中 N」+ 分阶段计数；逐行显示币种、阶段徽标、最近更新、最近处理 1h、剩余时间、未满足（带实值）、展开后已满足/待评估/箱体参数；带阶段筛选与币种搜索；数据不截断，只限制一次渲染 60 行 |
| `components/SignalListPanel.tsx` | 新增 `observe` / `observePending` 两个 prop，把 `<ObservePanel/>` 放在**信号列表上方常显**（不在 `<details>` 折叠里） |
| `App.tsx` | `useEndpoint('/signals/observe?arm=3a', 15000)` + `toObserveView` 归一后传入 |

### 3.3 为什么明细不并进状态文件（体积决策）

`step_once()` → `_save_status_file()` **每 2 秒把整份状态 JSON 全量重写一次**。

| 方案 | 体积 | 写盘量 |
|---|---|---|
| 明细并入状态文件 | 79 KB + 346 条 × ~0.9 KB ≈ **390 KB / 2 秒** | 约 **9~13 GB/天** |
| 明细独立文件 + 内容不变不写（采用） | 状态文件 **81.8 KB**（只多 ~0.5 KB 汇总） | 明细最坏 288 次/天 × 313 KB ≈ **64 MB/天** |

实测：`paper_runtime_3a_status.json` = **81,830 B**，`paper_runtime_3a_observe.json` = **312,756 B**（392 条）。
两条通道都调同一个 `observe_events()`，**口径不会分叉**。

---

## 4. 修复后行为（实测）

探针：`probes/40_after_fix_checks.py`（只读，18 项断言全过）。

```
[原始] quality.status=ok  items=392  observing_total=392  counts={'BOX':350,'SWEEP':17,'RETEST':25}
[原始] per_symbol_state=526 条  state_counter={'BOX':350,'IDLE':134,'RETEST':25,'SWEEP':17}
[原始] age=5.7s  stale=False  writer_pid_alive=True
```

| 断言 | 结果 |
|---|---|
| ① 每条记录含全部必需字段（含币种/阶段/最近更新/已满足/未满足） | PASS（392 条无缺字段） |
| ① 最近更新时间均有值 | PASS 392/392 |
| ① 每条至少给出「已满足/未满足/待评估」之一 | PASS 392/392 |
| ② 观察集合 == 状态机 `per_symbol_state` 观察集合 | PASS（392 == 392，多出/缺少均为空） |
| ② 分阶段计数一致 | PASS 状态机 `{BOX350,RETEST25,SWEEP17}` = 接口 |
| ② 不把非观察态混进来 | PASS（越界状态：无） |
| ③ 同一 symbol 不重复 | PASS（392 个唯一） |
| ④ 观察中标的没有持仓 | PASS（持仓 0 条） |
| ⑤ `quality.status` 在白名单内 / 有 universe / data_not_ready / scan_errors_total / 新鲜度可判定 | 全 PASS |
| ⑥ 状态文件未被明细拖大 / 明细独立存在 / 无截断 | PASS（81.8 KB / 312.7 KB / truncated=False） |

**观察随闭合 K 线推进**：23:46（修复前基线）346 个 `{BOX304,SWEEP21,RETEST21}` →
00:08（新 1h 收盘后）392 个 `{BOX350,SWEEP17,RETEST25}`。集合随收盘变化，不是常量。
基线留存：`out/00_baseline_observe_set.json`。

### 页面行为（`probes/50_verify_after.js`，20 项全过，0 控制台错误）

```
[PASS] 观察面板出现 :: 首屏 701 ms
[PASS] 显示观察总数 :: 观察中 392 · 箱体 350 · 已扫低 17 · 待突破 25
[PASS] 渲染出逐币行 :: DOM 行数=60
[PASS] 每行有币种 / 形态阶段（限定三态）/ 最近更新时间
[PASS] 显示未满足条件（带当前实值） :: 60/60 行
[PASS] 显示已满足条件（展开后） :: 第一条 3 项
[PASS] 首次加载无重复 / 两轮轮询后无重复 / 刷新后无重复 / 关闭重开后无重复
[PASS] 页面集合是接口集合的子集（无幽灵行/过期残留）
[PASS] 轮询后明细仍在推进（不冻结） :: 距今 93.1s，status=ok
[PASS] 持仓行数未因观察展示增加 :: 0 → 0
[PASS] 订单行数未因观察展示增加 :: 1 → 1
[PASS] 面板暴露 kind / reason / total 三个判定属性
```

截图：`shots/signal_panel_after.png`（整页）、`shots/observe_panel_only.png`（观察区默认态）、
`shots/observe_panel_expanded.png`（展开条件）。

### 空态四态区分（`probes/70_ui_states_mock.js`，5 项全过）

| 场景 | 页面文案 | `data-observe-reason` |
|---|---|---|
| 明细未写出 | **数据未就绪**（观察中 —） | `data_not_ready` |
| 全池扫描正常、确实没有观察标的 | **没有符合观察条件的标的** | `no_candidate` |
| 扫描有异常 | **观察区读取异常** | `fault` |
| 读到过期明细（写者已退出） | **观察区读取异常** + 过期原因 | `fault`（kind=`stale_record`） |
| 有观察标的 | 逐条列表 | `has_items` |

> 该测试**只拦截浏览器侧对只读接口的 GET**，不写生产库、不下单、不注入信号。
> 它证明的是**前端渲染契约**，不能替代真实数据证据（真实数据见 §4/§6）。

真实链路里 `not_connected` 也实测到了：`/signals/observe?arm=3b|3c`（无运行时）返回
`quality.status=not_connected` + 明确 `missing_reason`，页面判为「数据未就绪」。
`stale_record` 在两次重启的窗口里自然触发过（旧进程写的明细、写者已退出），
接口如实报 `stale_record` 而不是把旧数据当现状。

---

## 5. 修 bug 过程中发现的三个坑（都曾静默失败）

1. **方法挂错类**：`_save_observe_file()` 是 `UnifiedRuntime` 的方法，却写了 `self.observe_events()`——
   那是 `MultiSymbolSignalEngine` 的方法 ⇒ 每轮 `AttributeError`，被 `except: pass` 吞掉，
   明细文件永不生成，页面永远「没有观察标的」。
2. **空结果被缓存**：`UnifiedRuntime.__init__` 写第一份状态文件时就会调 `observe_summary()` →
   `observe_events()`，那时引擎还没装配（`len(engines)==0`），这个空结果被按 60 秒 TTL 缓存下来；
   预热完成后仍在 TTL 内 ⇒ **346 个观察标的被报成 0**。修法：空引擎结果不许缓存 + `bootstrap()` 末尾清缓存。
3. **字段类型假设错**：`scan_errors` 是**字典**（symbol→错误）不是列表，`scan_errors[-5:]` 抛
   `KeyError: slice(-5, None, None)`，同样被吞 ⇒ 落盘变成 `generation_error`。
   修法：字典/列表两种形态都兼容。

这三条都靠「失败时写一条可读的错误记录」才被抓出来——所以 `_save_observe_file` 现在
**不允许静默失败**：写不出来就落 `generation_error`，接口报 `error`，页面显示「观察区读取异常」。

---

## 6. 隔离历史回放：观察 → 满足入场 → 模拟执行 / 观察失效退出

设计要点（三个探针共用）：
- **同一生产逻辑**：直接构造 `UnifiedRuntime`，用 `step_once(origin="replay")` 走真实状态机、
  真实风控挂单、真实撮合，**不改任何阈值或门禁**；
- **完全隔离**：`PAPER_LOCK_DIR` / `PAPER_LEDGER_DIR` 指向临时目录，脚本开头断言状态文件与观察文件
  都落在临时目录，否则直接退出；生产账本/状态文件/信号库全程只读；
- **真实行情**：读生产热库的已闭合 K 线，不用人造数据；
- 每小时一步，逐步记录：观察集合、账本订单/持仓、事件迁移。

### 6.1 结论先说

| 要验证的 | 结果 |
|---|---|
| 观察事件在回放里真的会出现 | **通过**（5 个标的进入观察集合） |
| 观察期间不下单 | **通过**（隔离账本订单 0 / 持仓 0，全程无违规） |
| 观察事件会退出、且按既有规则退出 | **通过**（0GUSDT 迁移链 `BOX → SWEEP → INVALIDATED`） |
| 观察数量随收盘推进变化 | **通过**（取值 4 ↔ 5） |
| 「观察 → 满足入场条件 → 模拟执行」 | **未验证（数据窗口不足，见 6.3）** |

### 6.2 生命周期回放（`probes/62_replay_lifecycle.py`）

窗口 = 最近 5 天（步长 1 小时，121 步），标的 = 生产观察区里 6 个真实箱体观察标的
（`0GUSDT / 1000000BOBUSDT / 1000000MOGUSDT / 1000BONKUSDT / 1000CHEEMSUSDT / 1000FLOKIUSDT`）。

```
预热: {"universe": 6, "ready": 6, "skipped": 0}
… 步 24 观察 5 订单 0 持仓 0
… 步 120 观察 4 订单 0 持仓 0
进入过观察区: {'0GUSDT','1000000MOGUSDT','1000BONKUSDT','1000CHEEMSUSDT','1000FLOKIUSDT'}
观察期间出现订单的违规: 无
隔离账本终态: {"orders": {}, "positions": {}}

[PASS] ① 回放里确实出现了观察事件 :: 5 个
[PASS] ② 观察期间账本无该标的订单/持仓 :: 违规 0 条
[PASS] ③ 观察事件会退出观察集合 :: 1 个退出（0GUSDT）
[PASS] ⑤ 观察数量随收盘推进变化（不是常量） :: 取值集合=[4, 5]
[PASS] ⑥ 回放全程无 step 异常 :: 121 步
[FAIL] ④ 退出原因读不到终止态 :: 读的是 sm.active_event，事件结束时它已被置空
```

④ 的失败是**读取位置错**，不是「退出没有原因」。定点探针 `probes/63_exit_reason.py`
在同一窗口单独重跑 `0GUSDT`，直接读引擎的迁移计数：

```
最终 transition_counts: {"BOX": 1, "SWEEP": 1, "INVALIDATED": 1}
```

⇒ 该事件走过 `BOX → SWEEP`，最后按**既有失效规则** `INVALIDATED` 退出观察集合
（不是永久挂在列表里，也不是被前端隐藏）。这与冻结规则 §3「事件结束后同币种冷却 24 小时」一致。

### 6.3 候选链回放**未能复现**（如实记录，不当成通过）

`probes/60_replay_observe_flow.py` 目标是用生产 `bootstrap.discarded_detail` 里的**真实历史候选**
（`BBUSDT 2026-10-11T07:00Z`、`BNBUSDT 2026-10-05T03:00Z`、`CHZUSDT 2026-10-03T04:00Z`）
回放「观察 → 突破 → 模拟执行」。实测结果：

```
回放窗口: 2026-10-01T07:00Z → 2026-10-13T07:00Z（288 步）
… 步 264 观察 0 订单 0 持仓 0
断言：8 项中 2 通过 6 失败 —— 观察集合全程为 0
```

**原因（已定位，是数据窗口不足，不是链路故障）**：

- 热库 `数据/市场数据/db/market_v2.sqlite3` 的 4h 数据**统一只从 2026-09-09T20:00Z 起，共 275 根**
  （528 个标的全部如此，实测）；
- `MarketDataWatcher.get_closed_bars` **只读热库**；更早历史在冷层 parquet，运行时不读；
- 于是在候选时刻附近，引擎拿到的 4h 历史远少于 `atr_warmup_bars=200` 所需的量，
  箱体不成立 ⇒ 观察集合为空。

**这不是「观察区没反应」的复发**：同一个回放换成数据充足的窗口（最近 5 天）就正常产出观察事件（6.2）。
所以这一条判 **未验证**，并把它需要的前置条件写进 §7 的缺口。



---

## 7. 完成 / 部分完成 / 阻塞 / 未验证

| 工单条目 | 状态 | 依据 |
|---|---|---|
| 1. 复用既有观察规则/状态机/接口/页面，核实定义与进出条件 | **完成** | §1（定义来自冻结规则 §3，未新造） |
| 2. 追查故障段并提供修复前证据 | **完成** | §2（①正常，②③④故障，附页面实拍） |
| 3. 观察区显示币种/阶段/最近更新/已满足/未满足；随收盘推进；失效过期按既有规则 | **完成** | §4（18+20 项断言、截图）；失效退出见 §6.2/6.3（`BOX→SWEEP→INVALIDATED`） |
| 4. 观察与执行分开判定；不得因展示下单 | **完成** | 生产侧「持仓 0→0、订单 1→1」；回放侧「观察期间订单 0 / 持仓 0，无违规」 |
| 4'. 满足条件不必等观察期结束即进模拟交易 | **未验证** | 回放未能复现候选（§6.3 数据窗口不足）；生产自然候选为 0。代码层面状态机本来就不看剩余有效期，但**没有实测证据，不算通过** |
| 5. 无观察标的时区分「没有符合条件/数据未就绪/扫描或接口异常」 | **完成** | §4 四态表（5 项全过） |
| 边界：不改冻结策略/阈值/止损/参数/门禁；不启用真实交易 | **遵守** | 未改任何 config 与门禁；仅新增只读导出 |
| 边界：不扩池、不新增下载 | **遵守** | 使用既有 526 合约池，无新增下载 |
| 边界：保留账本/证据/断点，不打断采集 | **遵守** | 重启走 `start.sh`（采集器按防误停策略不动，pid 42278 复用）；生产账本 mtime 未变 |

### 剩余未验证项（明确写「未验证」，不用模拟数据替代）

1. **「观察 → 满足入场条件 → 模拟执行」未验证（阻塞于数据窗口）。**
   本机热库 4h 数据统一只从 `2026-09-09` 起（275 根），而全部 51 个历史候选都发生在此之前或紧邻该起点，
   引擎拿不到足够 4h 历史形成箱体 ⇒ 隔离回放里观察集合全程为 0（§6.3）。
   要在回放里复现这条链，必须先把冷层 parquet 接入运行时取数路径（`MarketDataWatcher.get_closed_bars`
   目前只读热库）。这属于数据层改动，**本轮不擅自改**，列出待裁决。
2. **自然行情下的真实模拟成交未验证。**
   当前自然候选为 0（最近候选 `2026-10-11T20:00Z`，此后长期零候选，原因是规则未触发而非链路故障）。
3. **生产页面上的「已过期」徽标未在自然数据上触发。**
   当前 392 个观察事件全部在有效期内（`expired=false`）。过期判定与展示逻辑已按既有规则实现，
   回放里已观察到事件以 `INVALIDATED` 退出观察集合（§6.2），但生产页面尚无自然样本。
4. **多臂（3b/3c/3d）未接观察区。** 本轮只给 3a 接上（其余臂没有运行中的统一运行时，
   接口如实返回 `not_connected`）。是否推广由后续工单决定。

---

## 8. 复现命令

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node
cd /Users/boos/Desktop/威科夫因子共振趋势

# 修复前（已在 10/20 号脚本里固化；重跑会得到修复后的结果，不要重跑当基线）
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/40_after_fix_checks.py   # 接口层 18 项
cd evidence/SIGNAL-OBSERVE-FIX-12/probes
$NODE 50_verify_after.js        # 页面行为 20 项（含刷新/两轮轮询/关闭重开/账本不变）
$NODE 51_shot_observe_panel.js  # 观察区截图
$NODE 70_ui_states_mock.js      # 四态渲染契约 5 项
cd /Users/boos/Desktop/威科夫因子共振趋势
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/62_replay_lifecycle.py  # 观察生命周期回放（约 6 分钟）
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/63_exit_reason.py       # 退出原因定点取证
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/60_replay_observe_flow.py  # 候选链回放（数据窗口不足，见 §6.3）
```

## 9. 文件清单

```
evidence/SIGNAL-OBSERVE-FIX-12/
├── README.md                       # 本文件
├── probes/
│   ├── 10_before_fix.py            # 修复前四段取证
│   ├── 20_shot_before.js           # 修复前页面实拍
│   ├── 30_probe_observe_error.py   # 隔离探针（抓被吞掉的异常栈）
│   ├── 40_after_fix_checks.py      # 修复后接口层 18 项断言
│   ├── 50_verify_after.js          # 修复后页面行为 20 项断言
│   ├── 51_shot_observe_panel.js    # 观察区截图
│   ├── 60_replay_observe_flow.py   # 候选链回放（未复现，数据窗口不足）
│   ├── 61_debug_replay_window.py   # 回放窗口诊断（为什么 0 根 4h）
│   ├── 62_replay_lifecycle.py      # 观察生命周期回放（进入/退出/不下单）
│   ├── 63_exit_reason.py           # 退出原因定点取证
│   └── 70_ui_states_mock.js        # 四态渲染契约
├── out/                            # 各探针的原始 JSON / 日志输出
└── shots/                          # 修复前后截图
```
