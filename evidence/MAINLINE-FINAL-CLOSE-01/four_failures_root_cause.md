# 4 项策略测试失败 · 逐项定性

- 订单：`MAINLINE-FINAL-CLOSE-01`
- 被测基线：`wyckoff-v3.1-main` HEAD `26ec8aef`
- 测试命令：`/usr/bin/python3 -m pytest 策略/WyckoffMaster/event_trend_v0_1/tests/ -q -p no:cacheprovider`（每次用全新空 `TMPDIR`）
- 修复后结果：**175 passed / 0 failed / 0 skipped**（修复前 171 passed / 4 failed）
- 冻结参数文件：`策略/WyckoffMaster/event_trend_v0_1/config.py`，SHA256 `122683f2b489c742dd594ff465e916147dd097bd1afcb8a35cb4116c0eedeb46`（**本轮未改动**，改前改后一致）
- 冻结规则文档：`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md`，SHA256 `1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab`，13,315 字节（**本轮未改动**）

**结论：4 项全部属 A 类（旧测试期望与冻结规则不一致），无一项是生产实现缺陷。**
**没有修改任何生产代码，没有修改任何冻结参数，没有放宽任何断言。**

---

## 失败 1 · `test_event_trend_closure.py::test_adversarial_candidate_unique_binding_and_field_mapping`

| 项 | 内容 |
|---|---|
| 完整失败信息 | `AssertionError: assert 0.9084680556108683 < 1e-09`<br>`abs((1.7953321364452446 - 0.8868640808343763))`<br>位于 `test_event_trend_closure.py:359` |
| 测试名 | `test_adversarial_candidate_unique_binding_and_field_mapping`（测试项 4：多候选唯一关联、未来候选不成交、真实字段读取、真实 order_id 贯穿） |
| 失败前版本 | 该断言在 `f05b6d63` 基线同样是失败（上一单 `MAINLINE-ALL-IN-01` 已记录同一断言文本），非本轮引入 |
| 失败后版本 | 本轮修复后通过 |
| 对应冻结规则 | FT-LOW4H：`stoploss_s = 0.05`（固定 5% 初始止损）、`risk_budget_per_trade = 0.005`、`initial_equity = 2000`、`max_notional_per_position = 0.30` |
| 测试期望 | 期望手数 `0.8868640808343763`，算法为 `initial_equity × risk_budget_per_trade ÷ (|111.24 − 100.1×(1−3bps)| + (111.24 + 100.1×(1−3bps))×5bps)` —— 即**用候选快照里的 `initial_stop_price=100.1` 当止损**，并把双边手续费算进单位风险 |
| 实际行为 | 实际手数 `1.7953321364452446`，算法为 `2000×0.005 ÷ (111.24 − 105.67)` = `10 ÷ 5.57` |
| 差异来源 | 两处：① 止损来源不同（旧规则取 `cand.initial_stop_price`；FT-LOW4H 取「实际入场价×0.95」）；② 分母不同（旧规则含手续费；FT-LOW4H 只用止损距离，手续费是成本不是风险） |
| 根因分类 | **A**（旧测试期望与冻结规则不一致） |
| 是否需改生产代码 | **否**。生产路径 `engine_adapter.EventTrendExecutionPolicy.decide_entry` 的 FT-LOW4H 分支：`stop_px = 入场价×(1−stoploss_s)` 向下取整到 tick，`target_qty = 权益×risk_fraction ÷ stop_dist`，与冻结规则一致 |
| 可否在不改策略语义下修测试 | **可以**。把期望值改为按冻结规则推导，断言严格度不变（仍 `abs(...) < 1e-9`），并新增 `_stop_px == 105.67` 这一锚点断言，防止以后再次漂移 |
| 修复方式 | 期望公式改为 `initial_equity × risk_budget_per_trade ÷ (111.24 − floor_tick(111.24×0.95))`，用 `Decimal` 显式做向下取整到 `0.01` |

---

## 失败 2 · `test_event_trend_closure.py::test_max_holding_10_days_expiry_exit`

| 项 | 内容 |
|---|---|
| 完整失败信息 | `AssertionError: assert 'force_close_eod' == 'max_holding_expired'`<br>位于 `test_event_trend_closure.py:622` |
| 测试名 | `test_max_holding_10_days_expiry_exit`（测试项 7：最长持仓 10 个自然日，到期按下一根 15m 开盘退出） |
| 失败前版本 | `f05b6d63` 基线同样失败，非本轮引入 |
| 失败后版本 | 本轮修复后通过 |
| 对应冻结规则 | FT-LOW4H：`max_holding_days = None`，`config.py:87` 原注释「用户决定：取消 10 天自动平仓」 |
| 测试期望 | 到期退出，`exit_reason == "max_holding_expired"`，`exit_time_ms == t_exec + 10 天`，持仓 240.0 小时 |
| 实际行为 | 持仓 264.0 小时，`exit_reason == "force_close_eod"`（段末强平）。因为 `max_holding_ms=None`，到期判据根本不成立 |
| 差异来源 | 冻结规则已由用户取消 10 天自动平仓；测试仍按旧规则要求到期退出 |
| 根因分类 | **A** |
| 是否需改生产代码 | **否**。到期退出机制本身完好：本轮实测显式传 `EventTrendConfig(max_holding_days=10)` 时，`exit_reason=max_holding_expired`、`exit_time_ms` 恰为 `t_exec+10 天`、`holding_duration_hours=240.0` |
| 可否在不改策略语义下修测试 | **可以**。拆成两段：① 冻结默认下**不得到期平仓**（钉住冻结规则）；② 显式覆盖 10 天时机制仍生效（保留机制覆盖）。两段都只在本用例内传配置，不改 `config.py` 默认值 |
| 修复方式 | 拆两段断言，见上 |

