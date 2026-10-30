# BACKTEST-REPAIR-01 · 施工报告

**工单**：Wyckoff V3.1 历史回测基础修复（任务 A 数据路径 / 任务 B FT-LOW4H 接线 / 任务 C 绩效补全）
**优先级**：P0　**执行方**：WorkBuddy　**性质**：隔离施工 + 自动测试 + 证据交付
**结论**：**施工 PASS**（三个任务的目标都达成、有可复现证据、门禁未绕过、PR2 未受影响）
**但**：**回测结果本身仍不是正式结论** —— 历史 OI 无真实发布时刻证明，`formal_pass_allowed=False` 这一条没有被改动，也不该被改动。

---

## 0. 一句话结论

三个任务都修好了，而且**没有把「候选数变多」当成功**：

- 任务 A：三条历史数据路径（热库 / parquet 归档 / 合约快照）现在**显式可配、缺失必报错**，不再静默回退。29 项测试全过。
- 任务 B：反重力报告说的「按 4h 收盘推进缺 1h 步进」**实测不成立**（1h 覆盖率两边都是 100%）。真正的缺失环节是**持仓量（OI）数据源接错**：FT-LOW4H 分支走的是实时信号引擎，OI 从实时热库取，而历史 OI 的抓取时刻全为空 ⇒ 每个决策点都判「当时读不到 OI」⇒ 突破永不成立 ⇒ 候选恒 0。传进来的 parquet OI 只进了指纹，**从没喂给状态机**。已改为回测自有状态机 + parquet OI，9 项接线测试全过。
- 任务 C：绩效字段从 13 项补到 36 项，工单要求的 10 项全部落位；**旧 13 项数值逐字节不变**（同一标的同一窗口同一候选集实测对照）。

---

## 1. 工作树路径 / 分支 / HEAD

| 项 | 值 |
|---|---|
| 工作树 | `/private/tmp/backtest-repair-01` |
| 分支 | `backtest-repair-01` |
| HEAD | `9c8929d50a60ffa897496d028bff11649bfdbcfd`（`ci: add architecture guardrails and portable agent paths`） |
| 提交数 | **0**（未 commit、未 push、未 merge、未部署） |
| 暂存区 | 空 |
| 检出方式 | `git worktree add --no-checkout` + 稀疏检出（排除 `evidence/`、`回测日志/`，仅保留 `evidence/BACKTEST-REPAIR-01/` 与冻结规则 md）；占用约 101 MB |

**为什么用稀疏检出**：磁盘只剩 2.1 G，完整检出需 1.95 G。冻结规则 md 已单独纳入并核对 SHA256 = `1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab`，与 `config.py` 声明一致。

证据：`out/04_范围与哈希核验.txt`

---

## 2. 修改文件清单

受跟踪文件只动了 **5 个**，全部落在工单允许范围内（任务 C 原文点名了 `engine_adapter.py` 与 `backtest_runner.py`）：

| 文件 | 任务 | 改动 |
|---|---|---|
| `框架/backend/oi_feed.py` | A | `default_hot_db()` 支持 `MARKET_DB`；新增 `resolve_hot_db()` / `probe_hot_db()`；`_connect()` 加存在性前置判断（`FileNotFoundError: hot_db_missing:…`） |
| `策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py` | A | `SpecBook.__init__` 加 `strict` / `missing_reason`；显式指定的目录不存在 ⇒ 抛错；`get_spec_book()` 正确区分「默认路径」与「显式指定」 |
| `策略/WyckoffMaster/event_trend_v0_1/runner.py` | A+B | 去掉 `base_dir` 的本机绝对路径，改由 `WX_PROJECT_ROOT`/`DATA_HUB_PROJECT` 或代码位置上溯推导；`parquet_base` 支持 `MARKET_ARCHIVE`、`runs_base` 支持 `WX_RUNS_BASE`；新增 `data_paths_report()` 预检；`run_state_machine_simulation` 增加可选 `cfg`；**FT-LOW4H 分支改走回测自有状态机 + parquet OI** |
| `策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py` | C | 新增 `compute_summary_metrics()` / `_merge_summary()`，把 23 个新字段**加性**并入 `summary`，不覆盖旧字段 |
| `框架/backend/backtest_runner.py` | C | `_run_event_trend` 的指标映射改读新字段名（`wins`/`losses`/`win_rate_pct`…），保留旧名兼容，去掉「缺失即 0」的写法 |

