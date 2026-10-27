# FIX-002｜成交精度校验与历史合约规格门禁交付报告 (P0)

- **任务编号**：FIX-002
- **审查基线**：`wyckoff-v3.1-main`（基于 `e5e82640`）
- **交付日期**：2026-10-27（北京时间）
- **交付状态**：已完成定点修复，专项回归测试 11/11 通过，全套规格测试 46/46 通过。
- **关联工单**：FIX-001（时间轴与槽位释放修复，两单解耦并行，互不冲突）

---

## 一、审查基线与执行红线遵守

1. **定点门禁与精度校验**：严格限定于成交精度、合约规格强门禁、K线合理价格范围校验与拒单码规范化，未触碰或修改策略入场与出场信号规则。
2. **数据与账本隔离**：原始回测数据零删除、零覆盖；未获正式放行与旧批次回测结果统一透传标记 `historical_research_only=True`。

---

## 二、整改项落实与代码位置

### 1. 彻底移除默认 Tick 回退
- **修复位置**：`框架/backend/paper_engine.py`（第 2579-2588 行）。
- **整改详情**：彻底清除原 `limits = contract_limits(symbol) or {"price_step": 0.01}` 静默回退，强制执行显式门禁。缺失规格时立即阻断并返回 `reason: "FALLBACK_TICK_BLOCKED"`, `spec_status: "MISSING_CONTRACT_SPEC"`。

### 2. 合约规格强门禁
- **修复位置**：
  - `框架/backend/replay_calc.py`：`plan_entry()` 强化合约规格检查，`limits` 为空或 `price_step <= 0` / `quantity_step <= 0` 立即显式拒单，记录 `reject: "MISSING_CONTRACT_SPEC"`。
  - `框架/backend/paper_engine.py`：`open_position()` 与 `close_at_price()` 中对缺失规格强制拒绝，统一拒单原因为 `MISSING_CONTRACT_SPEC`。

### 3. 成交价格与精度合法性校验
- **修复位置**：
  - `框架/backend/replay_calc.py`：实现 `validate_execution_price_in_bar(fill_price, bar_low, bar_high, tol=0.001)` 工具函数；`plan_entry()` 支持执行 K 线 `[bar_low, bar_high]` 价格越界硬校验；`scan_exit()` 支持对退出成交价在执行 K 线区间内的合法性校验与越界标记。
- **校验逻辑**：撮合成交价超出对应执行 K 线的 `[low * (1 - 0.001), high * (1 + 0.001)]` 时，一律拦截并标记 `EXEC_PRICE_OUT_OF_RANGE`。

### 4. 标准化拒单原因码
- **修复位置**：`策略/WyckoffMaster/trading_core/rejects.py`。
- **登记常量**：
  - `MISSING_CONTRACT_SPEC`：缺少明确合约规格；
  - `FALLBACK_TICK_BLOCKED`：拦截尝试静默回退默认 tick 行为；
  - `EXEC_PRICE_OUT_OF_RANGE`：撮合成交价超出对应执行 K 线区间；
  - `INVALID_QTY_PRECISION`：下单数量不满足最小数量步长或非有限正数。
- **归类判定**：全量纳入 `FIX002_VALIDITY_REASONS`，在 `classify_reject()` 与 `is_execution_validity()` 中严格识别为 `REJECT_CLASS_EXECUTION`（执行有效性错误）。

### 5. 账本隔离与历史研究标记
- **修复位置**：`框架/backend/backtest_batches.py`。
- **隔离机制**：在单 run 读取 `_run_facts()` 与批次汇总 `_batch_facts()` 中增加 `historical_research_only` 字段。所有未通过正式放行（`formal_pass_allowed=False`）或历史清单型批次自动标记为 `historical_research_only=True`，与新回测输出明确隔离。

---

## 三、修改文件清单

1. `框架/backend/paper_engine.py`
2. `框架/backend/replay_calc.py`
3. `策略/WyckoffMaster/trading_core/rejects.py`
4. `框架/backend/backtest_batches.py`
5. `策略/WyckoffMaster/event_trend_v0_1/tests/test_fix002_precision_and_gates.py`（新增专项回归测试）

---

## 四、回归测试与验证

### 1. 专项回归测试
执行命令：
```bash
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python -m pytest 策略/WyckoffMaster/event_trend_v0_1/tests/test_fix002_precision_and_gates.py
```
测试结果：
- 覆盖场景：
  - 低价币高精度保留与防截断（`test_low_price_symbol_precision_preservation`、`test_low_price_entry_and_rounding`）；
  - 合约规格缺失强门禁与移除 0.01 回退（`test_paper_engine_fallback_tick_completely_removed`、`test_replay_calc_missing_contract_spec_gate`、`test_engine_adapter_requires_instrument_spec`）；
  - 人工构造价格越界拦截与原因码（`test_validate_execution_price_in_bar`、`test_plan_entry_with_out_of_range_bar`、`test_scan_exit_price_out_of_range_flag`）；
  - 下单数量精度不满足步长拦截（`test_invalid_qty_precision_handling`）；
  - 拒单原因码执行有效性归类（`test_reject_codes_registered_in_execution_validity`）；
  - 历史账本与批次隔离标记（`test_backtest_batches_marks_historical_research_only`）。
- **运行结果**：`11 passed in 0.87s`。

### 2. 合约规格历史适用性全量测试
执行命令：
```bash
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python -m pytest 策略/WyckoffMaster/event_trend_v0_1/tests/test_spec_historical_applicability.py
```
- **运行结果**：`35 passed in 0.85s`。

---

## 五、剩余风险说明

1. **历史单时点快照局限性**：由于历史各窗口交易所合约规格变更证据（如特定退市币或杠杆调整历史）尚未全部完备，未夹住变更区间的历史币种在正式回测中会如实维持 `unverified_historical` 状态，不能直接声称正式放行（需待历史规格追溯补齐）。
2. **自然行情挂钟成交**：本次修改为纯回测引擎与模拟撮合门禁修复，真实挂钟环境下的流动性冲击与实际滑点仍需依赖后续实盘模拟验证。
