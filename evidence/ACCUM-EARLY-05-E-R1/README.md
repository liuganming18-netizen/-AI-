# ACCUM-EARLY-05-E-R1｜当前实现复验与跨进程恢复独立核验报告

- **执行窗口**：`ACCUM-EARLY-05-E-R1`（独立行为复验与跨进程核验窗口）
- **核验对象**：`框架/backend/accum_early_entry.py`（05-B 当前可审阅版本）vs `evidence/ACCUM-EARLY-05-B/config/fixture_rules_05.json`（05 版测试配置）
- **工程纪律**：只读共享工程，只写本窗隔离核验脚本与证据；工程缺陷移交 05-B 修复，本窗绝不抢改共享代码。
- **免责声明**：所有运行均在专用隔离测试环境（`fixture`）下进行，**仅用于核查工程链路、时序逻辑与跨进程幂等性，不输出任何正式策略绩效**；正式策略参数继续保持待批准状态。**本文所有结论均为执行方自报，仍待 Codex 独立验收**。

---

## 0. 核心结论与状态口径

1. **版本固定与重大缺陷仍存**：
   - 当前 05-B 代码版本已修复了「扫低箱底约束 `s_loc > L`」、「摆动高点 `swing_high_k3_lookback48`」、「压制位空间合格性 `resistance_qualify`」以及「连续收盘跌破箱底结构失效」；
   - **重大缺陷依然存在（当前仍失败）**：05-B 声称已修复扫低根承接判定，但代码深度审计证实 **`_absorption` 依然在确认突破压制位根（`_confirm_actions` 第 1069 行）被调用，而在扫低根（`_sweep_check`）内部调用次数为 0**！承接判断时序颠倒的核心缺陷在当前版本中仍未修复！
2. **原 7 笔输入同输入复验**：
   - 复用 05-E 原 7 笔事件的原始行情与箱体输入；
   - 在当前版本严格的结构失效保护下，7 笔事件在破位时均正规触发「连续 2 根 1h 收盘跌破箱体下沿 ⇒ `INVALIDATED`」并合法终结，不再产生旧版中的虚假提前入场候选；
   - 严格遵循审查纪律：**同一输入如实记录其拦截条件与终态，绝不更换为容易通过的新案例**。
3. **真正的跨进程恢复测试 100% 成立**：
   - 彻底废除旧脚本“同进程重开连接冒充重启”的伪验证；
   - 调度系统独立拉起进程 A（PID 27923）正常开仓退出，系统 `ps` 确认进程 A 死亡；
   - 拉起全新进程 B（PID 27934，独立解释器、零内存共享），读取持久化账本，成功执行 `duplicate_signal_uid` 幂等拦截，订单/成交/持仓零重复。

---

## 1. 固定被验版本与新旧缺陷比对矩阵

数据来源：`evidence/ACCUM-EARLY-05-E-R1/out/01_version_matrix.json`

### 1.1 核心被验文件 SHA-256 哈希

