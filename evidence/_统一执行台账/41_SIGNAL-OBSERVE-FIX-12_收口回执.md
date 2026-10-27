# 41｜SIGNAL-OBSERVE-FIX-12 收口回执（观察期无响应）

- **工单**：SIGNAL-OBSERVE-FIX-12｜修复观察期无响应
- **用户要求（原话要点）**：观察期与模拟实盘并行；没有满足入场条件，不应导致观察区没有反应
- **证据目录**：`evidence/SIGNAL-OBSERVE-FIX-12/`（`probes/` `out/` `shots/`）
- **收口状态**：**完成（含 3 项明确「未验证」）**；交付方自测已完成，**待 Codex 独立验收**
- **判定纪律**：不以本回执或截图单独判定完成；本回执只记录**可复算**的实测结果

---

## 1. 结论一句话

观察区**不是没数据，是没接线**：状态机一直在产出「处于观察状态的事件」（修复前实测全池 346 个），
接口只给 `{币种: 阶段名}`，前端连这个字段都没解析、也没画任何明细 ⇒ 页面只剩一句
「箱体观察 304 · 已扫低 21 · 待突破 21」，还折叠在诊断块里。

本轮把「状态机 → 接口逐条明细 → 页面常显观察区」补通，并保持**观察与执行分开判定**。

## 2. 观察期的权威定义（未新造规则）

`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` §3：「每个 symbol+venue 最多一个处于观察状态的事件」
⇒ 观察中 = `BOX` / `SWEEP` / `RETEST`；`CANDIDATE` 及之后已结束，`IDLE` 没有事件。
进入 = 事件建档；退出 = 升候选或按既有规则失效/过期。**未放宽任何门槛。**

## 3. 故障段定位（修复前，有证据）

| 段 | 修复前实测 | 判定 |
|---|---|---|
| ① 状态机 | 346 个观察中（BOX 304 / SWEEP 21 / RETEST 21） | 正常 |
| ② 接口 | 只有 `per_symbol_state {symbol: 阶段名}`，无箱体/时间/条件 | 故障 |
| ③ 前端类型层 | `ExecutionDaemonView` 无 `perSymbolState`，解析函数不读 `per_symbol_state` | 故障 |
| ④ 前端渲染 | 只把 `stateCounter` 拼成一句计数串；整块在 `<details>` 折叠里 | 故障 |

页面实拍：`shots/signal_panel_before.png`（「观察」出现 0 次、逐币行 0 行）。

## 4. 改动清单

**后端**
- `unified_runtime.py`：新增 `MultiSymbolSignalEngine.observe_events()` / `observe_summary()`、
  `observe_file_path()`、`UnifiedRuntime._save_observe_file()`、`get_observe_events()`；
  `step_once()` 末尾落明细；`get_full_status()` 改放汇总；`bootstrap()` 末尾清观察缓存。
- `api/routes.py`：新增 `_signals_observe()` 与 `Route("/api/v1/signals/observe")`（加性只读）。

**前端**
- `api/liveTrading.ts`：新增 `ObserveItem/ObserveView/classifyObserve/toObserveView/fetchObserveEvents`；
  `ExecutionDaemonView` 补 `perSymbolState` + `observeSummary` 并真正解析。
- `components/ObservePanel.tsx`（新增）、`components/SignalListPanel.tsx`（并列常显）、`App.tsx`（15s 轮询）。

**未改**：冻结规则 md、`config.py` 任何阈值、止损、执行门禁、交易参数；未启用真实交易；未扩池、未新增下载。

## 5. 关键工程决策（体积）

状态文件每 2 秒全量重写。346 条明细并入 ⇒ 每 2 秒约 390 KB ⇒ **约 9~13 GB/天**写盘。
改为：状态文件只放几百字节汇总（实测 **81,830 B**），明细走独立文件（**312,756 B** / 392 条），
**内容不变且未超 5 分钟不写**。两条通道调同一个 `observe_events()`，口径不分叉。

## 6. 修复后实测（可复算）

- `probes/40_after_fix_checks.py`：**18/18 通过**。观察集合与 `per_symbol_state` 完全一致（392 == 392），
  无重复，无非观察态混入，持仓 0 条，`truncated=False`。
- `probes/50_verify_after.js`：**20/20 通过**，0 控制台错误。含刷新、两轮轮询、关闭重开均无重复无残留；
  账本 `持仓 0→0`、`订单 1→1`（观察展示不下单）。
