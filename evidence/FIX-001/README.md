# FIX-001｜组合回测时间推进与仓位释放修复（P0）

- 日期：2026-10-27（北京时间）
- 审查基线：`wyckoff-v3.1-main` @ `e5e82640`
- 状态：**工程修复完成、回归测试 17/17 通过**；策略绩效结论仍未验证（见第七节）
- 执行方自报，未经独立验收

---

## 一、根因（代码位置已钉死）

`框架/backend/accum_early_entry.py` 的 `research_run()`，修复前 2164–2180 行：

```python
for sym in symbols:                    # ← 外层按「标的」循环
    r = scan_symbol(..., open_candidates=open_candidates, ...)   # 单标的整段窗口扫完，期间直接开仓
    out["per_symbol"].append(r)
if drive_exits:                        # ← 全部标的扫完之后，才统一驱动退出
    out["exits"] = drive_all_exits(fd, ..., end_ms=end_ms + walk_days*DAY, ...)
```

**机制链条**（不是推测，逐环有代码依据）：

1. `scan_symbol()` 在 `_reap()` 里调 `try_open()`（第 1997 行），**扫描过程中就把仓开了**；
2. 此时账本里该持仓 `status='open'`，因为退出要到第 2 段才写回；
3. 引擎槽位判据是**查库实时判断**——`paper_engine.pre_open_check()` 第 675–677 行：
   `pos = open_positions(con, arm); if len(pos) >= params["max_positions"]: return False, "max_positions_reached"`；
4. ⇒ 只要槽位被占满，后续标的无论其信号时刻晚多少，都一律被拒 ⇒ **全池只成交扫描顺序上的前 10 个候选**。

07-W5 实测：40 标的甲组 105 个候选，**69 个 `max_positions_reached` + 26 个 `position_already_open_no_add`，只成交 10 笔**；乙丙同样 10 笔。

**这是编排顺序问题，不是风控参数问题。** 槽位上限 10 本身没错，错的是「退出从不先于入场发生」。

---

## 二、修法：两段式 + 统一时间轴回放

### 2.1 为什么不是「真·单遍事件循环」

工单要求「改为按统一历史时间戳顺序推进所有标的的信号与仓位事件」。严格单遍实现需要把
`scan_symbol()`（单标的：4h 建档 → 1h 状态机推进）改造成**可中断、可跨标的交替推进的生成器**，
等于重写扫描与状态机——直接违反红线 1（禁止重构整个系统）与红线 2（禁止改信号判定条件）。

因此采用**两段式**，并给出等价性证明（2.2）：信号生成保持原样，只把**仓位事件**放到统一时间轴上回放。
仓位事件是唯一会互相争抢资源（槽位、现金、风险预算）的东西；信号生成不消耗共享资源。

### 2.2 等价性依据（关键，不是口头保证）

AST 逐项核对 `scan_symbol` 全函数体，**唯一**与仓位相关的调用是第 1997 行 `try_open`，
且被 `if open_candidates:` 包住；箱体构建、状态机、候选判定、失效判定**都不读仓位状态**。
⇒ 以 `open_candidates=False` 扫描，候选产出与开仓时**逐条相同**。

实测佐证：`ZORAUSDT` 单标的扫描 `boxes_used=39 / actions=164 / 候选=11`，与 07-W5 产物
（`boxes_used=39 / actions=164 / n_candidates=11`）**三项完全一致**。

### 2.3 新增三个函数（都在 `accum_early_entry.py`）

| 函数 | 职责 |
|---|---|
| `_timeline_events(per_symbol)` | 把各标的候选拍平成统一事件表，按 `(时间戳, symbol, scheme, signal_uid)` 排序 |
| `_advance_open_positions(...)` | 把各方案存量持仓推进到某时刻；触发的立即平仓（槽位即刻可用），未触发的上移跟踪止损 |
| `_replay_timeline(...)` | 按时间戳回放：同刻先退出释放槽位 → 再评估入场 |

### 2.4 三步顺序如何落实

工单要求同一时间戳严格三步。实现映射：

| 工单步骤 | 实现 |
|---|---|
| ① 处理存量持仓平仓/退出，**立即释放槽位** | `_advance_open_positions()` 调 `drive_position_exit(end_ms=该时刻)`；平仓走 `paper_engine.close_at_price()` 落库，返回后 `open_positions()` 立即查不到它 |
| ② 更新剩余存量持仓状态 | 同一次 `drive_position_exit` 内完成：每根 15m 先按 4h 最低价上移跟踪止损（`record_stop_update`），再判 15m 退出。**① 与 ② 在同一调用内、且顺序正确** |
| ③ 评估新入场并分配槽位 | 循环里对该时刻候选逐个 `try_open()`，槽位由引擎 `pre_open_check()` 实时判定 |

