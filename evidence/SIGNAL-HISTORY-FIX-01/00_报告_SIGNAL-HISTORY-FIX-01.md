# SIGNAL-HISTORY-FIX-01 收口报告：零信号的真实归因、缺陷修复与衔接验证

- 项目：`/Users/boos/Desktop/威科夫因子共振趋势`
- 证据目录：`evidence/SIGNAL-HISTORY-FIX-01/`（`code/` 脚本 + `out/` 结果）
- 参考时刻：`2026-10-24T12:59:59Z`（= 全池最后一根已闭合 1h 的 `close`，**数据相对，不是挂钟**）
- 冻结策略：`WX_EVENT_TREND_V0_1`，本单**未改任何策略参数**（`config.py` / `state_machine.py` / `features.py` / `contracts.py` / `runner.py` 五个文件 SHA 与开工记录逐项相同，见 `out/08_产物清单与上游快照.json` → `frozen_strategy_unchanged=true`）
- 本单唯一改动的运行文件：`框架/backend/unified_runtime.py`
  - 修复前 `sha256 = a4292310c8163602719e2fa434f742a4224c64c73cc20b187d9fb2d96d276c0c`（70,966 B）
  - 修复后 `sha256 = b3dd374490ca18da469620aa6494206f93166f68c74b08308e6890cff041297a`（80,221 B）

---

## 0. 一句话结论

**零信号的主因是「数据不足 + 供给稀缺」，不是状态恢复错误、不是实时推进错误。**
本地行情只有 **47.46 天**（1h），而预热窗口本身就要吃掉 **47.46 天**（生产 48.3 天）⇒ 引擎启动后**几乎没有"启动后新数据"余量**；全池 526 标的在 47.46 天里只走过 **4 次**到候选关，历史候选按纪律不下单。**展示问题确实存在**（`IDLE=130` 被压成一个数字、3 个币的数据坏死看不出、启动时 3 个币的阶段是过期的），已修。**状态恢复错误与实时推进错误经实测被排除**，但"状态恢复"这条链在代码上确实是断的（不是零信号的原因，但会让"0"被反复误读）。

---

## 1. 最终回答：零信号属于哪一类？各多少币？

| 归因类别 | 币数 | 判定 | 关键证据 |
|---|---|---|---|
| **① 数据不足** | **526 / 526** | **成立（主因）** | 0 个标的覆盖 180 天；1h 仅 47.46 天、4h 44.54 天、15m 42.58 天；距 180 天缺 132.54 / 135.46 / 137.42 天；三周期内部缺口均为 **0**（不是数据质量问题，是历史长度就这么多） |
| **② 条件未达到（策略口径正常等待）** | **523 / 526** | **成立（正常）** | 已进入结构推进 **396**（BOX 370 + RETEST 18 + SWEEP 8）；冷却期内 **106**；箱体宽度/ER 未达 **21** |
| **③ 数据坏死（被展示成"没信号"）** | **3 / 526** | **成立（展示缺陷，已修）** | `1000000BOBUSDT` / `PROMPTUSDT` / `PUMPBTCUSDT` 的 4h 从 `2026-10-05T08:00Z` 起全零（`volume=0`、`high==low==close`）⇒ ATR=0 ⇒ 静默不建档 |
| **④ 状态恢复错误** | **0 / 526** | **不成立（已排除）** | 内建 `export_state/restore_state` 单标往返**逐字段相同**；中途换引擎续跑与不换引擎跑到同一截止时刻 **28/28 标的 0 差异**；三路衔接 P1≡P2≡P3 全 true |
| **⑤ 实时推进错误** | **0 / 526** | **不成立（已排除）** | 同一 `upto_ms` 连跑三次 `step()` → 新候选 0 / 0 / 0（不重复发信号）；三路状态 `n_diff=0` |
| **⑥ 展示问题** | **526 / 526（读数口径）** | **成立（已修）** | `state_counter` 把 `IDLE=130` 压成一个数；启动时刻 **526/526** 标的带 1 根未处理尾根，其中 **3** 个标的阶段是过期的 |

