# 06-B 回测程序准备｜必要验证证据与验收回执 (反例收敛修复版)

- **工单编号**：06-B-BACKTEST-PREP-01（反例收敛修复）
- **执行时间**：2026-11-11（北京时间）
- **执行角色**：反重力（独立测试与回测程序准备）
- **独立验收方**：总控 Codex
- **业务基线提交**：`8bef22b0`
- **治理依据提交**：`6cf7edf4d5956f4e8056351faecbd0cdd8eb8c16`
- **测试环境**：macOS (Darwin 24.6.0) / Python 3.9.6 (pytest 8.4.2) / Python 3.13.12 兼容
- **纪律声明**：**依总控裁决，撤回前序“ALL P0 CRITERIA VERIFIED”声明，原 3 项交付、6 项 P0 分母保持不变，按反例收敛逐项报告自测结论，交独立复核。**

---

## 一、六项 P0 验收执行回执与反例收敛说明

| 验收项 | 验收判据 | 实测用例与反例收敛验证 | 实际运行输出 / 证据 | 自测结论 |
| :--- | :--- | :--- | :--- | :---: |
| **P0-1** | **输入绑定固定开发快照及实际代码、配置版本；原始数据保持只读；统一覆盖资金费；凭据损坏直接拒绝** | `test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created`<br>`test_p0_1_input_validation_requires_funding_and_rejects_corrupted_manifest` | 1. 内存快照、外部目录与只读库统一校验完整输入身份，内存与外部快照缺失资金费（`funding_events`）直接抛错拒绝；<br>2. 首次登记落盘不可变 `inputs_manifest.json`；复用快照时凭据损坏直接抛错拒绝，**绝不只警告后继续**。原始数据仅只读连接。 | **PASS (收敛闭环)** |
| **P0-2** | **历史时钟正确限制可见数据（剔除未收盘与未来数据）；严格与近似 PIT 区分** | `test_p0_2_history_clock_filters_unclosed_and_future_bars`<br>`test_p0_2_strict_pit_mode_blocks_when_available_at_missing` | 注入未收盘（`is_closed=0`）与未来时间戳 K 线被自动剔除；`pit_mode="strict"` 时缺失 `available_at` 直接阻断；`"approximate"` 模式如实记录 `PIT_APPROXIMATE` 与 `degraded`。 | **PASS** |
| **P0-3** | **信号与成交顺序符合既有保守规则；同根止损与歧义资金费进入假设桶，不美化结果** | `test_p0_3_conservative_execution_and_funding_ambiguity_bucket`<br>+ `test_funding_runner_isolation_01.py` 12 项 | 相位 A/B/C/D 顺序推进；同根开仓盘中触发止损；歧义资金费事件落入 `total_funding_fee_hypothetical`，不进已确认现金账本。 | **PASS** |
| **P0-4** | **成交、手续费、资金费、现金、持仓和未实现盈亏能够对账；缺失费用数据不静默按零** | `test_p0_4_ledger_recompute_and_reconciliation`<br>+ `recompute_metrics_from_ledger` | 独立从 `ledger.sqlite3` 重新聚合计算净收益、胜率、回撤、现金恒等式，与引擎指标完全吻合；缺失费用数据标记 `funding_ok=False`。 | **PASS** |
| **P0-5** | **重复事件与中断恢复不重复记账；同run_id防漂移；冲突在set_status(RUNNING)前拒绝且旧状态/快照/账本不变；受控中断恢复无重复无遗漏** | `test_p0_5_manual_dirty_residue_cleanup_on_recovery`<br>`test_p0_5_same_run_id_rejects_altered_inputs_or_spec_before_running`<br>`test_p0_5_controlled_interruption_recovery_and_reconciliation` | 1. **规范化配置比对**：正确处理缺省值、新增字段、exchange/market，同 run_id 规则或配置漂移直接拒绝；<br>2. **防覆盖**：不同内容外部快照即使自带有效 manifest，也严禁覆盖旧 run 绑定身份；<br>3. **写前拒绝与断言**：在 `set_status(RUNNING)` 前拒绝，断言拒绝后旧成功状态、快照和账本均保持不变；<br>4. **手工残留行清理**：准确命名保留；<br>5. **受控中断恢复**：原固定小样本在执行中发生真实受控崩溃后，再次恢复重算，对照逐笔交易、费用、资金费和最终账本完全一致，无重复、无遗漏，状态为 `SUCCEEDED`。 | **PASS (收敛闭环)** |
| **P0-6** | **回测不写运行库、不污染运行队列、不调外部下单；最终盲测区间保持封闭；真实实验注册门禁生效** | `test_p0_6_experiment_registry_gate_validates_existence_and_freeze`<br>`test_p0_6_holdout_sealed_blocks_execution_when_window_crosses` | 1. 运行产物严格限定在独占隔离目录；时间窗口触及盲测保护区间（>= 2024-10-01）抛出 `HoldoutSealedError` 强阻断；<br>2. **复用真实实验注册门禁**：检查实验存在性、冻结状态与批准开发范围；不存在的非空 ID 必须拒绝；正式候选不能靠省略 `is_formal` 或传 `holdout_sealed=False` 绕过；机制测试走隔离测试通道。 | **PASS (收敛闭环)** |