新增测试（3 个文件，**49 项**）：

- `策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_paths.py`（29 项，任务 A）
- `策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_wiring.py`（9 项，任务 B）
- `策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_metrics.py`（11 项，任务 C）

**未动**：`paper_engine.py`、`live_api.py`、`live_store.py`、`live_worker.py`、`unified_runtime.py`、`state_machine.py`、`pit_bridge.py`、`config.py`、`features.py`、`contracts.py`、`trading_core/*`、`UI界面/`（534 个受跟踪文件全部一致）、数据库与结构、真实账本、策略参数、风险阈值、运行锁、`project_state.json`（HEAD 中本就不存在）、Git 门禁。

---

## 3. 修复前失败复现（分两层归因）

### 3.1 第一层：数据路径（任务 A）

在隔离工作树里直接跑原代码，OI 读取**每个标的每轮都失败**：

```
oi_diag: windows_loaded=726, rows_loaded=0, last_reason="oi_reader_error"
errors: 726 × "OperationalError: unable to open database file"
```

原因：`oi_feed.default_hot_db()` 只有 `Path(__file__).parents[2] / "数据" / …` 一条路，隔离树里 `数据/` 不存在；异常被 `_load_oi_window` 捕获后只记进 `oi_diag`，回测**照常跑完**，产物看起来只是「没有候选」。

证据：`out/00_复现_ft_low4h_驱动.json`

### 3.2 第二层：OI 数据源接线（任务 B）—— 把路径修好之后仍然 0 候选

把 `MARKET_DB` 指向真实热库后再跑原 FT-LOW4H 分支：

```
NEARUSDT 2026-06-01~09-30
  载入 OI 行 = 13185
  未知可得性 = 13185        ← 每一行都没有 fetched_at
  有 OI 的决策 = 0 / 2904
  漏斗：sweeps=4 retests=3 breakouts=0 oi_unavailable=1 expired=10 invalidated=5
  候选 = 0
```

**这就是真正的缺失环节**：`RealSignalEngine._load_oi_window` 把每行可得时刻取成 `fetched_at`，为空即 `NaN`，下游 `_oi_slice_as_of` 全部排除；而该函数**没有** `declared_assumption` 分支（只有 `PitBridge` 有）。所以历史 OI 在实时引擎里永远「当时读不到」。

### 3.3 反重力报告的判断，实测不成立

报告称「按 4h 收盘推进却缺 1h 数据步进导致无法形成候选」。实测（同数据同窗口，只换驱动粒度）：

| 驱动 | 调用次数 | 已处理 1h | 1h 覆盖率 | 候选 |
|---|---|---|---|---|
| 按 4h 收盘推进（现状） | 946 | 2904 / 2904 | **100.0%** | 0 |
| 逐 1h 步进（同一引擎） | 2904 | 2904 / 2904 | **100.0%** | 0 |

两种驱动漏斗逐项相同 ⇒ **1h 步进没有丢**（引擎内部 `watcher.get_closed_bars` 按区间批量取，一次调用就吃满整段）。所以「步进粒度」不是卡点，**OI 数据源才是**。

证据：`out/00_复现_ft_low4h_驱动.json`（`A_vs_B_same`）、`out/02_任务B_修复前后对照.json`

---

## 4. 修复后命令与退出码

所有命令在 `/private/tmp/backtest-repair-01` 下执行，环境变量：