**结论口径**：④⑤ 不是原因（有反向证据）；①是主因；②是策略正常；③⑥是真实缺陷，已在本单修复并给出前后证据。

---

## 2. 历史读取与数据覆盖（工单「二」）

脚本 `code/01_coverage.py` → `out/01_覆盖审计.json`、`out/01_逐币覆盖.csv`（526 行 × 逐周期 8 字段）。

| 周期 | 标的数 | 有数据 | **覆盖 180 天** | 全池最早 | 全池最晚 | 实际跨度 | 距 180 天缺 | 内部缺口 |
|---|---|---|---|---|---|---|---|---|
| 1h | 526 | 526 | **0** | `2026-09-07T02:00Z` | `2026-10-24T12:00Z` | **47.46 天** | **132.54 天** | **0** |
| 4h | 526 | 526 | **0** | `2026-09-09T20:00Z` | `2026-10-24T08:00Z` | **44.54 天** | **135.46 天** | **0** |
| 15m | 526 | 526 | **0** | `2026-09-12T00:00Z` | `2026-10-24T13:00Z` | **42.58 天** | **137.42 天** | **0** |

- 请求范围：`2026-04-27T12:59:59Z` ~ `2026-10-24T12:59:59Z`（180 天）
- **不满足 180 天**：`data_sufficient_for_180d = false`，三周期 `symbols_covering_180d` 全为 **0**。
- **本单不下载**（`downloaded_anything=false`），如实标注缺口，不伪造、不默默缩短。
- 逐币记录字段：请求范围 / 实际范围 / 各周期根数 / 天数 / `covers_180d` / `inner_gap_bars` / `pre_listing_shortfall_days`。**上市晚的币与中间有洞分开统计**，不混成一个"不足"。
- **读半年 ≠ 改策略窗口**：`box_bars_4h=42`、`er_threshold=0.30`、2A~8A、风险与执行规则全部沿用，一个字节没动。

> 结构性事实（不是缺陷，需人决策）：数据 47.46 天，预热就要 47.46 天（`bootstrap._window_hours=1139.0`；生产 `1160h=48.3 天`）⇒ **引擎启动后没有"启动后新数据"余量**。这是本单所有"0"的地基。

---

## 3. ATR 预热口径核对（工单「二·核对 ATR」）

脚本 `code/02_atr_warmup.py` → `out/02_ATR预热对照.json`、`out/02_ATR逐币差异.csv`。

| 口径 | 实现位置 | 实际预热 | 与声明一致？ |
|---|---|---|---|
| **声明** | `config.py:27` `atr_warmup_bars=200` | 200 根 4h | — |
| **回测** | `runner.py:225-230 / 297-301` | 全历史一次连续 Wilder ATR(14)，逐根取 `[-1]`；窗口回取 220 根 | **一致** |
| **实时** | `unified_runtime.py:308-318` | **每根 4h 只取最近 42 根重新起算**（播种第 14 根，之后只平滑 28 根） | **不一致** |

在 **119,055 个 4h 时点**上的实测影响：

- ATR 相对差：**中位 0.9555%**、**p95 4.1705%**、**最大 87.7033%**
- 相对差 `>1%`：**57,566** 根；`>5%`：**4,062** 根
- 宽度闸（`2A <= (H-L)/A <= 8A`）判定翻转：**1,728 次 = 1.4514%**（engine 通过/回测不通过 691；engine 不通过/回测通过 1,037）
- 「宽度 + ER 双过」建档判定翻转：**engine_only=633 / bt_only=846**

**处置：只记录，不修改。** `atr_warmup_bars` 属冻结配置，改它即改策略口径，须走裁决流程。本单不动，只把影响量化留档。

---

## 4. 让「为什么没有信号」可以查到（工单「三」）

脚本 `code/03_funnel_why.py` → `out/03_漏斗汇总.json`、`out/03_逐币漏斗与原因.csv`（526 行逐币）。

**全池 526 标的预热回放 21.4 s 完成**，`ready=526 / skipped=0`。

### 4.1 历史漏斗（预热回放期走过的每一关）