---

## 失败 3 · `test_fill_precision.py::test_exit_failure_keeps_position_open_and_marks_run_invalid`

| 项 | 内容 |
|---|---|
| 完整失败信息 | `AssertionError: 出场失败时仓位必须仍未平` / `assert []`<br>位于 `test_fill_precision.py:343` |
| 测试名 | `test_exit_failure_keeps_position_open_and_marks_run_invalid`（测试项 7：出场失败不得把仓位当作已平仓） |
| 失败前版本 | `f05b6d63` 基线同样失败；上一单未能定性，本轮追到根因 |
| 失败后版本 | 本轮修复后通过 |
| 对应冻结规则 | FT-LOW4H：初始止损 = 实际入场价×(1−0.05)，**不再使用候选快照的 `initial_stop_price`** |
| 测试期望 | 入场成功（0.004 向上取整成 0.01），出场取整成 0.00 被拒 ⇒ 持仓仍在、`run_valid=False`、`unclosed_positions` 非空 |
| 实际行为 | `trades_count=0`、`open_positions_remaining=[]`、`run_valid=True`、`rejected_orders=[{"phase":"C_entry","reason":"invalid_signal_stop"}]` |
| 差异来源 | 场景在冻结规则下**不可达**：入场价 0.004 向上取整为 0.01 ⇒ 止损 = 0.01×0.95 = 0.0095 ⇒ 向下取整成 0.00 ⇒ 止损距离退化为 0 ⇒ **入场就被拒**，根本没有持仓。因此「持仓为空」是正确结果，不是「把仓位当成已平仓」 |
| 根因分类 | **A**（原用例用旧规则的 25% 止损构造场景；冻结规则改成 5% 后，该价格无法表达止损） |
| 是否需改生产代码 | **否**。`invalid_signal_stop` 在 `trading_core/rejects.py` 中明确登记为 `strategy_cancel`（策略主动不做），不判运行无效；入场从未发生，账本无副作用（`final_cash` 停在 2000） |
| 可否在不改策略语义下修测试 | **可以**。换一个可达场景，断言一字不改 |
| 修复方式 | 入场价改 0.019（取整后 0.02，止损 0.019→0.01，止损距离 0.01 可表达），入场后第 4 根 15m bar 起跳空到 0.004（卖方向取整成 0.00）。实测：`trades_count=0`、`open_positions_remaining=['SOLUSDT']`、`run_valid=False`、`invalid_reasons` 含 `exit_gap_fill_rejected` / `exit_intrabar_fill_rejected` / `exit_eod_fill_rejected`、`unclosed_positions=['SOLUSDT']`、`cash_identity_ok=True` |

---

## 失败 4 · `test_run_artifacts.py::test_fingerprints_cover_rules_code_config_inputs`

| 项 | 内容 |
|---|---|
| 完整失败信息 | `AssertionError: assert None == 10`<br>位于 `test_run_artifacts.py:188` |
| 测试名 | `test_fingerprints_cover_rules_code_config_inputs`（指纹四件套齐备且可复算） |
| 失败前版本 | `f05b6d63` 基线同样失败，非本轮引入 |
| 失败后版本 | 本轮修复后通过 |
| 对应冻结规则 | FT-LOW4H：`max_holding_days = None` |
| 测试期望 | 配置指纹里 `params["max_holding_days"] == 10` |
| 实际行为 | `params["max_holding_days"] is None` |
| 差异来源 | 期望值写死为旧规则的 10；冻结规则已取消 10 天自动平仓 |
| 根因分类 | **A** |
| 是否需改生产代码 | **否**。若按测试要求把 `max_holding_days` 改回 10，等于改冻结参数，并且会连带改变 `config_fingerprint` 的 `sha256`（配置身份漂移），明令禁止 |
| 可否在不改策略语义下修测试 | **可以**。本用例要验的是「指纹覆盖配置参数且可复算」，把值改成 `is None` 后该性质不变；同一用例里 `rules["sha256"] == 1f1167de…` 与 `max_participation… == 0.001` 两条硬断言原样保留 |
| 修复方式 | `assert cfg_fp["params"]["max_holding_days"] is None`，并在注释里写明「若仍写 10 就等于要求改冻结参数」 |

---

## 汇总

| # | 用例 | 根因分类 | 改生产代码 | 修测试 | 修复后 |
|---|---|---|---|---|---|
| 1 | `test_adversarial_candidate_unique_binding_and_field_mapping` | A | 否 | 是 | PASS |
| 2 | `test_max_holding_10_days_expiry_exit` | A | 否 | 是（拆两段） | PASS |
| 3 | `test_exit_failure_keeps_position_open_and_marks_run_invalid` | A | 否 | 是（换可达场景） | PASS |
| 4 | `test_fingerprints_cover_rules_code_config_inputs` | A | 否 | 是 | PASS |

- B 类（生产实现缺陷）：**0 项**
- C 类（夹具/环境）：**0 项**
- D 类（证据不足）：**0 项**
- 冻结参数、PIT 门槛：**0 处改动**
- 断言放宽：**0 处**（4 处断言全部保持原严格度，其中 2 处还新增了锚点断言）