```bash
export MARKET_DB="…/数据/市场数据/db/market_v2.sqlite3"
export MARKET_ARCHIVE="…/数据/市场数据/parquet"
export CONTRACTS_DIR="…/数据/contracts/旧标的池"
export EVENT_TREND_RUNS_DIR=/private/tmp/bt01_runs
export WX_RUNS_BASE=/private/tmp/bt01_runs
```

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `python3 -m unittest …test_backtest_repair_01_paths -v` | 0 | 29 项 OK |
| 2 | `python3 -m unittest …test_backtest_repair_01_wiring -v` | 0 | 9 项 OK |
| 3 | `python3 -m unittest …test_backtest_repair_01_metrics -v` | 0 | 11 项 OK |
| 4 | `python3 -m pytest 策略/…/tests/ -q --basetemp=/private/tmp/pt01_base` | 1 | 166 过 / 4 失败 / 2 错误（**与基线完全同一组**） |
| 5 | `python3 -m unittest discover -s tests`（框架/backend） | 1 | 450 项：47 失败+错误（**与基线完全同一组**） |
| 6 | `python3 …/02_任务B_修复前后对照.py --symbols NEARUSDT,BTCUSDT,ETHUSDT,SOLUSDT` | 0 | 见第 6 节 |
| 7 | `python3 …/03_端到端回测与摘要对照.py --symbol NEARUSDT` | 0 | run `succeeded` |
| 8 | `python3 …/04_范围与哈希核验.py` | 0 | 无越界、关键文件全一致 |

**环境说明（必须记录）**：本机原先没有 `pytest`（既有策略测试全是 pytest 风格），为执行回归已装到用户目录（`~/Library/Python/3.9`，`pytest 8.4.2`，纯开发工具，未改系统环境）。pytest 默认临时目录在沙箱里报 `EEXIST`，故统一加 `--basetemp=/private/tmp/pt01_base`。

---

## 5. 新增测试数与通过数

| 套件 | 项数 | 通过 | 覆盖 |
|---|---|---|---|
| 任务 A 路径 | 29 | **29** | 默认路径兼容（6）、不依赖 cwd（4）、合法外部路径（4，含真实数据）、缺失必报错（7）、非法路径（5）、隔离裸树（3） |
| 任务 B 接线 | 9 | **9** | 1h 全覆盖、4h 成箱只用已收盘 bar（2）、候选时间链因果、**OI 不依赖热库**（把 `MARKET_DB` 指向不存在的文件仍出候选）、PIT 门禁未绕过（2）、关 OI 消融、A/B/C 不退化 |
| 任务 C 绩效 | 11 | **11** | 全盈利、全亏损、零成交、曲线不足 2 点、回撤峰从初始权益起算、成本恒等式、恒等式破坏要报、平盘计入分母、旧字段不被覆盖、runner 映射、脏值不进合计 |
| **合计** | **49** | **49** | |

其中**任务 B 最关键的一条**是 `test_w04_oi_independent_of_hot_db`：把 `MARKET_DB` 指到 `不存在的库/ghost.sqlite3`，修复后**仍然产出候选**。旧实现在同一配置下 100% 失败——这条用例直接证明「OI 不再走实时热库」。

---

## 6. 完整回测结果

### 6.1 修复前后对照（4 标的，2026-06-01 ~ 2026-09-30）

| 标的 | 驱动 | 1h 覆盖 | 突破 | OI 不可用 | 候选 | 因果违规 |
|---|---|---|---|---|---|---|
| NEARUSDT | 修复前 | 2904/2904 | 0 | 1 | **0** | 0 |
| NEARUSDT | 修复后 | 2904/2904 | **1** | **0** | **1** | 0 |
| BTCUSDT | 修复前 | 2904/2904 | 0 | 0 | 0 | 0 |
| BTCUSDT | 修复后 | 2904/2904 | 0 | 0 | 0 | 0 |
| ETHUSDT | 修复前 | 2904/2904 | 0 | 0 | 0 | 0 |
| ETHUSDT | 修复后 | 2904/2904 | 0 | 0 | 0 | 0 |
| SOLUSDT | 修复前 | 2904/2904 | 0 | 0 | 0 | 0 |
| SOLUSDT | 修复后 | 2904/2904 | 0 | 0 | 0 | 0 |