| BOX | SWEEP | RETEST | **CANDIDATE** | VOLUME_REJECTED | OI_UNAVAILABLE | OI_REJECTED | INVALIDATED | EXPIRED | MISSED_WITHOUT_RETEST |
|---|---|---|---|---|---|---|---|---|---|
| 3311 | 675 | 408 | **4** | 0 | 0 | 0 | 1662 | 1240 | 9 |

> **1,106 + 1,662 + 1,240 + 9 条淘汰里，没有一条是 OI 相关**——生产跑的是 B 组（`enable_oi_filter=False`，`unified_runtime.py:1397` 未传该参数），OI 三道闸门整段跳过。

### 4.2 当前阶段（必须分开列，不压成一个"0"）

| BOX | SWEEP | RETEST | IDLE 合计 | 其中：冷却 | 其中：等箱体 | 其中：**数据坏死** |
|---|---|---|---|---|---|---|
| 370 | 8 | 18 | **130** | 106 | 21 | **3** |

**0 个标的箱体数为 0** ⇒ 每个标的都至少形成过一次箱体，不是"根本没跑起来"。

### 4.3 4 个历史候选（逐项可追溯）

| 标的 | 候选时刻（1h 收盘） | 突破收盘 | 初始止损 | 箱体 ER |
|---|---|---|---|---|
| BBUSDT | `2026-10-11T07:00:00Z` | 0.010308 | 0.00842493 | 0.0961 |
| SUSDT | `2026-10-06T06:00:00Z` | 0.04433 | 0.03703084 | 0.2238 |
| VELVETUSDT | `2026-10-02T15:00:00Z` | 0.06737 | 0.05427380 | 0.0635 |
| WALUSDT | `2026-10-10T11:00:00Z` | 0.04253 | 0.03230604 | 0.0815 |

**历史候选不下单是预期行为**（`history_candidates_discarded=4` 单独留痕，不补开过期订单）。

### 4.4 与生产状态交叉核对（强证据）

生产 `框架/backend/locks/paper_runtime_3a_status.json`：`state_counter = {BOX:370, IDLE:130, RETEST:18, SWEEP:8}`、`signals_generated_total=0`、`funnel.history={BOX:3303, SWEEP:672, RETEST:403, CANDIDATE:4, INVALIDATED:1640, EXPIRED:1239, MISSED:8}`、`bootstrap._window_hours=1160`。

我的独立回放：`BOX 370 / RETEST 18 / SWEEP 8` **逐项吻合**；`IDLE 130 = 冷却 106 + 等箱体 21 + 坏死 3` **正好等于生产 IDLE=130** ⇒ 独立回放与生产同构。

---

## 5. 历史与实时衔接（工单「四」）

脚本 `code/05_linkage.py` → `out/05_三路衔接对比.json`、`out/05_候选逐项对照.csv`、`out/05_状态逐币对照.csv`。

隔离方式：自建独立行情库（只导出所选标的 1h/4h），注入 `MARKET_DB / LIVE_DIR / PAPER_LEDGER_DIR / PAPER_LOCK_DIR` 环境变量后重导入 `unified_runtime`，断言 `UR.L.MARKET_DB == ISO_DB`；**不调用 `step_once`，不写任何账本**。

- `T_boot=2026-09-20T00:00Z`、`T0=2026-10-01T00:00Z`、`Tm=2026-10-12T06:00Z`（非对齐）、`Tm_aligned=2026-10-12T00:00Z`、`T1=2026-10-24T12:59:59Z`
- 保留 **23** 个标的（在 `T_boot` 达不到 bootstrap 门槛的 13 个如实剔除并记录原因，**没有偷偷改门槛**）

| 路径 | 说明 | 候选（窗口内） | 步数 |
|---|---|---|---|
| P1_continuous | 连续历史回放 | 1 | 829 |
| P2_init_then_append | 历史初始化后逐根追加 | 1 | 565 |
| P3_restart_midway | 中途重启（非对齐时刻） | 1 | 565 |
| P3b_restart_aligned | 中途重启（对齐时刻，对照） | 1 | 565 |

