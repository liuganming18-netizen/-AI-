# 06-B 回测程序准备｜必要验证证据与验收回执 (反例收敛与缺口闭环修复版 R2)

- **工单编号**：06-B-BACKTEST-PREP-01（独立复核缺口闭环补正）
- **执行时间**：2026-11-11（北京时间）
- **执行角色**：反重力（独立测试与回测程序准备）
- **独立验收方**：总控 Codex
- **业务基线提交**：`8bef22b0`
- **实际父提交**：`b534b3061f75afe3353c0bdb5b2052f0bfc730bc`
- **前序复核提交**：`3e3277cb44306d9945473101350856c50e17ff98`
- **治理依据提交**：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- **测试环境**：macOS (Darwin 24.6.0) / Python 3.9.6 (pytest 8.4.2)
- **纪律声明**：**依总控独立复核裁决，原 3 项交付、6 项 P0 分母保持不变；针对写前输入强验、同 run_id 空凭据防覆盖与目录不变断言、实验门禁组合绕过关闭、非空持久化受控中断恢复及交付回执日志补交 5 项缺口全面收敛闭环，逐项如实报告实测结论，交独立复核。**

---

## 一、六项 P0 验收执行回执与缺口闭环补正说明

| 验收项 | 验收判据 | 实测用例与补正闭环验证 | 实际运行输出 / 证据 | 自测结论 |
| :--- | :--- | :--- | :--- | :---: |
| **P0-1** | **输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读；统一覆盖资金费；写前输入强验** | `test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created`<br>`test_p0_1_input_validation_requires_funding_and_rejects_corrupted_manifest` | 1. 内存快照、外部目录与只读库统一校验完整输入身份，内存与外部快照缺失资金费（`funding_events`）直接抛错拒绝；<br>2. **写前输入强验**：外部快照缺失 `inputs_manifest.json`、内容为 `{}`、身份字段不完整或损坏时，在任何写入操作前（未创建目标 run 目录）直接抛出 `CandidateExecutionError` 拒绝，杜绝无凭据输入。 | **PASS (闭环完成)** |
| **P0-2** | **历史时钟正确限制可见数据（剔除未收盘与未来数据）；严格与近似 PIT 区分** | `test_p0_2_history_clock_filters_unclosed_and_future_bars`<br>`test_p0_2_strict_pit_mode_blocks_when_available_at_missing` | 注入未收盘（`is_closed=0`）与未来时间戳 K 线被自动剔除；`pit_mode="strict"` 时缺失 `available_at` 直接阻断；`"approximate"` 模式如实记录 `PIT_APPROXIMATE` 与 `degraded`。 | **PASS** |
| **P0-3** | **信号与成交顺序符合既有保守规则；同根止损与歧义资金费进入假设桶，不美化结果** | `test_p0_3_conservative_execution_and_funding_ambiguity_bucket`<br>+ `test_funding_runner_isolation_01.py` 12 项 | 相位 A/B/C/D 顺序推进；同根开仓盘中触发止损；歧义资金费事件落入 `total_funding_fee_hypothetical`，不进已确认现金账本。 | **PASS** |
| **P0-4** | **成交、手续费、资金费、现金、持仓和未实现盈亏能够对账；缺失费用数据不静默按零** | `test_p0_4_ledger_recompute_and_reconciliation`<br>+ `recompute_metrics_from_ledger` | 独立从 `ledger.sqlite3` 重新聚合计算净收益、胜率、回撤、现金恒等式，与引擎指标完全吻合；缺失费用数据标记 `funding_ok=False`。 | **PASS** |
| **P0-5** | **重复事件与中断恢复不重复记账；同run_id防漂移；冲突在set_status(RUNNING)前拒绝且旧状态/快照/账本不变；受控中断恢复无重复无遗漏** | `test_p0_5_manual_dirty_residue_cleanup_on_recovery`<br>`test_p0_5_same_run_id_rejects_altered_inputs_or_spec_before_running`<br>`test_p0_5_controlled_interruption_recovery_and_reconciliation` | 1. **规范化配置比对**：正确处理缺省值、新增字段、exchange/market，同 run_id 规则或配置漂移直接拒绝；<br>2. **空凭据防覆盖与目录不变断言**：旧成功 run 的 manifest 置空为 `{}` 后仅修改 funding 再次调用，在写前直接拦截；断言拒绝后旧成功状态 `SUCCEEDED` 与旧目录所有文件哈希保持 100% 原始不变；<br>3. **实际持久化非空中断恢复与逐笔对账**：构造包含 3 笔确定性成交的非空候选对象，在实际持久化落盘阶段（此时 SQLite 账本与 `trades.jsonl` 已落盘非空数据）注入可控崩溃中断；再次调用恢复重跑，核验从恢复后 SQLite 查出的每一笔交易（价格、方向、数量、费用、资金费、净收益等）与基准完全一致，无重复（不多不少恰 3 笔）、无遗漏，现金恒等式 `cash_identity_ok=True`，状态正常结束。 | **PASS (闭环完成)** |
| **P0-6** | **回测不写运行库、不污染运行队列、不调外部下单；最终盲测区间保持封闭；真实实验注册门禁生效** | `test_p0_6_experiment_registry_gate_validates_existence_and_freeze`<br>`test_p0_6_holdout_sealed_blocks_execution_when_window_crosses` | 1. 运行产物严格限定在独占隔离目录；时间窗口触及盲测保护区间（>= 2024-10-01）抛出 `HoldoutSealedError` 强阻断；<br>2. **关闭已复现的组合绕过**：正式策略候选（包含所有 `WX_*`）必须绑定已冻结实验，严禁通过 `is_mechanism_test=True` 省略实验 ID 绕过门禁；在 06-B 阶段未获授权前，严禁传入 `holdout_sealed=False` 试图解封盲测，门禁一律硬阻断拒绝。 | **PASS (闭环完成)** |