**如实说明**：修复只解开了「OI 读不到」这一个卡点，**没有制造候选**。四个标的里只有 NEARUSDT 在该窗口真的出现了突破事件；BTC/ETH/SOL 修复前后都是 0 候选，因为窗口内本来就没有走完 `BOX→SWEEP→RETEST→BREAKOUT` 的事件——这是策略性质（FT-LOW4H 是反转策略，等的是跌破下沿后的回抽），不是管道问题。

**驱动差异（必须记录）**：BTCUSDT 的成箱数从 17 变成 16（`invalidated` 9→8）。原因是窗口末有一个事件在仿真驱动里到窗口结束仍为 active，在实时引擎里已被判失效。这是**窗口末端截断口径**的差异，不是判定规则差异，不影响候选结论；本单**未深究**，列入剩余风险。

### 6.2 候选时间链（NEARUSDT，因果检查通过）

| 项 | 值 |
|---|---|
| 快照时刻 | 2026-09-04T12:00:00Z（`1788552000000`） |
| OI 可得时刻 | 同一时刻（`declared_assumption`：按桶结束声明） |
| 最早执行时刻 | 2026-09-04T12:15:00Z（`1788552900000`） |
| `available_at ≤ execution_earliest` | 成立 |
| `execution_earliest > snapshot` | 成立（+900 000 ms = 15 分钟） |

### 6.3 官方入口端到端回测

走 `backtest_runner.execute_run(run_id)`（含 FRAME-FIT-01 实验闸门）：

```
run_id = bt_20261029T…   状态 = succeeded   耗时 5.4 s
漏斗：boxes=17 sweeps=5 retests=3 breakouts=1 candidates=1 executed_trades=1
数据质量：status=ok（reconcile 现金/权益恒等式、权益曲线末点、身份链 全部通过）
正式资格：formal_pass_allowed = False
  阻断原因：instrument_spec_unproven
           oi_available_at_unproven:桶起点/桶结束不是已证明的发布时刻
           oi_read_under_assumption:1次按桶结束假设取用
```

**闸门说明**：`execute_run` 要求 run 绑定「已登记且已冻结」的实验，研究模式不会自动冻结。本单**没有绕过闸门**，而是把登记库指到工作树内隔离目录（`FRAME_FIT_REGISTRY`），正规登记一条**研究**实验并冻结，冻结备注写明「仅用于让闸门放行以验证管道可执行；非正式实验资格」。`formal_eligible` 全程 False。

另外：生产生效视图在本工作树缺失（`experiment_registry.default_execution_contract()` 返回 `null`），执行契约改由 `make_ft_low4h_config()` 的冻结参数**显式声明**，来源已在报告与 JSON 中标注，**没有伪造生效视图**。

证据：`out/03_端到端回测.json`、`out/03_端到端回测.txt`

---

## 7. 新旧 summary 字段对照

同一标的（NEARUSDT）、同一窗口、同一候选集（1 候选 → 1 成交），只换代码版本：

| 项 | 基线 | 当前 |
|---|---|---|
| summary 字段数 | 13 | **36** |
| 候选 / 成交 | 1 / 1 | 1 / 1 |
| 旧 13 字段差异 | — | **无（13/13 逐字段完全一致）** |

旧字段逐项对照（全部一致）：`initial_equity` 5000.0、`final_equity` 5036.9729803005375、`net_pnl` 36.97298030053679、`trade_count` 1、`order_count` 2、`fee_total` 0.5159425、`slippage_total` 0.47000000000015696、`slippage_pure_total` 0.3095655000000441、`rounding_total` 0.16043450000011283、`slippage_split_ok` True、`funding_total` 0.346077199463116、`spec_status` spec_applied、`formal_pass_allowed` False。

**新增 23 个字段**，工单要求的 10 项落点：