时间戳取 **`entry_bar_open_ms`（成交 bar 开盘时刻）**，与 `try_open()` 里 `attempt_ms` 的口径一致；
不用 `confirm_close_ms`，否则会与引擎 `bar_after_attempt_clock_no_lookahead` 闸门口径打架。

### 2.5 多标的竞争确定性排序

排序键 `(时间戳, symbol, scheme, signal_uid)`：
- 时间戳是「同一时刻」的唯一判据；
- **`symbol` 字母升序**——同刻多候选抢槽位时结果只由 symbol 决定，与输入币种列表顺序**完全无关**；
- `scheme`/`signal_uid` 仅作兜底（正常不会同时命中）。

排序在 `_timeline_events()` 内一次性完成，回放阶段不再依赖输入顺序。

### 2.6 旧行为保留方式

新增参数 `timeline_entries: bool = True`（默认＝修复后行为）。
`timeline_entries=False` 保留旧「按标的边扫边开 + 退出后置」路径，**仅供复现历史证据**，
出成绩一律不得使用。

---

## 三、改动范围（AST 证明，不是声明）

对改动前后做顶层定义级 sha256 比对：

```
新增定义: ['_advance_open_positions', '_replay_timeline', '_timeline_events']
删除定义: []
内容改动: ['research_run']          ← 只有这一个已有函数被改
未变定义数: 44
```

关键函数逐项确认**全部未变**：

```
scan_symbol            未变        try_open               未变
drive_position_exit    未变        drive_all_exits        未变
check_fixed_rules      未变        build_boxes            未变
ledger_isolation       未变        load_config            未变
_absorption            未变        open_positions         未变
ledger_snapshot        未变
```

模块级常量（含 `FIXED_RULES`、`SCHEMES`、`VERSION`、`EXIT_RULES`、`RULE_VERSION`）
**改动 0、新增 0、删除 0**。

| 对象 | 改前 | 改后 |
|---|---|---|
| `accum_early_entry.py` sha256 前 16 | `65b0ee70bb0283ff` | `fc86eea46684b8a0` |
| 字节数 | 119,895 B | 127,969 B |
| 行变化 | — | +151 / −3 |

**未改动**：`paper_engine.py`、`replay_calc.py`、`策略/**`、冻结 `event_trend_v0_1/config.py`、
账本、历史数据、旧报告。完整 diff 见 `out/01_code_diff.patch`，改前原文件见 `runs/pre_fix_accum_early_entry.py`。

---

## 四、测试

### 4.1 跑法

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/wyckoff-framework/bin/python

# 只跑可移植用例（不读行情库，克隆到任何机器都能跑）
$PY evidence/FIX-001/scripts/test_fix001_timeline.py