---

## 二、实际自动化测试套件执行证据

### 1. 实际测试分布核正与执行结果（55 passed in 1.16s）

本次执行严格更正回执分布为 **11 + 12 + 32 = 55** 项：
- 六项 P0 专项单测：11 项（`框架/backend/tests/test_candidate_backtest_runner.py`）
- 资金费双口径隔离单测：12 项（`框架/backend/tests/test_funding_runner_isolation_01.py`）
- 运行身份溯源防篡改单测：32 项（`框架/backend/tests/test_run_provenance_46.py`）

真实完整执行日志已物理持久化并强制跟踪落盘于 `evidence/06-B-BACKTEST-PREP-01/test_run.log`：

```text
$ cd /Users/boos/Desktop/task_06b_p0_fix_worktree
$ PYTHONPATH=框架/backend /Library/Developer/CommandLineTools/usr/bin/python3 -m pytest \
    框架/backend/tests/test_candidate_backtest_runner.py \
    框架/backend/tests/test_funding_runner_isolation_01.py \
    框架/backend/tests/test_run_provenance_46.py -v

============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Library/Developer/CommandLineTools/usr/bin/python3
cachedir: .pytest_cache
rootdir: /Users/boos/Desktop/task_06b_p0_fix_worktree
collecting ... collected 55 items

框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created PASSED [  1%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_1_input_validation_requires_funding_and_rejects_corrupted_manifest PASSED [  3%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_history_clock_filters_unclosed_and_future_bars PASSED [  5%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_2_strict_pit_mode_blocks_when_available_at_missing PASSED [  7%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_3_conservative_execution_and_funding_ambiguity_bucket PASSED [  9%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_4_ledger_recompute_and_reconciliation PASSED [ 10%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_controlled_interruption_recovery_and_reconciliation PASSED [ 12%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_manual_dirty_residue_cleanup_on_recovery PASSED [ 14%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_5_same_run_id_rejects_altered_inputs_or_spec_before_running PASSED [ 16%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_6_experiment_registry_gate_validates_existence_and_freeze PASSED [ 18%]
框架/backend/tests/test_candidate_backtest_runner.py::TestCandidateBacktestRunnerP0::test_p0_6_holdout_sealed_blocks_execution_when_window_crosses PASSED [ 20%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_01_compute_metrics_double_bucket_fields PASSED [ 21%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_02_recompute_metrics_double_bucket_compatibility PASSED [ 23%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_03_funding_quality_gate_degrades_on_hypothetical PASSED [ 25%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_04_oi_causal_boundary_future_bucket_blocked PASSED [ 27%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_05_pit_unproven_not_upgraded_by_bucket_end PASSED [ 29%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_06_backfill_data_must_remain_pit_unproven PASSED [ 30%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_07_scenario_a_only_hypothetical_funding PASSED [ 32%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_08_scenario_b_only_pit_unproven PASSED [ 34%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_09_scenario_c_pit_unproven_and_hypothetical_funding_combination PASSED [ 36%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_10_f1_negative_hypothetical_funding_rebate PASSED [ 38%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_11_f2_offsetting_net_zero_hypothetical_funding PASSED [ 40%]
框架/backend/tests/test_funding_runner_isolation_01.py::test_12_f3_negative_funding_with_pit_unproven PASSED [ 41%]
框架/backend/tests/test_run_provenance_46.py::T00_基线::test_历史无_run_json仍可安全读取 PASSED [ 43%]
框架/backend/tests/test_run_provenance_46.py::T00_基线::test_可信记录被识别为_VERIFIED_TRUSTED PASSED [ 45%]
框架/backend/tests/test_run_provenance_46.py::T00_基线::test_拒绝记录不会变成成功运行 PASSED [ 47%]
框架/backend/tests/test_run_provenance_46.py::T00_基线::test_校验不写盘 PASSED [ 49%]
框架/backend/tests/test_run_provenance_46.py::T00_基线::test_研究运行不得冒充_TRUSTED PASSED [ 50%]
框架/backend/tests/test_run_provenance_46.py::T01_攻击一_复制门禁块到另一个运行::test_A1a_直接手写run_json带别人的data_gate PASSED [ 52%]
框架/backend/tests/test_run_provenance_46.py::T01_攻击一_复制门禁块到另一个运行::test_A1b_在已背书运行上注入别人的data_gate PASSED [ 54%]
框架/backend/tests/test_run_provenance_46.py::T02_攻击二_保留输入摘要改收益::test_改_metrics_被拒 PASSED [ 56%]
框架/backend/tests/test_run_provenance_46.py::T03_攻击三_改_run_id_后重新登记::test_外部补登记恒为_UNVERIFIED PASSED [ 58%]
框架/backend/tests/test_run_provenance_46.py::T03_攻击三_改_run_id_后重新登记::test_干净补登记为_UNVERIFIED PASSED [ 60%]
框架/backend/tests/test_run_provenance_46.py::T03_攻击三_改_run_id_后重新登记::test_直接改run_json里的run_id被拒 PASSED [ 61%]
框架/backend/tests/test_run_provenance_46.py::T04_攻击四_直接写claim_level::test_在已背书运行上改claim_level被拒 PASSED [ 63%]
框架/backend/tests/test_run_provenance_46.py::T04_攻击四_直接写claim_level::test_手写TRUSTED被拒 PASSED [ 65%]
框架/backend/tests/test_run_provenance_46.py::T05_攻击五_删除门禁字段::test_删掉data_gate被拒 PASSED [ 67%]
框架/backend/tests/test_run_provenance_46.py::T06_攻击六_伪造PIT_PROVEN::test_已背书运行上改pit_status被拒 PASSED [ 69%]
框架/backend/tests/test_run_provenance_46.py::T06_攻击六_伪造PIT_PROVEN::test_手写pit_status被拒 PASSED [ 70%]
框架/backend/tests/test_run_provenance_46.py::T07_攻击七_改配置留旧门禁证明::test_改config被拒 PASSED [ 72%]
框架/backend/tests/test_run_provenance_46.py::T07_攻击七_改配置留旧门禁证明::test_改spec被拒 PASSED [ 74%]
框架/backend/tests/test_run_provenance_46.py::T08_攻击八_给历史运行补签::test_历史运行调set_status也拿不到背书 PASSED [ 76%]
框架/backend/tests/test_run_provenance_46.py::T08_攻击八_给历史运行补签::test_搬运别人的provenance块被拒 PASSED [ 78%]
框架/backend/tests/test_run_provenance_46.py::T09_回执与链::test_删门禁回执后TRUSTED降级为REJECTED PASSED [ 80%]
框架/backend/tests/test_run_provenance_46.py::T09_回执与链::test_回执不能跨run搬运 PASSED [ 81%]
框架/backend/tests/test_run_provenance_46.py::T09_回执与链::test_换密钥后旧背书被拒 PASSED [ 83%]
框架/backend/tests/test_run_provenance_46.py::T09_回执与链::test_链中间被改即断 PASSED [ 85%]
框架/backend/tests/test_run_provenance_46.py::T09_回执与链::test_链尾被删即拒 PASSED [ 87%]
框架/backend/tests/test_run_provenance_46.py::T10_口径无回归::test_seal失败不影响创建 PASSED [ 89%]
框架/backend/tests/test_run_provenance_46.py::T10_口径无回归::test_会计字段不被溯源改动 PASSED [ 90%]
框架/backend/tests/test_run_provenance_46.py::T10_口径无回归::test_非终态不产生背书 PASSED [ 92%]
框架/backend/tests/test_run_provenance_46.py::T11_损坏证明::test_data_gate写成列表不崩 PASSED [ 94%]
框架/backend/tests/test_run_provenance_46.py::T11_损坏证明::test_payload写成字符串被拒 PASSED [ 96%]
框架/backend/tests/test_run_provenance_46.py::T11_损坏证明::test_provenance写成字符串被拒 PASSED [ 98%]
框架/backend/tests/test_run_provenance_46.py::T11_损坏证明::test_校验器对任意畸形doc都不抛 PASSED [100%]

============================== 55 passed in 1.16s ==============================
```