| 工单要求 | 字段 | 本次实测值 |
|---|---|---|
| 总笔数 | `trade_count` | 1 |
| 胜率 | `win_rate_pct` | 100.0（+ `win_rate_basis` 口径说明） |
| 盈亏笔数 | `wins` / `losses` / `flats` | 1 / 0 / 0 |
| 毛收益 | `gross_pnl` | 37.835 |
| 净收益 | `net_pnl` | 36.973 |
| 手续费 | `fee_total` | 0.5159 |
| 资金费 | `funding_total` | 0.3461 |
| 滑点 | `slippage_total` | 0.4700 |
| 最大回撤 | `max_drawdown_pct`（+`max_drawdown_detail` / `max_drawdown_basis`） | 1.0004 %（峰 5087.87 → 谷 5036.97，11616 点） |
| 平均盈亏 | `avg_win` / `avg_loss` | 36.973 / **None**（无亏损单） |
| 盈亏比 | `avg_win_loss_ratio` | **None**（缺亏损单 ⇒ 无定义） |
| Profit Factor | `profit_factor` | **None**（分母 0 ⇒ 不输出 inf） |
| 期初 / 期末权益 | `period_start_equity` / `period_end_equity` | 5000.0 / 5036.973 |

**零分母处理**：`profit_factor`、`avg_win_loss_ratio` 返回 `None` 而不是 0，并在 `metrics_notes` 写明原因；权益曲线不足 2 点时 `max_drawdown_pct` 也返回 `None`（不是 0%）。

**不重复扣费**：`net_pnl = gross_pnl − fees − funding_fee`，实测 `cost_identity_delta = 7.1e-15`、`cost_identity_ok = True`。**滑点不参与扣减**——它已经体现在成交价里（`gross_pnl` 用的是滑点后价格），再减一次就是重复扣费。这条写进了 `cost_identity_basis` 字段，并有专门测试用例守着。

证据：`out/09_新旧summary字段对照.txt`、`out/03_summary_基线.json`、`out/03_summary_当前.json`

---

## 8. PIT 门禁不变证据

| 门禁 | 修复前 | 修复后 | 判定 |
|---|---|---|---|
| `formal_pass_allowed` | False | **False** | 未绕过 |
| `formal_pass_allowed_components.instrument_spec` | False（`unverified_historical`） | **False** | 未绕过 |
| `formal_pass_allowed_components.pit` | False | **False** | 未绕过 |
| `oi_available_at_unproven` | 在 `qualification_failures` 中 | **在** | 未绕过 |
| `oi_read_under_assumption:{n}次` | 有 | **有（1 次）** | 未绕过 |
| `pit_qualified` | False | **False** | 未绕过 |
| `pit_mode` | `declared_assumption` | **`declared_assumption`** | 未改 |
| `pit.assert_candles_closed` 实际调用 | 有 | **有（`candles_checked` > 0）** | 未绕过 |
| `pit_audit_sha256` | 有 | **有**（写入 `summary.json`） | 保留 |

**没有伪造 OI 发布时刻**：修复后 OI 可得性走 `PitBridge.register_oi(ts, vals)` 的未证明分支 —— `declared_assumption` 模式下按「桶结束」声明假设并置 `assumed=True`，同时记 `oi_available_at_unproven` 让整次运行判不合格。`available_at` 不是编出来的抓取时间，是**明确标注为假设的桶结束**。

测试守住这条：`test_w05_pit_gate_not_bypassed`、`test_w05b_formal_pass_blocked`。

---

## 9. PR2 四文件（及全部关键生产文件）哈希不变证据

`git status --porcelain` 只有 9 条：5 个允许范围内的受跟踪文件 + 本单证据目录 + 3 个新测试文件。其余**全部与 HEAD 逐字节一致**。

逐个核对（工作树 sha256 vs `git show HEAD:<path>` 的 sha256）：