# 追加真实数据端到端（需本机行情库 + fixture 配置）
$PY evidence/FIX-001/scripts/test_fix001_timeline.py --integration
```

### 4.2 结果：**17 项全部通过**（`out/02_test_output.txt`，退出码 0）

| 组 | 用例 | 结果 |
|---|---|---|
| T1.1 | 排序键为 `(时间戳, symbol, scheme, signal_uid)` | 通过 |
| T1.2 | 同一时间戳内按 symbol 字母升序 | 通过 |
| T1.3 | 打乱输入顺序 **50 次**，事件序列逐条一致 | 通过 |
| T1.4 | 缺时间戳的候选被跳过（不塞到 0） | 通过 |
| T2.1 | A 在 T 时刻占满唯一槽位 | 通过 |
| T2.2 | 同刻 B 因槽位已满被拒 | 通过 |
| T2.3 | **A 平仓后 B 在后续时间戳成功入场** | 通过 |
| T2.4 | 本次共开仓 2 笔（A + B） | 通过 |
| T2.5 | 同一时间戳内**先推进退出、后尝试入场**（调用序列断言） | 通过 |
| T2.6 | **负向对照**：退出不推进时 B 进不来 | 通过 |
| T2b.1 | 打乱输入顺序 **20 次**，成交集合与时刻逐条一致 | 通过 |
| T2b.2 | T 时刻 3 抢 2：只成交字母序前 2 个 | 通过 |
| T2b.3 | 退出释放槽位后，后一时刻 2 个候选全部成交 | 通过 |
| T2b.4 | 合计成交 4 笔（槽位被复用，而非只成交 2 笔） | 通过 |
| T3.1 | 真实数据：打乱币种顺序后持仓逐字段一致 | 通过 |
| T3.2 | 两次运行候选数一致（扫描不受顺序影响） | 通过 |
| T3.3 | **修正后成交笔数严格多于旧行为** | 通过 |

**T3 实测数字**（5 标的 `ZORAUSDT,XTZUSDT,FLUXUSDT,DOLOUSDT,ZETAUSDT`，
窗口 `2026-06-27T08:00Z → 2026-10-26T09:00Z`，`max_boxes=40`，fixture 配置）：

```
候选 53 个 → timeline 模式成交 20 笔，legacy 模式成交 5 笔
```

即：同一批候选，修复后成交数是旧行为的 **4 倍**，槽位确实在滚动复用。

### 4.3 T2 为什么用插桩替身（必须说清）

`paper_engine.open_position()` 是 fail-closed 的：要过 `new_entry_gate()`（默认拒绝一切新开仓）、
`SC.validate_entry_contract` 配置指纹、paper epoch 三道硬闸，还要合约规格 CSV 与行情库。
构造满足它们的合成环境 ≈ 重写一个引擎。因此：

- **T2 用插桩替身只验证「本次改动的那段编排逻辑」**——同刻是否先退出再入场、结果是否与输入顺序无关；
- **真实引擎下的成交结果由 T3 负责**（真实行情库 + 真实 `paper_engine` + 隔离账本）。

### 4.4 测试自身的两个坑（已修，记录备用）

1. **共享 observe 库导致候选被 dedup 全跳过**：探针一度 0 候选，而 07-W5 同标的 11 个候选。
   根因是 `_reap()` 里 `dedup=True` 的候选**不进** `out["candidates"]`，而默认 observe 库是
   07-W5 用过的共享库。⇒ 每次运行必须给独立 `observe_db` 并清空。
2. **空对空假通过**：BTC/ETH/SOL 本身 0 候选，`0 == 0` 会误判「一致」。已加守卫：
   `候选数 == 0` 时明确跳过并说明「不构成证据」，不判通过。

---

## 五、剩余风险

1. **07-W5 及更早的组合成绩全部作废**。修复改变的是「哪些成交会发生」，不是记账细节 ⇒
   旧报告的 10 笔/组成绩不能用，必须用本版重跑。逐笔口径的旧结论（05-C 等）不受影响。
2. **`position_id` 与成交顺序会变**。任何按 `position_id` 对齐的历史对账脚本需要重跑。
3. **性能开销**：每遇到一个候选时间戳，要对「该刻有候选的方案」的存量持仓各推一次
   `drive_position_exit`（每次都从 `entry_ts` 重新走 15m）。5 标的实测 20 笔约 13s/次运行；
   526 标的全池耗时尚**未测**，预计明显高于 07-W5 的 215–282s。
4. **并行窗口干扰（本次实测遇到）**：测试期间另一窗口正在改 `paper_engine.py`/`replay_calc.py`
   （做 FIX-002），导致 T3.1 出现过一次瞬时不一致，复跑两次均通过。**复跑前必须先确认
   共享文件身份未漂移**，否则差异不能归因于本单。
5. **`timeline_entries=False` 是后门**。它保留旧缺陷行为，若下游误用会拿到错误成绩。
   建议 FIX-002 之后考虑移除或加显式告警。
6. **只对「同一 scheme 内」保证确定性**。4a/4b/4c 三本账本各自独立（不同 `account_id`），
   跨方案不共享槽位，因此跨方案顺序不影响结果；但若将来改成共享资金池，需重新设计排序。

---

## 六、未验证 / 不能说的话

- **不能声称「组合回测已可用于拍板」**：阈值仍是 `kind=fixture`、`approved=false`，
  13 项待决参数用的是工程占位值。本次只修编排缺陷，不涉及参数批准。
- **不能声称「策略变好/变差」**：成交从 5 笔变 20 笔只说明槽位在滚动，样本量与绩效无统计意义。
- **未跑 526 标的全池**，未做 Walk-Forward，未接资金费（`paper_engine.funding_events` 仍只读热库，
  380h 缺口未修）。
- **T2 的槽位模型是替身**，不等价于引擎全部风控（现金储备、组合止损风险、回撤锁定等）；
  这些由 T3 的真实引擎覆盖，但 T3 只有 5 标的。
- 未验证并发/多进程下的时间轴回放（当前是单进程串行）。

---

## 七、产物清单

```
README.md                          本文件
scripts/test_fix001_timeline.py    回归测试（T1/T2 可移植，T3 需行情库）
out/01_code_diff.patch             代码 diff（+151/−3）
out/02_test_output.txt             测试运行日志（17/17 通过，退出码 0）
runs/pre_fix_accum_early_entry.py  改前原文件（sha 65b0ee70bb0283ff，119,895 B）
runs/integration/                  T3 隔离账本/观察库/闸门（可删，重跑自动重建）
```