---

## 二、实际自动化测试套件执行证据

### 1. 06-B 专项六项 P0 修复测试（11 passed in 0.75s）

```text
$ cd /Users/boos/Desktop/task_06b_p0_fix_worktree
$ PYTHONPATH=框架/backend /usr/bin/python3 -m unittest 框架/backend/tests/test_candidate_backtest_runner.py -v
test_p0_1_input_bound_to_fixed_snapshot_and_manifest_created ... ok
test_p0_1_input_validation_requires_funding_and_rejects_corrupted_manifest ... ok
test_p0_2_history_clock_filters_unclosed_and_future_bars ... ok
test_p0_2_strict_pit_mode_blocks_when_available_at_missing ... ok
test_p0_3_conservative_execution_and_funding_ambiguity_bucket ... ok
test_p0_4_ledger_recompute_and_reconciliation ... ok
test_p0_5_controlled_interruption_recovery_and_reconciliation ... ok
test_p0_5_manual_dirty_residue_cleanup_on_recovery ... ok
test_p0_5_same_run_id_rejects_altered_inputs_or_spec_before_running ... ok
test_p0_6_experiment_registry_gate_validates_existence_and_freeze ... ok
test_p0_6_holdout_sealed_blocks_execution_when_window_crosses ... ok

----------------------------------------------------------------------
Ran 11 tests in 0.754s

OK
```

### 2. 直接受影响的定点测试套件（55 passed in 1.21s）

包含六项 P0 专项（11 项）、资金费双口径隔离单测（12 项）与运行身份溯源防篡改单测（32 项），真实日志落盘于 `evidence/06-B-BACKTEST-PREP-01/test_run.log`：

```text
$ PYTHONPATH=框架/backend /usr/bin/python3 -m pytest \
    框架/backend/tests/test_candidate_backtest_runner.py \
    框架/backend/tests/test_funding_runner_isolation_01.py \
    框架/backend/tests/test_run_provenance_46.py -v

============================== 55 passed in 1.21s ==============================
```

---

## 三、交付文件与物理原件清单

依据总控要求，交付原始 `.py` 文件、实际 diff、必要日志及校验单：

| 交付文件相对路径 | 物理形态 | SHA256 校验和 | 内容说明 |
| :--- | :--- | :--- | :--- |
| `框架/backend/candidate_backtest_runner.py` | 原始 Python 原件 | 见 SHA256SUMS.txt | 候选回测执行入口（含输入强验、规范化防覆盖、写前拒绝、真实实验门禁、受控中断恢复） |
| `框架/backend/tests/test_candidate_backtest_runner.py` | 原始 Python 测试原件 | 见 SHA256SUMS.txt | 六项 P0 专项自动化测试套件（11 项用例） |
| `evidence/06-B-BACKTEST-PREP-01/candidate_backtest_runner_fix.diff` | 实际 Git Diff | 见 SHA256SUMS.txt | 本轮收敛修复的全部实际代码与测试差异 |
| `evidence/06-B-BACKTEST-PREP-01/test_run.log` | 测试运行日志 | 见 SHA256SUMS.txt | 55 项定点测试全绿执行日志原件 |
| `evidence/06-B-BACKTEST-PREP-01/01_REUSE_AND_DIFF_MANIFEST.md` | 文档交付物 1 | 见 SHA256SUMS.txt | 复用与变更清单 |
| `evidence/06-B-BACKTEST-PREP-01/02_CANDIDATE_RUNNER_INTERFACE_GUIDE.md` | 文档交付物 2 | 见 SHA256SUMS.txt | 候选调用说明与接口规范 |
| `evidence/06-B-BACKTEST-PREP-01/03_ACCEPTANCE_RECEIPT_AND_VERIFICATION.md` | 文档交付物 3 | 见 SHA256SUMS.txt | 本验收回执与自测报告 |
| `evidence/06-B-BACKTEST-PREP-01/SHA256SUMS.txt` | 校验和单 | - | 全套交付原件哈希签名清单 |

---

## 四、停手声明

06-B 回测程序准备工单原 3 项交付物、6 项 P0 缺口已按反例全面收敛修复，55 项定点测试 100% PASS。
已撤回统一通过声明，原件代码、实际 diff、测试日志与校验单齐备。
继续保持本单独立工作树，严格保护旧工作区与已验收数据，立即停手，不自动调参，不打开最终盲测，不合并 main，交付总控 Codex 独立复核。