| 文件 | 结果 |
|---|---|
| `框架/backend/paper_engine.py` | 一致 `cbbde84a7f4da986…` |
| `框架/backend/live_api.py` | 一致 `94302c968efe2fe5…` |
| `框架/backend/live_store.py` | 一致 `bcea899ffd7d7988…` |
| `框架/backend/live_worker.py` | 一致 `9ac5600e5c7f433e…` |
| `框架/backend/unified_runtime.py` | 一致 `e30e213538686466…` |
| `策略/…/state_machine.py` | 一致 `4348f1201f8fca76…` |
| `策略/…/pit_bridge.py` | 一致 `5ea657faeafe1f25…` |
| `策略/…/config.py` | 一致 `122683f2b489c742…` |
| `策略/…/features.py` | 一致 `163194b340e56b33…` |
| `策略/…/contracts.py` | 一致 `6895e0949f9b683c…` |
| `策略/…/trading_core/engine.py` | 一致 `ca5be5b82b7032f9…` |
| `策略/…/trading_core/fill.py` | 一致 `f050652c0d83b41a…` |
| `策略/…/trading_core/policy.py` | 一致 `6303d06ae6fea38b…` |
| `框架/backend/data/experiments/events.jsonl` | 一致 `677c5ef771b87558…` |
| `框架/backend/data/experiments/head.json` | 一致 `a32910080866494d…` |
| `UI界面/`（534 个受跟踪文件） | **534/534 全部一致** |

**一处需要主动交代的插曲**：早先有一次探测性运行在设置隔离登记库之前，把两条实验登记记录写进了生产路径 `框架/backend/data/experiments/`（`events.jsonl` 追加 1 行、`head.json` 改 1 行）。发现后立即 `git checkout --` 还原，并纳入第 9 节的哈希核对，现两者均与 HEAD 一致。后续脚本已改为**先设 `FRAME_FIT_REGISTRY` 再登记**，不会再写生产路径。

证据：`out/04_范围与哈希核验.txt`

---

## 10. 剩余问题与风险

### 10.1 数据与结论层面（影响「能不能当结论用」）

1. **样本只有 1 笔**。4 个标的 4 个月窗口里只有 NEARUSDT 出了 1 个候选、1 笔成交。这只能证明「管道通了」，**不能当策略统计结论**，更不能外推胜率/盈亏比。
2. **OI 可得性仍是假设**。历史 OI 没有真实发布时刻，全部按「桶结束」声明。`oi_available_at_unproven` 一直在，`formal_pass_allowed=False`。要让 OI 变成「已证明」，需要交易所级发布时点证据，不是改代码能解决的。
3. **合约规格仍是 `unverified_historical`**。`数据/contracts/旧标的池` 只有 2 份 futures exchangeInfo（2026-10-05）+ 0 份 `spec_changes`，历史窗口一律无法证明适用性。这也是 `formal_pass_allowed=False` 的另一个分量。

### 10.2 工程层面（本次发现但未处理）

4. **两个驱动在窗口末端差 1 个箱**（BTCUSDT 17→16）。原因是末事件在仿真驱动里仍 active、在实时引擎里已判失效。未深究，未改口径。
5. **`run_state_machine_simulation` 的 `cfg` 只在 FT-LOW4H 分支传入**。非 FT-LOW4H 的旧分支仍用 `EventTrendConfig()` 默认配置。这是**故意不动**（避免改旧路径结果），但属于潜在不一致：调用方传了自定义 `cfg` 时，判定用默认、执行用自定义。建议单独立单处理。
6. **绩效口径有两份实现**。`框架/backend/backtest_metrics.py`（读 run 目录现算）与本次新增的 `engine_adapter.compute_summary_metrics`（从内存推导）口径逐字对齐，但**没有合并**。一致性目前靠测试保证，长期看是漂移风险。
7. **`RealSignalEngine` 结构上无法消费历史 OI**。它只有「逐行 `fetched_at`」一条路，没有 `declared_assumption` 分支。本次绕开了它（回测改用自有状态机），但生产侧若将来需要「用历史 OI 回放」，仍会撞同一堵墙。改它属于动生产文件，超出本单范围。
8. **`backtest_metrics._max_dd` 在曲线不足 2 点时返回 `0.0`**（不是 `None`），这是既有的「0 冒充未知」，本次只在新增的 summary 侧做了正确返回，未改 `backtest_metrics`（不在本单范围）。
9. **本机原先没有 pytest**，已装到用户目录。若验收环境不允许装包，第 4 节第 4 行的回归需在具备 pytest 的环境重跑（三套新测试是 `unittest`，不依赖 pytest）。