```
cand  P1_vs_P2 identical=True   P2_vs_P3 identical=True
      P1_vs_P3 identical=True   P2_vs_P3b identical=True
state P1_vs_P2 n_diff=0   P2_vs_P3 n_diff=0
      P1_vs_P3 n_diff=0   P2_vs_P3b n_diff=0
dup: first_run_new=0, second_run_same_t=0, third_run_after_reactivate=0
VERDICT: ALL_PASS = True
```

逐字段比对范围：`direction / breakout_close / initial_stop_price / box_high_h / box_low_l / box_atr_a`，以及 `current_state / event_id / created_at_ms / sweep_time_ms / retest_time_ms / sweep_low_s / retest_low_lr / n_transitions / n_candidates / last_processed_1h / engine_last_1h_open / cooling_until_ms / n_completed`。

> **覆盖缺口（必须写明）**：4 个已知历史候选里，`SUSDT / VELVETUSDT / WALUSDT` 在 `T_boot` 时刻 1h 只有 192 根（<200）、4h 只有 61 根，被如实剔除 ⇒ **三路对比只覆盖到 BBUSDT 这 1 个已知候选**，不能声称"4 个候选全部验证"。要覆盖全部 4 个，`T_boot` 必须晚于 `2026-09-26`，代价是可用标的大幅减少；这是选点权衡，不是结论缺口。

**结论**：`step()` 本身正确（不丢状态、不重复发信号）；问题不在推进逻辑，在 `bootstrap()` 的记账（D1）与"恢复"的缺失（D2）。

---

## 6. 缺陷清单与修复（工单「修复…并保留修复前后证据」）

脚本 `code/04_defects.py --tag before|after` → `out/04_缺陷复现_before.json` / `out/04_缺陷复现_after.json`；全池复核 `code/06_after_fix_check.py` → `out/06_修复后全池复核.json`。

### D1【真实缺陷｜已修】bootstrap 把"最后一根未处理的 1h"标成已处理

- **位置**：`unified_runtime.py:525`（记账）、`:593-596`（`has_new` 判据）
- **机制**：预热回放只按 **4h 收盘时刻**驱动 `process_market`，而它内部按 `close_time <= 该 4h 收盘` 取 1h ⇒ 最后一根 4h 收盘之后才开盘的那根 1h **从未被喂进状态机**；原实现却把 `last_1h_open` 记成"装载到的最后一根"（`b1[-1]["open_time"]`）⇒ `step()` 判 `n1 > last_1h_open` 恒 False。
- **修复前实测**：隔离集 28 标的 **28 / 28** 各带 1 根尾根、`step_has_new_true = 0 / 28`（引擎整轮空转）；全池 526 标的 **526 / 526** 各带 **1 根**尾根缺口。把这根补处理后 **3 个标的阶段发生变化**：
  - `0GUSDT`：`BOX → SWEEP`（`sweep_time_ms` 由 null 变 `1792846800000`）
  - `DIAUSDT`：`RETEST → IDLE`（事件 `ev_..._DIAUSDT_1792454399999` 完结）
  - `ONEUSDT`：`BOX → IDLE`（事件 `ev_..._ONEUSDT_1792583999999` 完结）
  ⇒ **启动瞬间有 3/526 标的的阶段接口显示的是过期一个 1h 的状态**。
- **修复**：水位改取状态机**实际处理过的最大** 1h `open_time`（未处理过任何 1h 时回退到首根之前以强制 `has_new=True`）。
- **修复后实测**：`tail_gap = 0 / 526`、`step_has_new_true = 526 / 526`、全池 `watermark mismatch = 0`。
- **危害边界（不夸大）**：这是**启动时刻的瞬时延迟**，不是"零信号"的原因；且若下一根 1h 到达，`process_market` 会按 `max(processed)+1h` 自动补齐，不会永久丢失。真正会永久丢失的场景是"补齐前进程停止"。

### D2【真实缺陷｜能力缺口，已如实声明】"状态恢复"这条链在多层断裂

逐层实测：

