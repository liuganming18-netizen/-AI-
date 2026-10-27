# 工单 A｜SYSTEM-ISOLATION-03 交付验收报告

## 1. 工单基本信息
- **工单编号**：SYSTEM-ISOLATION-03
- **目标**：修复启动隔离、停止与账户缺库处理，保留已通过的本金和风险逻辑。
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **执行时间**：2026-10-23
- **交付归档目录**：`evidence/SYSTEM-ISOLATION-03/`

---

## 2. 施工范围完成情况逐项对账

| 施工项 | 对应问题与目标 | 修复方案与代码落脚点 | 验收结果 |
| :--- | :--- | :--- | :--- |
| **1. 启动路径隔离与环境透传** | 解决 `service_manager` fallback 覆盖隔离路径，导致隔离实例读写日常库的问题。 | 1. `ServiceConfig` 增加 `live_db`、`live_dir`、`is_isolated` 字段；<br>2. `resolve_paths()` 强制隔离模式强校验：缺少 6 项必要路径或指向日常目录直接 `RuntimeError` 拒绝启动，绝不退回日常目录；<br>3. `shared_env` 显式设置并透传 `PORT`、`MARKET_DB`、`LIVE_DB`、`LIVE_DIR`、`PAPER_LEDGER_DIR`、`PAPER_LOCK_DIR`、`JOBS_DB` 到全量子进程。 | **完全通过**<br>见 `path_isolation_evidence.json` 硬实据 |
| **2. 进程精准停止与外部进程防护** | 解决停止自己启动的进程失败、按端口盲杀或凭 PID 强杀的问题。 | 1. 建立基于 `service_meta_{port}.json`、PID 文件、`cmdline` 与 `cwd` 的多重身份校验机制；<br>2. 仅对身份核验一致的本实例进程执行 SIGTERM 优雅退出（超时 SIGKILL）；<br>3. 外部未知进程占用端口或 PID 身份不符时明确抛错拒绝（`RuntimeError: 拒绝停止...身份不匹配`）；<br>4. 重复停止保证完全幂等，不抛未捕获异常。 | **完全通过**<br>通过 `Test 3-A`（幂等）与 `Test 3-B`（拒绝误杀） |
| **3. 停止状态与健康度准确区分** | 解决停止后仍显示 `overall_health=healthy` 的虚假健康问题。 | 1. `unified_runtime.py`：在 `get_full_status` 与 `get_unified_runtime_status` 中增加停止态检测，停止时明确报告 `stopped`，崩溃报告 `unhealthy`；<br>2. `api/routes.py`：`_paper_daemon_status` 停止时返回 `quality.status = "stopped"`；<br>3. `service_manager.py`：`status()` 顶层增加 `overall_health` 字段，严格区分 `healthy`、`degraded`、`stopped`、`unhealthy`。 | **完全通过**<br>通过 `Test 4`（流转检验 stopped -> healthy -> stopped） |
| **4. /api/v1/paper/accounts 缺库处理** | 解决缺少 live 库时抛未处理 `OperationalError` 500 的问题。 | 1. `live_store.py`：`get_run()` 增加连接异常安全捕获，不抛出崩溃；<br>2. `live_api.py`：`_paper_accounts_legacy()` 增加前置 `LIVE_DB.exists()` 及运行时 SQLite 异常捕获；<br>3. 复用既有约定返回 `quality.status = "unavailable"`，`items = []`，`missing_reason = "实时信号库缺失：..."`；<br>4. 严禁伪造零余额条目，严禁自动在日常目录隐式创建库。 | **完全通过**<br>通过 `Test 5`（缺库 unavailable 约定校验） |

---

## 3. 测试与硬证据汇总

### 3.1 交付证据文件索引
- `path_isolation_evidence.json`：临时独立目录启动隔离实例，子进程 PID 及 `lsof` 实际打开文件描述符硬核验证据（证明 0 访问日常数据路径，`isolation_verified=True`）；
- `version_hash.txt`：当前基线 Commit 与 Git 分支状态；
- `modified_files.txt`：本次修改的文件清单与完整变更 Diff；
- `test_commands_and_outputs.log`：全部测试套件运行的原始命令行与 stdout/stderr 完整输出；
- `remaining_issues.md`：边界约束与剩余问题说明；
- `README.md`：本交付总结报告。

### 3.2 运行测试套件汇总（全量 100% 通过）
1. `框架/backend/tests/test_system_isolation_03.py`：5 用例全部通过（耗时 7.0s）
   - `test_01_isolation_mode_rejects_missing_paths`: PASS
   - `test_02_subprocesses_path_isolation_and_open_files`: PASS
   - `test_03_stop_precision_and_idempotency_and_mismatch_rejection`: PASS
   - `test_04_overall_health_accurate_distinction`: PASS
   - `test_05_paper_accounts_missing_live_db_handling`: PASS
2. `框架/backend/tests/test_system_startup_01.py`：6 用例全部通过（耗时 17.3s）
3. `策略/WyckoffMaster/test_work02_core_contract.py`：15 契约用例全部通过（耗时 0.94s）
4. `策略/WyckoffMaster/event_trend_v0_1/tests/test_event_trend_closure.py`：13 用例全部通过（耗时 0.70s）
5. `框架/backend/tests/test_metrics_contract.py`：30 用例全部通过（耗时 1.2s）
6. `框架/backend/tests/test_lifecycle_isolation.py`：18 用例全部通过（耗时 0.02s）

---

## 4. 遵守纪律声明
- 全程未重置生产账户、未修改既有 5000U 账本数据；
- 未删除或覆盖日常生产数据文件（`market_v2.sqlite3` 等）；
- 未触碰日常运行状态，未破坏采集器逻辑；
- 未启动未受控的实盘或前向验证；
- 全程遵循最小修改原则，复用既有模块，不另建多余框架。
- 版本现已冻结，供 Work 进行独立审核。
