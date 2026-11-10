# 06-B 回测程序准备｜复用与变更清单

- **工单编号**：06-B-BACKTEST-PREP-01
- **项目阶段**：06 回测与样本外 / 06-B 回测程序准备
- **任务定位**：为冻结候选提供可调用的回测执行能力；非最终样本外评估
- **执行角色**：反重力（独立测试与回测程序准备）｜验收方：总控 Codex
- **治理依据提交**：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`（治理版本，继承其 README.md、AGENTS.md 及《策略研究与冻结验收规范》）
- **业务代码基线**：`8bef22b0`（实际最新有效版本，继承所有已验收成果）
- **任务隔离分支**：`task/06-b-backtest-runner-prep`

---

## 一、已有有效成果与复用清单（直接继承，不重复验收）

| 模块 / 环节 | 代码路径与引用文件 | 已有验收证据 / 工单 | 复用机制与继承范围 |
| :--- | :--- | :--- | :--- |
| **撮合驱动与风控核心** | `策略/WyckoffMaster/trading_core/`<br>- `engine.py`<br>- `fill.py`<br>- `funding.py`<br>- `ledger.py`<br>- `policy.py`<br>- `position.py`<br>- `risk.py` | `WORK-02`、`WORK-04`<br>`docs/evidence/WORK-04_strategy_isolation_and_backtest_convergence_2026-10-16.md` | 100% 直接复用：相位 A/B/C/D 保守时序、止损 95% 上移、取整与滑点算术、`assert_live_disabled()` 硬闸门。回测与离线模拟共享该唯一核心。 |
| **资金费双口径与费用阻断** | `框架/backend/backtest_runner.py`<br>`策略/WyckoffMaster/trading_core/funding.py` | `GM-FUNDING-RUNNER-02`<br>`GM-FUNDING-RUNNER-02-R1`<br>（提交 `809decfc`、`5d1e6a89`） | 100% 直接复用：区分已确认资金费（`total_funding_fee_confirmed`）与假设资金费（`total_funding_fee_hypothetical`）；未确认费用严禁进现金与净收益；负数假设返还与抵消事件不漏检。 |
| **数据门禁与时限校验** | `框架/backend/backtest_data_gate.py`<br>`框架/backend/backtest_gate_wiring.py` | `GM-BACKTEST-DATA-GATE-17`<br>`GM-BACKTEST-DATA-GATE-FIX-27`<br>`GM-BACKTEST-OHLC-SEVERE-GATE-31`<br>`GM-BACKTEST-4H-GATE-ENTRYPOINT-FIX-45` | 100% 直接复用：OHLC 几何异常判定、15m/1h 网格严格对齐、连续性全窗覆盖、不可变指纹生成。 |
| **回测溯源与背书防篡改** | `框架/backend/backtest_run_provenance.py` | `GM-BACKTEST-TRUSTED-RUN-PROVENANCE-FIX-46` | 100% 直接复用：运行身份哈希链、指标与配置篡改硬拦截、CLAIM_TRUSTED 降级。 |
| **实验登记与留出泄露门禁** | `框架/backend/experiment_registry.py` | `FRAME-FIT-01`、`FRAME-FIT-02` | 100% 直接复用：样本切分重叠检测、标签越界检测、未冻结实验阻断、留出样本受保护封闭。 |
| **只读行情资产** | `数据/市场数据/db/market_v2.sqlite3`<br>`数据/市场数据/parquet/` | `WB-DATA-AUDIT-01`、`Q02`、`Q03` | 100% 只读继承：历史封存数据保持只读，不触碰、不重跑全量数据审计。 |

---

## 二、本轮实际缺项与修改路径清单

对照用户要求与原验收结果，本轮精确定位并补齐的实际缺项如下：

| 缺项编号 | 发现的具体缺项 | 修改 / 新增文件确切路径 | 补齐内容说明 |
| :--- | :--- | :--- | :--- |
| **GAP-01** | **冻结候选可调用的标准化回测执行入口缺失**：旧回测入口强依赖特定实验登记库或私有 `_run`，缺乏供 04 阶段策略冻结候选直接调用的标准化函数。 | `框架/backend/candidate_backtest_runner.py`（**新增**）<br>`框架/backend/backtest_runner.py`（**修改**，导出入口） | 提供 `run_candidate_backtest(...)` 标准执行入口，支持传入策略对象/标识、规格、固定快照、PIT 模式、盲测保护及自定义隔离输出目录。 |
| **GAP-02** | **固定开发快照绑定与只读隔离机制强化**：动态 SQLite 持续增长不可作为固定快照；需支持内存快照、文件快照及执行时原子绑定不可变 `inputs_manifest.json`。 | `框架/backend/candidate_backtest_runner.py`（**新增**） | 实现 `create_or_bind_snapshot`：支持内存快照字典直接喂入（100% 隔离运行不碰数据库），支持目录快照，或从只读库提取并原子落盘不可变 `inputs/` 快照与 SHA256 清单。 |
| **GAP-03** | **严格历史时钟与 PIT 模式分级**：未知历史可得时间需显式区分严格（strict）与近似（approximate）模式，未收盘和未来数据强制剔除。 | `框架/backend/candidate_backtest_runner.py`（**新增**） | 强制 `is_closed=1` 且时间戳在窗口内；`pit_mode="strict"` 时缺失 `available_at` 直接阻断失败；`"approximate"` 模式标记 `pit_status="PIT_APPROXIMATE"` 并将质量记为 `degraded`。 |
| **GAP-04** | **最终盲测物理硬阻断防护**：准备阶段严禁打开最终盲测（Holdout）。 | `框架/backend/candidate_backtest_runner.py`（**新增**） | 增加 `_verify_holdout_boundary` 与 `HoldoutSealedError`：窗口一旦越入盲测区间且盲测处于封存状态，直接抛错阻断，从代码级杜绝未来函数和样本泄露。 |
| **GAP-05** | **测试找寻成功 run 缺少状态过滤导致偶发失败**：`test_bt_ui_01_read_path.py` 寻找无 chart 的 run 时未校验 `status=="succeeded"`，偶选失败 run 造成断言异常。 | `框架/backend/tests/test_bt_ui_01_read_path.py`（**修改**） | 增加 `run_doc.get("status") == "succeeded"` 过滤，确保测试与 API 设计契约一致。 |
| **GAP-06** | **六项 P0 专项验证测试套件**：缺乏覆盖 06-B 工单六项 P0 验收判据的专项自动化回归单测。 | `框架/backend/tests/test_candidate_backtest_runner.py`（**新增**） | 编写 7 项覆盖 P0-1 至 P0-6 的自动化单测，100% PASS。 |

---

## 三、未触碰与严格保护边界

严格遵守用户规则与执行闸：
1. **不碰 UI 文件**：未修改 `UI界面/server.js`、`UI界面/index.html`、`UI界面/web/assets/` 等任何前端代码。
2. **不碰共享规范**：未修改共享 `README.md`、`AGENTS.md` 及现有接口契约。
3. **不碰策略核心与参数**：未改变策略参数、未改变 95% 追踪止损阈值、未改变数据划分或 Python 环境版本。
4. **不碰真实生产与模拟运行库**：回测过程完全只读，输出全部限定在原子隔离运行目录（`runs/<run_id>/`）内，不写任何生产锁或实盘表。
5. **不打开最终盲测**：最终盲测区间（2024-10-01 之后或指定留出区间）设置硬阻断拦截，未获最终评估授权严禁打开。