| 层 | 能力 | 实测结果 |
|---|---|---|
| L1 `EventStateMachine` | `export_state` / `restore_state` | **往返逐字段相同**（`roundtrip_identical=true`）；`config`/`pit`/`last_clock` 不在导出里（`pit` 由 `pit_*` 键重建，`last_clock` 真丢失） |
| L2 `RealSignalEngine` | `export_state` / `restore_state` | 能恢复 `sm` + 两个 processed 集合；**丢 3 个属性**：`history_floor_ms`、`signals_generated`、`last_cycle_ms`（调用方必须手工补，否则下次 `process_market` 从 0 扫全表） |
| L3 `MultiSymbolSignalEngine` | `export_state` | **是桩**：只导出 `symbols/ready/bootstrap`，**不含任何逐标的状态机状态**；类上**没有 `restore_state`** |
| L4 `UnifiedRuntime._recover_state_on_startup` | 启动恢复 | 只数 `paper_position` / `paper_pending` 行数，**一次都不碰信号引擎** |
| L2b 行为等价性 | 中途换引擎 vs 不换 | **28/28 标的 0 差异**（单标机制是忠实的） |

- **为什么不补多标快照恢复**：`EventStateMachine.export_state()` 含**不裁剪**的 6 个 1h 数组（每标的约 1.2k 根），全池 526 标的序列化量级 ~10² MB，写读成本远高于重放（526 标的预热重放 **21.4 s**），且重放与"不重启"的状态等价性已由三路验证实测成立。⇒ **重放是这里的正确恢复方式。**
- **已做的修复**：`MultiSymbolSignalEngine.export_state()` 现在**如实声明** `state_persisted=False` / `restore_supported=False` / `recovery_method="bootstrap_full_replay"`，杜绝"名字叫 export_state 就以为存了状态"的误解。
- **危害边界**：状态本身由重放重建，**不是零信号的原因**。真正的副作用是计数语义：重启后 `since_start` / `candidates_since_start` 归零（这是语义正确的），而 `funnel.history` 会被重放立刻填回（`BOX 3311 / … / CANDIDATE 4`）⇒ 用户不会只剩一个裸"0"。

### D3【潜在风险｜本窗口未触发】bootstrap 硬门槛静默剔除标的

`1h >= 200` 且 `4h >= box_bars_4h+8 = 50`，达不到即整币跳过。**当前窗口 `ready=526 / skipped=0`，未触发**。风险在于：新上市标的会被静默跳过，且 `skipped_reasons` 只进 `bootstrap_stats`，不进 `state_counter`（`get_full_status` 已暴露 `skipped_reasons` 字段，但前端是否呈现本单未验）。

### D4【真实缺陷｜已修（展示层）】坏死行情被当成"没机会"

- `1000000BOBUSDT / PROMPTUSDT / PUMPBTCUSDT` 的 4h 从 `2026-10-05T08:00Z` 起 **114 根全零**（`volume=0`、`high==low==close`）⇒ `compute_wilder_atr_series` 返回 `0.0` ⇒ `if np.isfinite(value) and value > 0` 不成立 ⇒ `check_and_form_box` 静默不执行。
- 展示层原本把 3 个币混进 `IDLE`，看不出是**数据坏死**。
- **修复**：新增 `MultiSymbolSignalEngine.idle_breakdown()`（只读诊断，60 s 缓存），把 `IDLE` 拆成 `IDLE_COOLING / IDLE_NO_4H_DATA / IDLE_DEAD_BARS / IDLE_ATR_INVALID / IDLE_WAIT_BOX / IDLE_READY_BOX`，并挂到 `get_full_status().signal_engine.idle_breakdown`。
- **修复后实测（全池）**：`IDLE_DEAD_BARS=3, IDLE_COOLING=106, IDLE_WAIT_BOX=21`，合计 **130**，与修复前脚本自算的 `3+106+21=130` **逐项吻合**。

### D5【已核查并排除】4h 建档 vs 1h 处理 的先后顺序

