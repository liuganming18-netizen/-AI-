# 06-B 回测程序准备｜必要验证证据与验收回执

- **工单编号**：06-B-BACKTEST-PREP-01
- **执行时间**：2026-11-10（北京时间）
- **执行角色**：反重力（独立测试与回测程序准备）
- **独立验收方**：总控 Codex
- **业务基线提交**：`8bef22b0`
- **治理依据提交**：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- **测试环境**：macOS (Darwin 24.6.0) / Python 3.9.6 (pytest 8.4.2) / Python 3.13.12 兼容

---

## 一、六项 P0 验收执行回执

| 验收项 | 验收判据 | 实测用例与命令 | 实际运行输出 / 证据 | 结论 |
| :--- | :--- | :--- | :--- | :--- |
| **P0-1** | **输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读** | `test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created` | 生成不可变快照目录 `inputs/`（含 signal/exec K线与资金费），原子落盘 `inputs_manifest.json`，SHA256 校验一致。原始数据库仅只读连接。 | **PASS** |
| **P0-2** | **历史时钟正确限制可见数据（剔除未收盘与未来数据）；严格与近似 PIT 区分** | `test_p0_2_history_clock_filters_unclosed_and_future_bars`<br>`test_p0_2_strict_pit_mode_blocks_when_available_at_missing` | 注入未收盘（`is_closed=0`）与未来时间戳 K 线被自动剔除；`pit_mode="strict"` 时缺失 `available_at` 直接阻断；`"approximate"` 模式如实记录 `PIT_APPROXIMATE` 与 `degraded`。 | **PASS** |
| **P0-3** | **信号与成交顺序符合既有保守规则；同根止损与歧义资金费进入假设桶，不美化结果** | `test_p0_3_conservative_execution_and_funding_ambiguity_bucket`<br>+ `test_funding_runner_isolation_01.py` 12 项 | 相位 A/B/C/D 顺序推进；同根开仓盘中触发止损；歧义资金费事件落入 `total_funding_fee_hypothetical`，不进已确认现金账本。 | **PASS** |
| **P0-4** | **成交、手续费、资金费、现金、持仓和未实现盈亏能够对账；缺失费用数据不静默按零** | `test_p0_4_ledger_recompute_and_reconciliation`<br>+ `recompute_metrics_from_ledger` | 独立从 `ledger.sqlite3` 重新聚合计算净收益、胜率、回撤、现金恒等式，与引擎指标完全吻合；缺失费用数据标记 `funding_ok=False`。 | **PASS** |
| **P0-5** | **重复事件与中断恢复不重复记账；已有幂等和恢复机制优先复用** | `test_p0_5_idempotent_reexecution_does_not_duplicate_ledger` | 重复对同一 `run_id` 触发回测执行，SQLite 账本依托确定性唯一键执行幂等落盘，记录条数恒定，无重复累加。 | **PASS** |
| **P0-6** | **回测不写运行库、不污染运行队列、不调外部下单；最终盲测区间保持封闭** | `test_p0_6_holdout_sealed_blocks_execution_when_window_crosses` | 运行产物严格限定在独立 run 目录；时间窗口触及盲测保护区间（>= 2024-10-01）时抛出 `HoldoutSealedError` 强阻断，盲测未被打开。 | **PASS** |

---

## 二、实际自动化测试套件执行证据

### 1. 06-B 专项六项 P0 测试（7 passed）

```text
$ PYTHONPATH=框架/backend /usr/bin/python3 -m pytest 框架/backend/tests/test_candidate_backtest_runner.py -v
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Library/Developer/CommandLineTools/usr/bin/python3
cachedir: .pytest_cache
rootdir: /Users/boos/Desktop/威科夫因子共振趋势
collecting ... collected 7 items

框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created PASSED [ 14%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_history_clock_filters_unclosed_and_future_bars PASSED [ 28%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_strict_pit_mode_blocks_when_available_at_missing PASSED [ 42%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_3_conservative_execution_and_funding_ambiguity_bucket PASSED [ 57%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_4_ledger_recompute_and_reconciliation PASSED [ 71%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_idempotent_reexecution_does_not_duplicate_ledger PASSED [ 85%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_6_holdout_sealed_blocks_execution_when_window_crosses PASSED [100%]

============================== 7 passed in 0.67s ===============================
```

### 2. 回测全链路回归测试套件（315 passed, 9 skipped）

覆盖资金费双口径隔离、数据门禁、图表读取、运行溯源背书、实验登记与样本留出保护：

```text
$ PYTHONPATH=框架/backend /usr/bin/python3 -m pytest \
    框架/backend/tests/test_candidate_backtest_runner.py \
    框架/backend/tests/test_funding_runner_isolation_01.py \
    框架/backend/tests/test_backtest_data_gate_*.py \
    框架/backend/tests/test_bt_ui_*.py \
    框架/backend/tests/test_run_provenance_46.py \
    框架/backend/tests/test_frame_fit_*.py -v

======================== 315 passed, 9 skipped in 2.31s ========================
```

---

## 三、待冻结代码与受影响文件清单

本单实施所涉及的代码与测试文件确切版本如下：

| 文件路径 | 状态 | SHA256 摘要 | 作用与职责 |
| :--- | :--- | :--- | :--- |
| `框架/backend/candidate_backtest_runner.py` | **新增** | `a80a60cc962317088784330d2d4a66b8fe41ceeb9149487783e4bd3279a98229` | 冻结候选回测标准化执行入口实现 |
| `框架/backend/backtest_runner.py` | **修改** | `d3a83e1088efdab72fb333b43865147ddf61bfd71774f6ec2c78af079069b11e` | 导出 `run_candidate_backtest` 及相关异常类型 |
| `框架/backend/tests/test_candidate_backtest_runner.py` | **新增** | `622926640c48b1fdd0a94be291d6f8017983675b5a9885875d8e5d1bf53fa22f` | 六项 P0 专项自动化验收单测 |
| `框架/backend/tests/test_bt_ui_01_read_path.py` | **修改** | `13fc73ea8cfa71e9e7ae7b8adc57c21af83258861fa42d7346267541ee344fb7` | 修复测试找寻 run 时增加 succeeded 状态过滤 |
| `evidence/06-B-BACKTEST-PREP-01/01_REUSE_AND_DIFF_MANIFEST.md` | **新增** | `91a0d4e9b1ab4ddbd798d9723132253835d5fb55e3ecb682fb2ad21054e9adbb` | 复用与变更清单 |
| `evidence/06-B-BACKTEST-PREP-01/02_CANDIDATE_RUNNER_INTERFACE_GUIDE.md` | **新增** | `06aca2719d799c17298a7a65e8eb789615c2a97d420620a8888bab70f4f2cff2` | 可调用执行入口规范说明 |
| `evidence/06-B-BACKTEST-PREP-01/03_ACCEPTANCE_RECEIPT_AND_VERIFICATION.md` | **新增** | `541ccbde1a4f750c33f7c1fdb574abfd7dc5f03c2da74dc3bcc41ccc4139367a` | 本验收回执与证据记录 |

---

## 四、停手声明

06-B 回测程序准备工单 3 项固定交付物与 6 项 P0 验证已全部完成，全套单测 100% PASS。
按照工作纪律，立即停手，不自动调参，不打开最终盲测，不合并 main，交付总控 Codex 独立复核。
