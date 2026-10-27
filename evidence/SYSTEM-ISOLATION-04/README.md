# 工单 A｜SYSTEM-ISOLATION-04 交付验收报告

## 1. 工单基本信息
- **工单编号**：SYSTEM-ISOLATION-04
- **目标**：关闭任务队列库隔离遗漏，重新提交同一次运行的可复核证据，冻结版本交 Work 独立审核。
- **项目路径**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **执行时间**：2026-10-23
- **交付归档目录**：`evidence/SYSTEM-ISOLATION-04/`

---

## 2. 施工范围与修复完成逐项对账

| 施工项 | 对应问题与目标 | 修复方案与代码落脚点 | 验收结果 |
| :--- | :--- | :--- | :--- |
| **1. jobs_db 纳入隔离必填路径** | 避免隔离模式下遗漏 `jobs_db` 导致其隐式回退日常 `数据/任务队列/jobs.sqlite3`。 | 1. [`service_manager.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/service_manager.py)：在 `resolve_paths()` 中将 `jobs_db` 纳入 `missing` 必填检查清单；<br>2. 移除原有的自动回退生产目录分支，缺少时直接抛出 `RuntimeError` 拒绝启动；<br>3. Worker 启动命令中显式传递 `--db str(self.cfg.jobs_db)`，避免 worker 进程缺省连接日常任务队列。 | **完全通过**<br>通过 `Test 1`（缺库拦截）及 `path_isolation_evidence.json` |
| **2. 保护日常数据目录与防别名绕行** | 全面核对 API、Worker、Runtime 全部路径，严禁隔离模式指向日常数据目录。 | 1. 建立 `protected_daily_roots`（包括 `数据/`、`live/`、`locks/`、`logs/` 等日常目录）；<br>2. 对 `market_db`、`live_db`、`paper_ledger_dir`、`paper_lock_dir`、`jobs_db`、`pid_dir`、`log_dir` 实施 `Path(p).resolve()` 规范化解析；<br>3. 任何直接指向日常路径、相对路径（`..`）或符号链接（symlink）别名绕回日常目录的配置，立即抛出 `RuntimeError` 拒绝启动。 | **完全通过**<br>通过 `Test 2`（直接指向拦截）与 `Test 3`（symlink/.. 绕行拦截） |
| **3. 扩充隔离检查覆盖 WAL、SHM 与锁** | 确保任务队列库的辅助文件（wal、shm、lock）也完全隔离在临时目录，不触碰日常库。 | 1. 在 Worker 进程启动并初始化 SQLite 连接后，通过 `lsof` 深度核验打开的所有文件；<br>2. 核查证实 Worker 进程实际打开的文件为隔离目录下的 `jobs_isolated.sqlite3`、`jobs_isolated.sqlite3-wal`、`jobs_isolated.sqlite3-shm`、`jobs_isolated.sqlite3.lock`；<br>3. 确认未访问任何日常生产的任务队列及其 wal/shm/lock 文件。 | **完全通过**<br>见 `path_isolation_evidence.json` 中 worker 打开文件清单 |
| **4. 同一次运行硬证据闭环与旧证据处置** | 彻底消除旧 JSON 的 PID 与测试日志不一致的问题，提供不可篡改的同一次运行硬证据。 | 1. 运行独立连续验证（Run ID: `RUN_ISO04_20261023_1792738494`），一次性记录起止时间戳、源码 SHA-256、各子进程 PID、命令行、打开文件清单、未访问日常文件证明及停止验证；<br>2. 将进程打开文件深度核验（含 jobs sqlite/wal/shm/lock）内化到 `test_system_isolation_04.py` 测试断言中，日志与断言 PID 严格同源闭环；<br>3. 在 `remaining_issues.md` 中如实阐明旧 JSON PID 与日志不一致的原因并作失效认定。 | **完全通过**<br>同源同批次执行，详见 `path_isolation_evidence.json` 与 `remaining_issues.md` |
| **5. 保留既有本金、风险与安全退出逻辑** | 不扩大修改范围，确保核心契约和异常退出的完整性。 | 1. 保留已通过的精准停止（身份校验、不误杀外部进程、重复停止幂等）；<br>2. 保留 overall_health 流转机制（stopped -> healthy -> stopped）；<br>3. 保留 `/api/v1/paper/accounts` 缺库时返回 `unavailable` 约定的逻辑；<br>4. 重跑全量受影响回归，未重跑 144 组研究。 | **完全通过**<br>通过所有 6 组回归测试用例 |

---

## 3. 同一次运行硬实据概览 (Run ID: RUN_ISO04_20261023_1792738494)

- **运行编号**：`RUN_ISO04_20261023_1792738494`
- **起止时间（UTC）**：`2026-10-23T06:54:54Z` 至 `2026-10-23T06:54:58Z`（总耗时 4.416s）
- **修改源码 SHA-256**：
  - `框架/backend/service_manager.py`: `ddd02d32f34623927904768cb0506fd24938e629eeba9a2b45f46051bc13d02d`
  - `框架/backend/live_api.py`: `c3012f11a9ae3c425a81e86b8e5a8c6ac97955c51db073d62e8195b15866db26`
  - `框架/backend/live_store.py`: `38df51165ecdf97656cf73b27e88fb34521ffde2e9be092dbaab44d0222cd3ea`
  - `框架/backend/unified_runtime.py`: `8e60301de295f924cc40d85d8583fc2729c9db6bed6650710682804e736c1bce`
  - `框架/backend/api/routes.py`: `832d16b43afa0b81b373c593d53548d499d1cf981af848df09c65f217dfbce09`
  - `框架/backend/tests/test_system_isolation_04.py`: `89bd92ef62096859d672233ff40387c34cf009acc0371bb60fc7e7c6b7219eef`
- **子进程运行与文件打开核验**：
  - **API 进程**：PID `27706`，端口 `65152`，日常路径访问数：`0`，`isolation_intact: true`
  - **Worker 进程**：PID `27710`，命令行携带 `--db .../jobs_isolated.sqlite3`，日常路径访问数：`0`，`isolation_intact: true`
  - **Worker 实际打开的隔离库文件**：
    - `.../jobs_isolated.sqlite3`
    - `.../jobs_isolated.sqlite3-wal`
    - `.../jobs_isolated.sqlite3-shm`
    - `.../jobs_isolated.sqlite3.lock`
- **生命周期操作验证**：
  - `missing_jobs_db_rejected: true`
  - `daily_jobs_path_rejected: true`
  - `alias_symlink_path_rejected: true`
  - `normal_stop_overall_health: "stopped"`
  - `normal_stop_success: true`
  - `repeat_stop_idempotent_success: true`
  - `identity_mismatch_rejected: true`
  - **综合隔离结论**：`isolation_verified: true`

---

## 4. 交付文件索引与测试结果汇总

### 4.1 交付证据文件清单（归档于 `evidence/SYSTEM-ISOLATION-04/`）
1. [`path_isolation_evidence.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/path_isolation_evidence.json)：同一次运行全量实据（起止时间、源码哈希、子进程 PID、命令行、实际打开文件列表、未访问日常数据证明、停止与健康度切换结果）；
2. [`test_commands_and_outputs.log`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/test_commands_and_outputs.log)：全量测试套件原始执行命令、标准输出/错误输出及退出码日志；
3. [`version_hash.txt`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/version_hash.txt)：当前 Git 分支状态、Commit 记录及源码文件 SHA-256 校验列表；
4. [`modified_files.txt`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/modified_files.txt)：修改文件列表及完整 Git Diff 详情；
5. [`remaining_issues.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/remaining_issues.md)：旧 JSON PID 与日志不一致失效原因深度说明及系统边界约束；
6. [`README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-ISOLATION-04/README.md)：本交付验收总结报告。