### 10.3 未做的事

- 未做多标的批量回测（只做了 4 标的单点对照）
- 未改任何策略参数、风险阈值、冻结参数、运行锁、数据库结构
- 未提交、未推送、未合并、未部署、未改 PR 状态

---

## 11. 完成判据核对

| 工单判据 | 状态 | 证据 |
|---|---|---|
| 数据路径修复 | ✅ | 29 项测试；`data_paths_report()` 三路径显式；缺失抛错不静默 |
| FT-LOW4H 接线修复并验证 | ✅ | 9 项测试；NEARUSDT 突破 0→1、候选 0→1；OI 不依赖热库（把 `MARKET_DB` 指到不存在仍出候选） |
| 绩效补全 | ✅ | 11 项测试；13→36 字段；旧字段 13/13 一致；恒等式成立 |
| 完整隔离回测可执行 | ✅ | `execute_run` → `succeeded`，5.4 s，产物齐全 |
| PIT 门禁未绕过 | ✅ | 第 8 节逐项；`formal_pass_allowed=False` |
| PR2 未受影响 | ✅ | 第 9 节；534/534 前端 + 15 个关键生产文件全一致 |
| 不把「候选数 > 0」当唯一成功条件 | ✅ | 判据改为「1h 全覆盖 + 4h 只用已收盘 + 时间链因果 + PIT 门禁在」；4 标的里 3 个仍 0 候选，如实记录 |

---

## 12. 结论

**BACKTEST-REPAIR-01：施工 PASS**

理由：三个任务的目标全部达成且有可复现证据；门禁一条没绕过；PR2 与前端零改动；既有回归与基线**同一组失败**（策略 4 失败 2 错误、后端 47 项，前后逐项相同），没有引入新回归。

**同时必须说清楚**：PASS 指的是「施工完成」，**不是「回测结论可用」**。当前结果仍是探索性研究结果 —— 历史 OI 无发布时刻证明、合约规格无法证明历史适用性，`formal_pass_allowed` 恒为 False。任何把它当正式回测成绩引用的做法都不成立。

**已停手**：未提交、未推送、未合并、未部署。等待独立验收。

---

## 附录：产物清单

```
evidence/BACKTEST-REPAIR-01/
├── README.md                       ← 本文件
├── scripts/
│   ├── 00_复现_ft_low4h_驱动.py          三种驱动对照（复现用）
│   ├── 02_任务B_修复前后对照.py          修复前/后逐标的对照
│   ├── 03_端到端回测与摘要对照.py        官方入口回测 + 摘要快照导出
│   └── 04_范围与哈希核验.py              改动范围与逐文件哈希
└── out/
    ├── 00_复现_ft_low4h_驱动.json        修复前复现（含 726 次读库失败）
    ├── 01_任务A_路径测试.txt              29 项
    ├── 02_任务B_修复前后对照.{json,txt}   4 标的 × 前后
    ├── 03_端到端回测.{json,txt}           官方入口 succeeded
    ├── 03_summary_基线.json / _当前.json  字段对照原始数据
    ├── 04_范围与哈希核验.{json,txt}       范围/哈希
    ├── 05_任务B_接线测试.txt              9 项
    ├── 06_任务C_绩效测试.txt              11 项
    ├── 07_策略全量回归_{基线,当前}.txt     117→166 过，同组 4 失败 2 错误
    ├── 08_后端全量回归_{基线,当前}.txt     450 项，同组 47 失败+错误
    └── 09_新旧summary字段对照.txt         13/13 一致 + 23 项新增
```