- `probes/70_ui_states_mock.js`：**5/5 通过**，四态文案互不相同
  （数据未就绪 / 没有符合条件的标的 / 观察区读取异常 / 有观察标的）。
- 观察随收盘推进：23:46 为 346（304/21/21）→ 00:08 为 392（350/17/25）。

## 7. 修 bug 时抓到的三个静默失败（值得留档）

1. `_save_observe_file` 调了 `self.observe_events()`（方法在信号引擎上，不在运行时）⇒ `AttributeError` 被 `except: pass` 吞掉，文件永不生成。
2. 构造期写状态文件 → `observe_summary()` → `observe_events()`，此时引擎为空，**空结果被按 60 秒 TTL 缓存** ⇒ 预热完成后仍返回 0（346 报成 0）。
3. `scan_errors` 是**字典**不是列表，`[-5:]` 抛 `KeyError: slice(...)`，同样被吞。

⇒ 现改为**不允许静默失败**：写不出来就落 `generation_error`，接口报 `error`，页面显示「观察区读取异常」。

## 7. 隔离历史回放（实测）

| 验证点 | 结果 |
|---|---|
| 观察事件在回放里真的会出现 | **通过**（5 个标的进入观察集合） |
| 观察期间不下单 | **通过**（隔离账本订单 0 / 持仓 0，无违规） |
| 观察事件按既有规则退出 | **通过**（`0GUSDT` 迁移链 `BOX → SWEEP → INVALIDATED`） |
| 观察数量随收盘推进变化 | **通过**（4 ↔ 5） |
| 「观察 → 满足入场条件 → 模拟执行」 | **未验证（数据窗口不足）** |

- `probes/62_replay_lifecycle.py`：窗口最近 5 天、121 步、6 个真实箱体观察标的 ⇒ 6 项断言 5 通过；
  唯一失败项「读不到终止态」是读取位置错（事件结束时 `active_event` 被置空），
  已由 `probes/63_exit_reason.py` 定点取证补上：`transition_counts = {BOX:1, SWEEP:1, INVALIDATED:1}`。
- `probes/60_replay_observe_flow.py`：试图用真实历史候选（BBUSDT/BNBUSDT/CHZUSDT）回放候选链，
  **未能复现**（288 步观察集合全程 0）。原因已定位：热库 4h 只从 `2026-09-09` 起（275 根），
  候选时刻附近 4h 历史远少于 `atr_warmup_bars=200`，箱体不成立。**判未验证，不当通过。**

## 8. 未验证项（明确标注，不用模拟数据替代）

1. **「观察 → 满足入场条件 → 模拟执行」未验证**：阻塞于数据窗口（见 §9），
   且当前自然候选为 0（最近 `2026-10-11T20:00Z`）。
2. **自然行情下的真实模拟成交未验证**。
3. **生产页面「已过期」徽标未在自然数据上触发**：392 个观察事件全部在有效期内。
4. **3b/3c/3d 未接观察区**：仅 3a 接上，其余臂接口如实返回 `not_connected`。

## 9. 数据可用性缺口（需裁决，非本轮可改）

热库 `market_v2.sqlite3` 的 4h 数据**统一只从 2026-09-09 起（275 根）**，
更早历史在冷层 parquet，而 `MarketDataWatcher.get_closed_bars` **只读热库**。
⇒ 任何早于 09-09 的历史候选都无法在隔离回放里重建（预热拿 0 根 4h，引擎按
`4h_bars_insufficient(0<50)` 跳过）。**是否把冷层接入运行时取数路径，需另行裁决**
（本轮不擅自改数据层，以免影响生产取数口径）。

## 10. 复现命令

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node
cd /Users/boos/Desktop/威科夫因子共振趋势
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/40_after_fix_checks.py
cd evidence/SIGNAL-OBSERVE-FIX-12/probes && $NODE 50_verify_after.js && $NODE 70_ui_states_mock.js
cd /Users/boos/Desktop/威科夫因子共振趋势
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/62_replay_lifecycle.py
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/63_exit_reason.py
$PY evidence/SIGNAL-OBSERVE-FIX-12/probes/60_replay_observe_flow.py
```

## 11. 运行态（本次收口时）

- API `:8787`、前端 `:3000`；运行时 arm 3a 由 `start.sh restart --enable-paper --arm 3a --symbol all` 拉起
- 采集器未中断（按防误停策略复用 pid 42278）
- 生产账本 `数据/paper/paper_3a.sqlite3` 未被本轮回放触碰（回放全部在临时目录）