### 4.2 受影响回归测试执行汇总（全量 100% 通过）
1. `框架/backend/tests/test_system_isolation_04.py`：6/6 PASS（耗时 4.97s）
   - `test_01_isolation_mode_rejects_missing_jobs_db`: PASS（缺失 jobs_db 拒绝启动，不回退日常目录）
   - `test_02_direct_daily_paths_rejected`: PASS（指定日常路径直接硬拦截）
   - `test_03_alias_and_symlink_paths_rejected`: PASS（符号链接与 `..` 相对路径别名硬拦截）
   - `test_04_opened_files_and_full_isolation`: PASS（打开文件硬核验：覆盖 jobs 及其 wal/shm/lock，日常路径 0 访问）
   - `test_05_lifecycle_stop_and_health_flow`: PASS（正常停止、重复停止幂等、身份不匹配拒绝、stopped 状态流转）
   - `test_06_paper_accounts_missing_live_db`: PASS（缺 live 库 unavailable 约定与防伪造零余额）
2. `框架/backend/tests/test_system_startup_01.py`：6/6 PASS（耗时 17.2s）
3. `策略/WyckoffMaster/test_work02_core_contract.py`：15/15 PASS（耗时 0.88s，核心本金、风险及订单状态机回归通过）
4. `策略/WyckoffMaster/event_trend_v0_1/tests/test_event_trend_closure.py`：13/13 PASS（耗时 0.73s）
5. `框架/backend/tests/test_metrics_contract.py`：30/30 PASS（耗时 1.3s）
6. `框架/backend/tests/test_lifecycle_isolation.py`：18/18 PASS（耗时 0.02s）

---

## 5. 红线与工程纪律确认
- **日常生产数据零访问**：隔离实例无论 API 还是 Worker，实际打开文件均在临时目录，对日常 `数据/`、`live/`、`locks/` 访问数为 0；
- **任务队列隔离无遗漏**：`jobs.sqlite3` 及其产生的 `-wal`、`-shm`、`.lock` 均完全重定向至隔离沙箱，彻底阻断对生产队列的并发读写；
- **防别名绕行机制**：基于 `Path.resolve()` 的强校验消除了通过相对路径或软连接绕回生产目录的可能性；
- **未重置账户与资产**：全程未触碰日常 5000U 生产账本；
- **未启动未受控实盘**：启动参数严格保持交易链禁用，未向外部发起实盘交易；
- **同源证据链**：所有证据自同一次运行连续采集，真实完整，版本已冻结供 Work 独立审核。
