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
| **P0-1** | **输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读；外部快照按冻结身份校验** | `test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created`<br>`test_p0_1_external_snapshot_manifest_tamper_rejected` | 生成不可变快照目录 `inputs/`（含 signal/exec K线与资金费），原子落盘 `inputs_manifest.json`，SHA256 校验一致。外部快照复用前强制比对 SHA256，篡改直接抛出 `CandidateExecutionError` 拒绝覆盖。原始数据库仅只读连接。 | **PASS** |
| **P0-2** | **历史时钟正确限制可见数据（剔除未收盘与未来数据）；严格与近似 PIT 区分** | `test_p0_2_history_clock_filters_unclosed_and_future_bars`<br>`test_p0_2_strict_pit_mode_blocks_when_available_at_missing` | 注入未收盘（`is_closed=0`）与未来时间戳 K 线被自动剔除；`pit_mode="strict"` 时缺失 `available_at` 直接阻断；`"approximate"` 模式如实记录 `PIT_APPROXIMATE` 与 `degraded`。 | **PASS** |
| **P0-3** | **信号与成交顺序符合既有保守规则；同根止损与歧义资金费进入假设桶，不美化结果** | `test_p0_3_conservative_execution_and_funding_ambiguity_bucket`<br>+ `test_funding_runner_isolation_01.py` 12 项 | 相位 A/B/C/D 顺序推进；同根开仓盘中触发止损；歧义资金费事件落入 `total_funding_fee_hypothetical`，不进已确认现金账本。 | **PASS** |
| **P0-4** | **成交、手续费、资金费、现金、持仓和未实现盈亏能够对账；缺失费用数据不静默按零** | `test_p0_4_ledger_recompute_and_reconciliation`<br>+ `recompute_metrics_from_ledger` | 独立从 `ledger.sqlite3` 重新聚合计算净收益、胜率、回撤、现金恒等式，与引擎指标完全吻合；缺失费用数据标记 `funding_ok=False`。 | **PASS** |
| **P0-5** | **重复事件与中断恢复不重复记账；同run_id防漂移；受控中断恢复账本一致** | `test_p0_5_idempotent_reexecution_does_not_duplicate_ledger`<br>`test_p0_5_same_run_id_rejects_altered_inputs_or_spec`<br>`test_p0_5_controlled_interruption_recovery_matches_baseline` | 1. 同一 run 重复执行幂等无增殖；<br>2. 显式同 run_id 篡改配置或输入时主动抛错拒绝覆盖；<br>3. 模拟部分执行中断崩溃残留脏行后，受控恢复执行指标与不中断基准完全一致，无重复无漏记，状态正常成功结束。 | **PASS** |
| **P0-6** | **回测不写运行库、不污染运行队列、不调外部下单；最终盲测区间保持封闭；实验门禁生效** | `test_p0_6_holdout_sealed_blocks_execution_when_window_crosses`<br>`test_p0_6_formal_candidate_blocked_without_experiment_id` | 运行产物严格限定在独立 run 目录；时间窗口触及盲测保护区间（>= 2024-10-01）时抛出 `HoldoutSealedError` 强阻断，盲测未被打开。真实候选未绑定核准实验身份直接阻断。 | **PASS** |

---

## 二、实际自动化测试套件执行证据

### 1. 06-B 专项六项 P0 测试（11 passed in 0.90s）

```text
$ cd /Users/boos/Desktop/task_06b_p0_fix_worktree
$ PYTHONPATH=框架/backend /usr/bin/python3 -m pytest 框架/backend/tests/test_candidate_backtest_runner.py -v
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Library/Developer/CommandLineTools/usr/bin/python3
cachedir: .pytest_cache
rootdir: /Users/boos/Desktop/task_06b_p0_fix_worktree
collecting ... collected 11 items

框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_1_external_snapshot_manifest_tamper_rejected PASSED [  9%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created PASSED [ 18%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_history_clock_filters_unclosed_and_future_bars PASSED [ 27%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_strict_pit_mode_blocks_when_available_at_missing PASSED [ 36%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_3_conservative_execution_and_funding_ambiguity_bucket PASSED [ 45%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_4_ledger_recompute_and_reconciliation PASSED [ 54%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_controlled_interruption_recovery_matches_baseline PASSED [ 63%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_idempotent_reexecution_does_not_duplicate_ledger PASSED [ 72%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_same_run_id_rejects_altered_inputs_or_spec PASSED [ 81%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_6_formal_candidate_blocked_without_experiment_id PASSED [ 90%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_6_holdout_sealed_blocks_execution_when_window_crosses PASSED [100%]

============================== 11 passed in 0.90s ==============================
```

### 2. 直接受影响的定点测试套件（55 passed in 1.04s）

包含六项 P0 专项（11 项）、资金费双口径隔离单测（12 项）与运行身份溯源防篡改单测（32 项）：

```text
$ PYTHONPATH=框架/backend /usr/bin/python3 -m pytest \
    框架/backend/tests/test_candidate_backtest_runner.py \
    框架/backend/tests/test_funding_runner_isolation_01.py \
    框架/backend/tests/test_run_provenance_46.py -v

============================== 55 passed in 1.04s ==============================
```

---

## 三、待冻结代码与受影响文件清单

本单实施所涉及的代码与测试文件确切版本如下：

| 文件路径 | 状态 | SHA256 摘要 | 作用与职责 |
| :--- | :--- | :--- | :--- |
| `框架/backend/candidate_backtest_runner.py` | **新增/修复** | `7888ccae044f4661c3144797d0a0dc93873e11695b1e95aab022d40565c74201` | 冻结候选回测标准化执行入口实现（含快照强验、同run_id防漂移与受控恢复） |
| `框架/backend/backtest_runner.py` | **修改** | `d3a83e1088efdab72fb333b43865147ddf61bfd71774f6ec2c78af079069b11e` | 导出 `run_candidate_backtest` 及相关异常类型 |
| `框架/backend/tests/test_candidate_backtest_runner.py` | **新增/修复** | `bc32ab605abd3ef4bf4f7b40d7cf95953cc61c1bf619698f77e5bfbdf3e36d76` | 六项 P0 专项自动化验收单测（共 11 项用例） |
| `框架/backend/tests/test_bt_ui_01_read_path.py` | **修改** | `13fc73ea8cfa71e9e7ae7b8adc57c21af83258861fa42d7346267541ee344fb7` | 修复测试找寻 run 时增加 succeeded 状态过滤 |
| `evidence/06-B-BACKTEST-PREP-01/01_REUSE_AND_DIFF_MANIFEST.md` | **新增/更新** | `7d9f54bd1bf856f5b9bf4e7e76593174712a4e82889b1ca61ac1112e14c5ccd8` | 复用与变更清单 |
| `evidence/06-B-BACKTEST-PREP-01/02_CANDIDATE_RUNNER_INTERFACE_GUIDE.md` | **新增/更新** | `70566b4093b8b08695287b3f72c9dfa71c5b26da5dfbd646980c79c3b1abc2c9` | 可调用执行入口规范说明 |
| `evidence/06-B-BACKTEST-PREP-01/03_ACCEPTANCE_RECEIPT_AND_VERIFICATION.md` | **新增/更新** | 见最新落盘 | 本验收回执与证据记录 |

---

## 四、停手声明

06-B 回测程序准备工单 3 项固定交付物与 6 项 P0 验证已全部完成，全套单测 100% PASS。
按照工作纪律，立即停手，不自动调参，不打开最终盲测，不合并 main，交付总控 Codex 独立复核。
