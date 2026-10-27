# 29｜SIGNAL-HISTORY-FIX-01 收口回执

- 工单：`SIGNAL-HISTORY-FIX-01｜查清零信号真实原因 + 修复历史状态恢复/实时衔接/展示`
- 证据：`evidence/SIGNAL-HISTORY-FIX-01/`（报告 + 9 脚本 + 产物，清单见 `out/08_产物清单与上游快照.json`）
- 收口时刻：`2026-10-24T14:00Z` 前后
- **状态：归因已定案（数据不足为主因）；6 项缺陷中 3 项已修、1 项潜在、2 项排除；修复已落盘并通过全池回归，但生产进程未重启 ⇒ 未生效**
- 参考时刻：`2026-10-24T12:59:59Z`（全池最后一根已闭合 1h 的 close，数据相对）

---

## 一、这一单回答了什么

| 问题 | 回答 | 证据 |
|---|---|---|
| 零信号是**数据不足**？ | **是，主因**。526 标的 **0 个**覆盖 180 天；1h 仅 **47.46 天**，而预热就要吃掉 **47.46 天**（生产 48.3 天）⇒ 启动后无新数据余量。内部缺口 **0**（不是数据质量问题，是长度不够） | `out/01_覆盖审计.json` |
| 是**条件未达到**？ | **是，策略正常**。已推进 396（BOX 370/RETEST 18/SWEEP 8）+ 冷却 106 + 等箱体 21 = 523 | `out/03_漏斗汇总.json` |
| 是**数据坏死被当没信号**？ | **3 个币**（`1000000BOBUSDT`/`PROMPTUSDT`/`PUMPBTCUSDT` 4h 全零 114 根 ⇒ ATR=0）。**已修展示** | `out/04_缺陷复现_after.json` |
| 是**状态恢复错误**？ | **不是**。单标往返逐字段相同；中途换引擎续跑 **28/28 标的 0 差异**；三路 P1≡P2≡P3 全 true | `out/05_三路衔接对比.json`、`out/04_*` |
| 是**实时推进错误**？ | **不是**。同一 `upto_ms` 连跑三次 `step()` → 0/0/0 新候选；三路 `n_diff=0` | `out/05_三路衔接对比.json` |
| 是**展示问题**？ | **是，已修**。`IDLE=130` 原被压成一个数；启动瞬间 526/526 标的带 1 根未处理尾根，其中 **3 个**阶段是过期的 | `out/06_修复后全池复核.json` |
| ATR 预热口径一致？ | **不一致**。声明 200 根 / 回测全历史连续 / **实时只取 42 根且每根重起算**。119,055 时点上中位差 0.9555%、p95 4.1705%、最大 87.7033%，宽度闸翻转 1,728 次（1.4514%）。**只记录不修改** | `out/02_ATR预热对照.json` |
| 历史候选能下单？ | **不能，且不应**（历史候选不下单是预期）。4 个：BBUSDT/SUSDT/VELVETUSDT/WALUSDT | `out/03_漏斗汇总.json` |
| 执行链路通过真实候选跑过？ | **没有**。账本 **0 行**可追溯到真实候选；`live_signal` **0 行**；3 个持仓全是测试驱动 | `out/07_执行链路核对.json` |

## 二、改了哪些文件（唯一一个）

`框架/backend/unified_runtime.py`

| | sha256 | 大小 |
|---|---|---|
| 修复前 | `a4292310c8163602719e2fa434f742a4224c64c73cc20b187d9fb2d96d276c0c` | 70,966 B |
| 修复后 | `b3dd374490ca18da469620aa6494206f93166f68c74b08308e6890cff041297a` | 80,221 B |

三处改动：
1. `bootstrap()` 预热水位改取「状态机**实际处理过**的最大 1h `open_time`」（D1）。
2. 新增 `MultiSymbolSignalEngine.idle_breakdown()`，把 `IDLE` 拆成 6 类，挂到 `get_full_status().signal_engine.idle_breakdown`（D4/展示）。
3. `MultiSymbolSignalEngine.export_state()` 如实声明 `state_persisted=False` / `restore_supported=False` / `recovery_method="bootstrap_full_replay"`（D2 诚实化）。

**冻结策略五个文件 SHA 逐项核对未变**（`config.py d51e188a…` / `state_machine.py 40ae81e2…` / `features.py 163194b3…` / `contracts.py 6895e094…` / `runner.py 3f47ba2b…`）。

## 三、修复前后（硬证）

| 项 | 修复前 | 修复后 |
|---|---|---|
| 全池带尾根标的数 | **526 / 526**（各 1 根） | **0** |
| `step_has_new_true` | 0 / 526（整轮空转） | 526 / 526 |
| 启动瞬间阶段过期的标的 | **3**（0GUSDT BOX→SWEEP、DIAUSDT RETEST→IDLE、ONEUSDT BOX→IDLE） | 0 |
| `IDLE` 可分因 | 1 个数字（130） | 6 类（冷却 106 / 等箱体 21 / **坏死 3**） |
| 迁移总数 / 历史候选 / 当前阶段 | BOX 3311 … CANDIDATE 4；BOX 370/IDLE 130/RETEST 18/SWEEP 8 | **逐项相同（无回归）** |
| 三路衔接 | ALL_PASS=True | **ALL_PASS=True** |
| 报告数字机械复算 | — | **76 / 76 PASS**，伪造声明 0 |

## 四、【必读】两条硬约束

### 4.1 修复**未生效**：生产进程仍在跑旧镜像

`pid 59109` 启动于 `2026-10-24T17:49:02`，文件 mtime `2026-10-24T13:48`（本地时区）⇒ 进程早于修复。
**生效需重启**：`python 框架/backend/service_manager.py restart --enable-paper --arm 3a --symbol all`。
重启会重放 48.3 天预热（约 21 s）并让 `since_start` 归零（`funnel.history` 会立刻被重放填回）。本单未擅自执行。

### 4.2 「状态恢复」在代码上确实断了，但**不是**零信号的原因

- L1 `EventStateMachine`：往返无损（`last_clock` 除外）。
- L2 `RealSignalEngine`：丢 `history_floor_ms` / `signals_generated` / `last_cycle_ms`（调用方必须手工补）。
- L3 `MultiSymbolSignalEngine`：`export_state` 是桩，**无 `restore_state`**。
- L4 `UnifiedRuntime._recover_state_on_startup`：只数账本行数，不碰信号引擎。

⇒ 生产重启**一律走 bootstrap 全量重放**。**不补快照恢复是刻意的**：`export_state()` 含不裁剪的 6 个 1h 数组，全池序列化 ~10² MB，远贵于 21.4 s 重放，且重放等价性已实测成立。**不要把这条写成"待修缺陷"**，它是有理由的取舍。

## 五、留给后续单的三件事

1. **重启生产进程让修复生效**（运维动作，需人确认）。
2. **`atr_warmup_bars` 口径统一**（实时 42 根重算 vs 声明 200 根）：属冻结口径变更，须走裁决流程。
3. **`paper_order` 补 `signal_uid`**：现在只有 `event_uid`（事件级），订单无法按候选逐条对账；属账本 schema 变更，须独立立项。