---

## 三、交付文件与物理原件清单

依据总控要求，交付原始 `.py` 文件、针对实际父提交 `b534b306` 的有效 diff、测试日志及校验单：

| 交付文件相对路径 | 物理形态 | 内容说明 |
| :--- | :--- | :--- |
| `框架/backend/candidate_backtest_runner.py` | 原始 Python 原件 | 候选回测执行入口（写前强验、空凭据防覆盖、组合绕过阻断、持久化中断恢复） |
| `框架/backend/tests/test_candidate_backtest_runner.py` | 原始 Python 测试原件 | 六项 P0 专项自动化测试套件（11 项用例） |
| `evidence/06-B-BACKTEST-PREP-01/candidate_backtest_runner_fix.diff` | 实际 Git Diff | 本轮收敛修复相对于父提交 `b534b306` 的全部代码与测试差异（反向应用验证通过） |
| `evidence/06-B-BACKTEST-PREP-01/test_run.log` | 测试运行日志 | 55 项定点测试全绿执行日志原件（已强制跟踪入库） |
| `evidence/06-B-BACKTEST-PREP-01/01_REUSE_AND_DIFF_MANIFEST.md` | 文档交付物 1 | 复用与变更清单 |
| `evidence/06-B-BACKTEST-PREP-01/02_CANDIDATE_RUNNER_INTERFACE_GUIDE.md` | 文档交付物 2 | 候选调用说明与接口规范 |
| `evidence/06-B-BACKTEST-PREP-01/03_ACCEPTANCE_RECEIPT_AND_VERIFICATION.md` | 文档交付物 3 | 本验收回执与自测报告 |
| `evidence/06-B-BACKTEST-PREP-01/SHA256SUMS.txt` | 校验和单 | 全套交付原件哈希签名清单（`shasum -c` 100% 通过） |

---

## 四、停手声明

06-B 回测程序准备工单原缺口已全面收敛闭环，55 项定点测试 100% PASS（11+12+32）。
已更正回执分布、记录父提交 `b534b306`、补交物理日志 `test_run.log` 并通过 diff 反向验证。
继续保持本单独立工作树，严格保护旧工作区与已验收数据，立即停手，不自动调参，不打开最终盲测，不合并 main，交付总控 Codex 独立复核。