| 文件路径 | 格式版本 | SHA-256 哈希 | 角色定位 |
|---|---|---|---|
| [`框架/backend/accum_early_entry.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/accum_early_entry.py) | `accum-early-05/v1` | `f7056980e0cef0cac7be31ee76fee19357ce33b7704b37add8dc4de83816c8c6` | 05-B 当前实现的共享工程代码 |
| [`evidence/ACCUM-EARLY-05-B/config/fixture_rules_05.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-B/config/fixture_rules_05.json) | `accum-early-05/v1` | `f322a940cea1e69d6ebe048f100b1e0b3b8234c1d42ef45a1f9b25bfd55152bd` | 05-B 发布的 05 版隔离测试配置 |
| [`evidence/ACCUM-EARLY-05-B/config/批准配置_空白表单_05.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-B/config/批准配置_空白表单_05.json) | `accum-early-05/v1` | `b3ac386a37cea7bb02c6a40f5e0037df97ed0d7f4fb623b558ded821bd72b138` | 待批准空白模板（48 项字段全 null） |
| [`框架/backend/paper_engine.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/paper_engine.py) | - | `e8a994b6750d8532576e5120e66144acc63cab355766b8ad8db932de3caec784` | 底层模拟撮合引擎 |
| [`框架/backend/live_store.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live_store.py) | - | `02eaf41b2549ee569548ca41a7200191b42444cac92733131bf667f9b1bf1ca6` | 生产与隔离账本存储层 |

### 1.2 规则行为逐项分类比对矩阵

| 规则维度 | 旧版 04-B 状态 | 当前 05-B 状态 | 类别判定 | 实际代码取证与事实说明 |
|---|---|---|---|---|
| **扫低约束 `s_loc > L`** | 缺失约束，跌破箱底仍误判扫低 | 已实现 `must_be_above_box_low` | **当前已修** | `_sweep_check` 行 779-786：若 `ref <= low_l` 则返回 False，阻断箱底破位误判。 |
| **局部压制位摆动高点** | 仅 `prior_n_high`，无摆动高点 | 实现 `swing_high_k3_lookback48` | **当前已修** | `_resistance` 行 719-737：从近到远寻找左右各 k 根不高于当根的摆动高点。 |
| **压制位空间合格性** | 缺失校验，无 `NO_RESISTANCE` | 实现 `_resistance_qualified` | **当前已修** | 强制要求 `L + 0.5A <= R <= H - 0.5A`，空间不足正规终结为 `NO_RESISTANCE`。 |
| **结构破位失效保护** | 缺失保护，跌破箱底后死守 | 实现 `_structural_invalid` | **当前已修** | 连续 2 根收盘跌破箱体下沿 L 或盘中跌破 L-0.5A 时，正规终结为 `INVALIDATED`。 |
| **经济口径一致性声明** | 静默放行口径冲突 | 声明与引擎不符时硬阻断 | **当前已修** | `_economic_caliber` 校验声明值与引擎生效值，不一致报 `economic_caliber_declared_mismatch`。 |
| **承接与卖压判定时序** | 颠倒在突破确认根判定 | **依然在确认根判定！** | **当前仍失败** | **严重语义缺陷**：`_absorption` 仍被放在 `_confirm_actions`（行 1069）调用，扫低根完全未执行！ |
| **未批准配置冷却期** | 冷却期退化为 0 | 依然硬编码为 0 | **当前仍失败** | `fixture_rules_05.json` 行 65 显式设为 0，违背安全回退默认值原则（应保持 24 小时）。 |
| **真实历史 available_at** | 早期曾误当已证明 | 诚实标记 `unknown/not_proven` | **保持合规** | 冷档历史数据无抓取挂钟证据，合规声明「计算不超前，但未证明当时可得」。 |
| **自然行情与正式策略绩效** | 未验证 | 未验证（无真实批准参数） | **未覆盖/待决** | 所有测试均为 fixture 链路自检，严禁作为正式策略绩效或用户批准依据。 |

---

## 2. 对原输入复验规则行为

数据来源：`evidence/ACCUM-EARLY-05-E-R1/out/02_original_events_retest.json` 及 [`cases/`](cases/)

严格复用 05-E 原 7 笔候选事件的原始行情输入（SOLUSDT 冷档 2026-08-01 ~ 2026-09-05，AVAXUSDT 热库 2026-09-12 ~ 2026-10-25），使用当前 05-B 代码与 `fixture_rules_05.json` 进行全链回放对撞。

### 2.1 原 7 笔事件在新版下的终态对比表

| 案例编号 | 标的 | 方案 | 04-B 旧版终态与确认时间 | 05-B 当前版终态 | 是否产生候选 | 当前版拦截根因与触发时间 (UTC) |
|---|---|---|---|---|---|---|
| [**案例 1**](cases/案例_1_SOLUSDT_4a.md) | SOLUSDT | 4a | `WAIT_ENTRY` (2026-08-02 02:59) | `INVALIDATED` | **否** | `2026-08-01T18:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 72.26 ⇒ 失效 |
| [**案例 2**](cases/案例_2_SOLUSDT_4b.md) | SOLUSDT | 4b | `WAIT_ENTRY` (2026-08-02 03:59) | `INVALIDATED` | **否** | `2026-08-01T18:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 72.26 ⇒ 失效 |
| [**案例 3**](cases/案例_3_SOLUSDT_4c.md) | SOLUSDT | 4c | `WAIT_ENTRY` (2026-08-02 03:59) | `INVALIDATED` | **否** | `2026-08-01T18:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 72.26 ⇒ 失效 |
| [**案例 4**](cases/案例_4_AVAXUSDT_4a.md) | AVAXUSDT | 4a | `WAIT_ENTRY` (2026-09-14 11:59) | `INVALIDATED` | **否** | `2026-09-16T00:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 7.27 ⇒ 失效 |
| [**案例 5**](cases/案例_5_AVAXUSDT_4b.md) | AVAXUSDT | 4b | `WAIT_ENTRY` (2026-09-14 12:59) | `INVALIDATED` | **否** | `2026-09-16T00:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 7.27 ⇒ 失效 |
| [**案例 6**](cases/案例_6_AVAXUSDT_4c.md) | AVAXUSDT | 4c | `WAIT_ENTRY` (2026-09-14 13:59) | `INVALIDATED` | **否** | `2026-09-16T00:59:59Z` 连续 2 根 1h 收盘跌破箱体下沿 7.27 ⇒ 失效 |
| [**案例 7**](cases/案例_7_AVAXUSDT_4a.md) | AVAXUSDT | 4a | `WAIT_ENTRY` (2026-09-16 20:59) | `NOT_FOUND` | **否** | 前序事件失效与冷却约束，该次箱体未建档 |

### 2.2 行为复验核心事实发现

1. **结构破位保护斩断了历史虚假候选**：
   - 旧版 04-B 在 SOLUSDT 行情已于 2026-08-01T18:59 连续跌破箱底的情况下，因缺失全局结构失效，硬撑至 2026-08-02 箱底下方反弹虚构候选；
   - 05-B 当前实现的 `_structural_invalid` 严格生效，在破位发生时正规终结为 `INVALIDATED`；
2. **遵守审查纪律，不更换案例**：
   - 同一输入修复后不再产生候选，报告如实留存其终态轨迹，**不找容易通过的新案例替代**。

---

## 3. 承接门槛开启/关闭与名义额指标深度审计

数据来源：`evidence/ACCUM-EARLY-05-E-R1/out/03_absorption_cases.json`

针对“承接门槛关闭的测试不能证明承接判断正确”的要求，本窗使用显式测试配置，构建了开启门槛时的 1 个标准正例与 4 类反例。

### 3.1 承接指标正反例实测结果

| 序号 | 测试用例场景 | 配置门槛设定 | 输入观测值与基准 | 判定结果 | 验证结论 |
|---|---|---|---|---|---|
| **0** | 门槛关闭对照 | `require: false` | 任意数据 | `pass` (skipped: not_required) | 关闭时安全跳过，不阻断 |
| **1** | **标准正例** | `require: true`, `buy_mult: 1.0`, `sell_mult: 0.8`, `upper: true` | 主动买占比 0.6 >= 基准 0.5；卖额 4000 <= 上限 4000；收盘 8.0 位于上半区 [7.5, 10] | `pass` (ok=True) | **全条件达标，承接判定通过** |
| **2** | **反例 A：主动买不足** | 同上 | 主动买占比 0.4 < 基准 0.5；卖额 4000；收盘 8.0 | `fail` (ok=False) | `absorption_buy_ratio` 拦截 |
| **3** | **反例 B：卖压额超标** | 同上 | 主动买占比 0.6 >= 0.5；**卖额 8000 > 上限 4000**；收盘 8.0 | `fail` (ok=False) | `sell_pressure_amount` 拦截 |
| **4** | **反例 C：下半区收盘** | 同上 | 主动买占比 0.6；卖额 4000；**收盘 6.0 < 7.5（下半区）** | `fail` (ok=False) | `absorption_close_upper_half` 拦截 |
| **5** | **反例 D：历史不足** | 同上 | 历史 K 线仅 5 根（不足 20 根） | `fail` (ok=None) | `baseline_window_short` 标记证据不足 |

### 3.2 致命缺陷定性：单体计算正确，但全链调用时序颠倒

- **正确项**：`_absorption` 内部已正确采用 USDT 名义额比例（`taker_buy_quote_volume / quote_volume`）对比前 20 根中位数，并核验主动卖出额（`quote_volume - taker_buy_quote_volume`）；
- **当前仍失败项**：在状态机推进中，`_sweep_check`（扫低根）完全没有调用 `_absorption`；反而延后到了 `_confirm_actions`（收上压制位突破根，行 1069）！
- **后果**：将恐慌扫低时的主力吸收卖盘，偷换成了向上突破时的买入量，策略因果关系依然颠倒。**此项列为 05-B 剩余阻赛季修缺陷**。

---

## 4. 真正的跨进程恢复测试证据

数据来源：`evidence/ACCUM-EARLY-05-E-R1/out/04_cross_process_evidence.json`

本窗彻底排除了旧脚本在同一进程内重开数据库连接的缺陷，通过操作系统子进程调度，完成了进程间**零内存共享**的跨进程恢复核验。

### 4.1 双独立进程执行日志与系统存活核查

```
[PROCESS A]
- 执行脚本: evidence/ACCUM-EARLY-05-E-R1/scripts/process_a_worker.py
- 系统 PID: 27923
- 启动时刻: 2026-10-26T15:57:02Z
- 退出时刻: 2026-10-26T15:57:03Z (耗时 0.4608s)
- 退出码: 0
- 首次开仓: ok=True, 产生订单 1 笔, 成交 1 笔, 持仓 1 笔

[系统核对]
- 调度器执行 `ps -p 27923`
- 结果: 进程已彻底退出，系统确认进程 A 死亡 (confirmed_dead=True)

[PROCESS B]
- 执行脚本: evidence/ACCUM-EARLY-05-E-R1/scripts/process_b_worker.py
- 系统 PID: 27934 (PID 绝对不同: 27923 != 27934)
- 启动时刻: 2026-10-26T15:57:03Z
- 退出时刻: 2026-10-26T15:57:03Z (耗时 0.0219s)
- 退出码: 0
- 再次开仓: ok=False, 拦截原因: duplicate_signal_uid
```

### 4.2 账本前后快照对比表（跨进程幂等性核验）

| 账本快照项 | 进程 A 退出后磁盘状态 | 进程 B 恢复提交后磁盘状态 | 差异与增加 | 跨进程核验判定 |
|---|---|---|---|---|
| **`paper_order` 订单数** | 1 | 1 | +0 笔 | **完全幂等** |
| **`paper_fill` 成交数** | 1 | 1 | +0 笔 | **完全幂等** |
| **`paper_position` 持仓数** | 1 (`open`, qty=6.84, px=72.99) | 1 (`open`, qty=6.84, px=72.99) | +0 笔 | **持仓不重复** |
| **拒绝原因** | - | `duplicate_signal_uid` | 一致 | **原因明确** |

> **同进程 vs 跨进程对比**：调度器同步执行了同进程内重复调用对照，同进程二次调用同样被 `duplicate_signal_uid` 拦截。两者分别独立报告，证明无论是进程内存态还是磁盘持久化恢复态，幂等机制均 100% 成立。

---

## 5. 批准闸门与物理隔离

数据来源：`evidence/ACCUM-EARLY-05-E-R1/out/05_gate_and_isolation.json`

1. **无批准配置硬阻断**：
   - 传入 48 项全空的空白配置表单时，`load_config` 检出 `missing=48`，`open_allowed=False`；
   - 调用 `try_open` 返回 `ok=False`，`called_paper_engine=False`，账本零写入；
   - 与此同时，`DataFeed` 读取数据与箱体建档观察正常进行，产出候选为 0，证实“读数据和记录观察可以继续，但不得调用实际开仓”；
2. **正常经过配置校验**：
   - 本窗所有开仓测试均通过 `kind="fixture"` 且环境变量 `ACCUM05_FIXTURE_OK="1"` 正常走通 `load_config` 内部校验链路，**绝不直接强制 `cfg["open_allowed"]=True` 虚构通过**；
3. **生产物理隔离与越界硬阻断**：
   - 生产目录 `数据/paper` 中无任何研究臂 `4a/4b/4c` 账本文件（清点结果 0 笔）；
   - 越界负例：将环境变量指向生产目录时，`try_open` 触发 `isolation_violation` 拦截，`called_paper_engine=False`，在写入生产账本之前 100% 阻断；
4. **历史 `available_at` 缺失诚实标记**：
   - 冷档历史 K 线无交易所采集挂钟时间，合规定性为 `unknown / not_proven`，不把历史已知等同于当时可得。

---

## 6. 产物与证据索引

| 产物路径 | 类型 | 核心内容说明 |
|---|---|---|
| [`evidence/ACCUM-EARLY-05-E-R1/out/01_version_matrix.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/out/01_version_matrix.json) | 机器数据 | 版本哈希与新旧缺陷逐项比对矩阵 |
| [`evidence/ACCUM-EARLY-05-E-R1/out/02_original_events_retest.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/out/02_original_events_retest.json) | 机器数据 | 原 7 笔事件同一行情输入回放全链轨迹与终态 |
| [`evidence/ACCUM-EARLY-05-E-R1/out/03_absorption_cases.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/out/03_absorption_cases.json) | 机器数据 | 承接门槛开启/关闭正反例与调用时序审计 |
| [`evidence/ACCUM-EARLY-05-E-R1/out/04_cross_process_evidence.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/out/04_cross_process_evidence.json) | 机器数据 | 双独立系统进程日志、PID、前后账本快照 |
| [`evidence/ACCUM-EARLY-05-E-R1/out/05_gate_and_isolation.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/out/05_gate_and_isolation.json) | 机器数据 | 批准闸门、越界硬阻断与数据可得性核验 |
| [`evidence/ACCUM-EARLY-05-E-R1/cases/`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/cases/) | 案例报告 | 原 7 笔事件逐时点 Markdown 终态分析 |
| [`evidence/ACCUM-EARLY-05-E-R1/scripts/`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-05-E-R1/scripts/) | 自动化脚本 | 全部 5 个独立验证脚本与进程 A/B 工作脚本 |

---

## 7. 移交 05-B 剩余缺陷清单

1. **【严重时序错位】承接判定移回扫低根**：
   - 将 `_confirm_actions` 行 1069 的 `_absorption` 调用彻底移除；
   - 移至 `_sweep_check` 成立之后（或在 `on_bar` 的 `state == "BOX"` 扫低收回瞬间），作为进入 `SWEEP` 的硬门槛；若承接不达标，不迁移至 `SWEEP`；
2. **【配置规范】测试配置冷却期回退至默认 24 小时**：
   - `fixture_rules_05.json` 将 `repeat_candidate_limit.cooldown_hours` 恢复为 24，避免未批准扫描测试时冗余膨胀。