- 实时：`process_market` 把事件按 `(close_time, kind)` 排序，`kind=0`（4h）先于 `kind=1`（1h）。
- 回测：`runner.py:409/432` 先 `check_and_form_box(t_close)`，再 `process_1h_bar(t_open=t_close-1h)`。
- ⇒ **顺序一致，不是缺陷。** 回测与实时的差异**只**来自 ATR 口径（见 §3）。

### D6【已核查并排除】`available_at` 与"下一根 15m 开盘"

- 候选：`snapshot_time_ms = available_at_ms = close_time_ms`，`execution_earliest_ms = snapshot_time_ms + 15m`。
- 纸面成交：`order_request_ms = max(event_time, available_at)`；`earliest_fill_ms = order_request_ms + 15m`；成交 bar = 第一根 `open_time >= earliest_fill_ms` 的已闭合 15m bar。
- ⇒ 两条路径都等于"1h 收盘 + 15m"，**口径一致，不是缺陷**。`available_at` 只承担"决策可得时刻"语义，不承担执行时点语义。

### 修复后回归（必须证明没改坏策略结论）

`code/06_after_fix_check.py` 在**修复后的代码**上对**全池 526 标的**重跑预热回放：

| 项 | 修复前 | 修复后 | 判定 |
|---|---|---|---|
| 迁移总数（10 类） | BOX 3311 / SWEEP 675 / RETEST 408 / CANDIDATE 4 / INVALIDATED 1662 / EXPIRED 1240 / MISSED 9 / 其余 0 | **逐项相同** | 无回归 |
| 历史候选 | 4 个（BBUSDT/SUSDT/VELVETUSDT/WALUSDT） | **4 个，逐项相同** | 无回归 |
| 当前阶段 | BOX 370 / IDLE 130 / RETEST 18 / SWEEP 8 | **逐项相同** | 无回归 |
| 水位不一致标的数 | 526 | **0** | 修复生效 |
| 三路衔接 | ALL_PASS=True | **ALL_PASS=True** | 无回归 |

`VERDICT_ALL_PASS = True`。

---

## 7. 执行链路核对：信号 → 订单 → 成交（只读）

脚本 `code/07_ledger_audit.py` → `out/07_执行链路核对.json`、`out/07_账本逐行.csv`。账本 `数据/paper/paper_3a.sqlite3`（**本单未写入任何行**）。

| 表 | 行数 | 可追溯到**真实候选**（`cand_ev_...`） | 可追溯到真实事件（`ev_...`） | 其它/测试 |
|---|---|---|---|---|
| `live_signal` | **0** | 0 | 0 | 0 |
| `paper_pending` | 3 | **0** | 0 | 3 |
| `paper_order` | 13 | **0** | 0 | 13 |
| `paper_position` | 3 | **0** | 0 | 3 |
| `paper_fill` | 6 | **0** | 0 | 6 |
| `paper_realized` | 3 | 0 | 0 | （无 uid 列） |
| `paper_blocked` | 12 | **0** | 0 | 12 |

- `paper_pending`：3 行全部 `status=rejected`、`reason=new_entry_blocked_pending_closure_review`
- `paper_order`：`filled 3 / rejected 10`（10 条拒绝含闸门 6、配置契约 4、`before_paper_epoch_no_backfill` 1）
- `paper_position`：3 行全部 `closed`，`exit_reason = manual_closeloop_test ×2 + ui_closeloop_verification ×1`
- `paper_fill`：6 条逐次成交事件（`action: open 3 / close 3`）

**结论：账本内没有任何一行由引擎真实候选驱动。** 全部持仓与成交都是测试驱动。
**身份链缺口**：`paper_order` / `paper_blocked` 只有 `event_uid`（事件级），**没有 `signal_uid`（候选级）** ⇒ 即使将来有真实候选，订单表也无法按候选逐条对账；逐次成交事件只在 `paper_fill` 里，**订单级记录不能冒充逐次成交事件**。

---

## 8. 用户四项重点：完成度

| # | 重点 | 判定 | 证据与缺口 |
|---|---|---|---|
| ① | **信号 → 订单 → 回测复盘** | **未完成** | 账本 **0 行**由真实候选驱动（§7）；`live_signal` **0 行**；3 个持仓全是测试驱动。回放的 4 个历史候选按纪律不下单（预期行为，`history_candidates_discarded=4` 已留痕）。要判"完成"，必须有一次真实候选 → 订单 → 成交 → 复盘的可追溯记录，现在没有。 |
| ② | **回测 K 线可留存重开** | **部分完成（沿用前序验收，本单未新增证据）** | 图表尺寸与上下文已在前序单验过（`BT-KLINE-CENTER-01`：右空 45%→0.9%；`BT-KLINE-CONTEXT-01`：1h 7 根→343 根，首屏铺满 343/343）。"留存后重开"需要一份带 run 身份的重开回执，**本单未做**，不写成已通过。 |
| ③ | **默认 1h、多图联动、订单与图表双向定位** | **部分完成** | 默认 1h 与多图联动沿用前序验收；"订单 → 图表"反向定位本单**未验证**，且账本里没有真实候选驱动的订单可用来验。 |
| ④ | **逐笔订单、成交记录和可对账导出** | **部分完成** | `paper_fill` 有 6 条逐次成交事件（open 3 / close 3）可对账；但 `paper_order` 只有 `event_uid` 无 `signal_uid`（§7），**订单无法按候选逐条对账**；"可对账导出"能力本单未验。 |

本单新增的回放结果**可追溯到对应 K 线**：`out/05_候选逐项对照.csv` 给出候选时刻/方向/突破收盘/初始止损/箱体三要素，`out/05_状态逐币对照.csv` 给出每条路径每个标的的 `event_id / sweep_time_ms / retest_time_ms / last_processed_1h`，可回查对应 1h/4h bar。**没有成交就明确记录原因**（§7）。

---

## 9. 边界与未做

- **未启用真实交易**；未调参、未放宽任何门槛、未重跑 45 组动态实验。
- **未下载任何行情**（`downloaded_anything=false`）；180 天缺口如实标注，未缩短请求窗口冒充覆盖。
- **未改冻结策略口径**：五个策略文件 SHA 逐项核对未变（§开头）。
- **未覆盖旧证据**：本单所有写入都在 `evidence/SIGNAL-HISTORY-FIX-01/`；行情库只读；纸面账本只读。
- **未做**：多标状态快照恢复（理由见 D2）；`atr_warmup_bars` 口径统一（须走裁决流程，见 §3）；`paper_order` 补 `signal_uid`（属账本 schema 变更，须单独立项）。
- **修复的生效状态**：代码已落盘并通过全池回归；**生产进程 `pid 59109`（启动于 `2026-10-24T17:49:02`）仍在跑修复前的内存镜像**，需重启才生效。重启属运维动作，本单未擅自执行。

---

## 10. 复算方式（每个数字都能机械复现）

```bash
cd evidence/SIGNAL-HISTORY-FIX-01/code
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

$PY 01_coverage.py        # -> out/01_覆盖审计.json, out/01_逐币覆盖.csv
$PY 02_atr_warmup.py      # -> out/02_ATR预热对照.json, out/02_ATR逐币差异.csv
$PY 03_funnel_why.py      # -> out/03_漏斗汇总.json, out/03_逐币漏斗与原因.csv
$PY 04_defects.py --tag before   # -> out/04_缺陷复现_before.json
$PY 05_linkage.py         # -> out/05_三路衔接对比.json + 两张逐项 CSV
#   ↑ 以上为「修复前」证据（04/05 在修复后重跑，结论不变，见 06）
$PY 06_after_fix_check.py # -> out/06_修复后全池复核.json（回归 + 新能力）
$PY 07_ledger_audit.py    # -> out/07_执行链路核对.json, out/07_账本逐行.csv
$PY 08_manifest.py        # -> out/08_产物清单与上游快照.json
$PY 09_verify_report.py   # 逐条断言：报告里的关键数字必须等于 out/*.json 里的原值
```

> 注：`04_defects.py --tag after` 需在修复后运行；`05_linkage.py` 在修复前后各跑一次，结论均为 `ALL_PASS=True`（修复没有改变结论，只补上了启动时刻的 1 根延迟）。
