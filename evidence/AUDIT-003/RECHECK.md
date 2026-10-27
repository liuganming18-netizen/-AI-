# [AUDIT-003-RECHECK] 针对 Work 补丁（提交 d26f33fb）的二次只读复核报告

- **对比基线**：`38eaa83b` $\rightarrow$ `d26f33fb`（分支 `wyckoff-v3.1-main`）
- **审计角色**：主代理 / 独立只读复核方
- **执行原则**：基于真实调用链与实际运行结果取证，未修改任何工程文件

---

## 总体复核结论：【AUDIT-003 代码级缺陷已全量修复通过，移交 GATE-004 前需用户确认 DEC-01 裁决】

- **代码与门禁判定**：
  1. **BLK-02（价格校验被绕过）彻底修复**：`paper_engine.open_position`、`close_at_price` 及全部 6 个真实撮合调用点已全量接入 K 线真实 `[low, high]` 强门禁，`plan_entry` 剔除默认可选参数，全链路实现 100% fail-closed 拦截。
  2. **BLK-01（退出异常带病出报告）彻底修复**：引入统一运行状态机 `run_state`。退出推进发生未捕获异常或平仓被拒时，`run_status` 强制置为 `FAILED`，跑分器与生产绩效入口 `compute_metrics` **100% 阻断收益率、胜率、回撤等绩效输出（置为 `None`）**，彻底杜绝带病出成绩。
- **GATE-004 移交前置条件**：
  Work 在修复价格门禁后，暴露了现行占位配置中 `exit.gap_model="stop_price"` 物理不可成交的本质（跳空时按虚高止损价成交，现已被门禁精准拦截并判 FAILED）。**需用户对 `exit.gap_model`（建议裁决为真实可成交的 `bar_open`）完成确认后，即可正式移交 Codex 执行 GATE-004 最终验收**。

---

## 一、核对 1：撮合与单事件回放调用点 K 线参数与区间校验 100% 生效核对

### 1. 核心撮合函数底层防护核验

1. **开仓校验：`paper_engine.open_position()`（行 1236-1254）**
   - **执行时机**：在写入任何数据库表（`paper_position` / `paper_order`）之前执行。
   - **双重硬门禁**：
     - 调用 `require_bar_range(_bar_low, _bar_high)`：若当前成交 K 线缺少 `low` 或 `high`（或非有限正数），立即拒单并返回 `MISSING_BAR_RANGE`。
     - 调用 `validate_execution_price_in_bar(fill, _bar_low, _bar_high)`：校验真正落账的成交价 `fill`（含滑点与步进取整）是否落在 `[low, high]`（含 0.1% 容差）内；越界立即通过 `_reject` 记录拒单并返回 `EXEC_PRICE_OUT_OF_RANGE`。
2. **平仓校验：`paper_engine.close_at_price()`（行 1788-1855）**
   - **函数签名重构**：新增必填项 `bar_low=None, bar_high=None`。
   - **零副作用拦截**：在建立数据库连接前，最优先调用 `require_bar_range(bar_low, bar_high)`，缺省即拒单并返回 `MISSING_BAR_RANGE`。
   - **真实成交价校验**：对最终成交价 `fill`（经价格步进取整及不利滑点）执行 `validate_execution_price_in_bar`，越界返回 `EXEC_PRICE_OUT_OF_RANGE`，**仓位保持 `open`，不写任何账本表**。
3. **入场计划：`replay_calc.plan_entry()`（行 198-230）**
   - **参数去默认值**：签名中的 `bar_low: float, bar_high: float` 去掉了 `Optional = None` 默认值。调用方漏传将直接在 Python 解释器层报 `TypeError`。
   - **无条件校验**：移除旧代码中的 `if bar_low is not None:` 条件分支，改为入口无条件调用 `require_bar_range` 与 `validate_execution_price_in_bar`。

### 2. 全局正式成交与回放调用点穷举追踪

| 业务调用链 | 调用点文件与行号 | 传入的 K 线边界参数 | 校验生效方式 | 审计核验状态 |
|---|---|---|---|---|
| **组合回测退出平仓** | `accum_early_entry.py:1860` | `bar_low=float(bar["low"])`<br>`bar_high=float(bar["high"])` | 强制校验，平仓被拒时不落账且标记失败 | **已确认生效** |
| **实时 Worker 退出** | `live_worker.py:440` | `bar_low=b_lo`<br>`bar_high=b_hi` | 出场 15m K 线高低价缺失直接拒单 | **已确认生效** |
| **手工/立即平仓** | `paper_engine.py:1778` (`close_position`) | `bar_low=_f(row["low"])`<br>`bar_high=_f(row["high"])` | 收盘执行 K 线的真实高低价接入 | **已确认生效** |
| **回撤熔断强平** | `paper_engine.py:2084` (`enforce_drawdown`) | `bar_low=_f(nxt["low"])`<br>`bar_high=_f(nxt["high"])` | 下一根 15m K 线的真实高低价接入 | **已确认生效** |
| **K 线逐根判退出** | `paper_engine.py:2539` (`check_position_exit_on_bar`) | `bar_low=low_p`<br>`bar_high=float(bar_detail["high"])` | 盘中停损价格对照本根 K 线高低价校验 | **已确认生效** |
| **单事件回放开仓** | `replay_calc.py:686` (`replay_one`) | `bar_low=entry_bar.get("low")`<br>`bar_high=entry_bar.get("high")` | 补齐参数，越界直接拒单返回 `rejected` | **已确认生效** |
| **单事件回放退出** | `replay_calc.py:740` (`replay_one`) | `scan_exit` 内部真实 `[low, high]` | 退出价越界则整条回放判为 `invalid_exit_price`，净额置 `None` | **已确认生效** |

- **核查结论**：全工程所有开仓与平仓路径已无任何静默旁路，K 线高低价校验达到 **100% 全覆盖**。

---

## 二、核对 2：退出推进异常处理逻辑及异常注入测试核查

### 1. 运行状态阻断架构
1. **状态机沉淀**：`accum_early_entry.py:1750-1785` 新增 `run_state`（包含 `status="OK"|"FAILED"`、`invalid_reasons` 及 `problems` 明细）。
2. **两级异常捕获并标记失效**：
   - **退出推进异常**：`accum_early_entry.py:2210` `try...except Exception` 捕获异常后，立即调用 `mark_run_invalid(run_state, INVALID_EXIT_DRIVE_ERROR, err_row)`。
   - **平仓被拒拦截**：`accum_early_entry.py:2202` 若 `close_at_price` 返回 `ok=False`（如价格越界），将该笔平仓从有效退出中剔除（`exit_ts=None, net_pnl=None`），并调用 `mark_run_invalid(run_state, INVALID_EXIT_CLOSE_REJECTED, ...)`。
3. **顶层状态落盘与绩效强阻断**：
   - `accum_early_entry.py:2443`：返回值顶层暴露 `out["run_status"] = "FAILED"` 及 `out["run_invalid_note"]`。
   - `run_abc_pool.py:279-300`：检测到 `run_status != "OK"` 时触发 `_block_metrics`，将胜率、总净盈亏、盈亏比、最大回撤、年化收益全部置为 `None`，并落盘 `run_status.json`。
   - `backtest_metrics.py:186-228`：入口读取 `run_status.json`，凡状态为 `FAILED`/`INVALID`，直接阻断指标产出，返回 `invalid=True` 且所有绩效字段为 `None`。

### 2. 专项回归与异常注入测试实测结果
在项目环境下运行 `evidence/AUDIT-003-PATCH/scripts/test_patch_price_gate_and_run_status.py`，**全部 68 项断言 100% 通过**：
- `T1.1 - T1.7`：`close_at_price` 缺失高低价/非法高低价精准拒单 `MISSING_BAR_RANGE`，零写数据库。
- `T2.1 - T2.5`：`close_at_price` 成交价越界精准拒单 `EXEC_PRICE_OUT_OF_RANGE`，仓位保持 `open`，零写账本。
- `T3.1 - T3.3`：负向对照，合法价格区间正常平仓落账，证明门禁非盲目拦截。
- `T4 - T7`：`open_position` 与 `plan_entry` 越界拦截及漏传 `TypeError` 断言全部通过。
- `T8.1 - T8.4`：**注入退出推进异常**，`run_status` 强制被打为 `FAILED`，记录 `EXIT_DRIVE_ERROR`。
- `T10.2` & `T11.4`：**异常注入下的绩效入口验证**，跑分器与 `compute_metrics` 输出的胜率、盈亏比、收益率等指标全部被清空为 `None`。

- **核查结论**：异常阻断逻辑严密，系统已彻底丧失“带病产出有效绩效报告”的可能。

---

## 三、核对 3：补修后甲乙丙组合回测实测复核与双臂对账

在真实行情库环境下（40 标的，121 天窗口，`d26f33fb` 版本），双臂同口径对账结果如下：

### 1. 现行默认臂 (ARM A) vs 合法区间臂 (ARM B) 对账表

| 对账核验项 | ARM A：现行默认 (`gap_model=stop_price`) | ARM B：敏感性对照 (`gap_model=bar_open`) | 审计核查结论 |
|---|---|---|---|
| **配置状态** | `fixture_rules_06w2.json` (`approved=false`) | `fixture_rules_06w2_gap_bar_open.json` (`approved=false`) | 两臂参数逐字节一致，仅差 `gap_model` |
| **候选信号数 (4a / 4b / 4c)** | **105 / 87 / 97** | **105 / 87 / 97** | 候选生成完全一致，信号层零改动 |
| **实际开仓数 (4a / 4b / 4c)** | 65 / 82 / 88 | 100 / 24 / 26 | 槽位正常复用释放（远超旧版 10 笔） |
| **运行可信度状态 (run_status)** | **FAILED** | **OK** | **ARM A 被新门禁成功捕获并阻断** |
| **失效原因码 (invalid_reasons)** | `['EXIT_CLOSE_REJECTED']` | `[]`（无异常） | ARM A 出现平仓越界；ARM B 全量合法 |
| **价格越界拦截条数 (run_problems)** | **20 条**（涉及 16 笔持仓） | **0 条** | 强门禁精准拦截物理不可成交价格 |
| **绩效指标产出 (胜率 / 净额 / 回撤)** | **全部阻断为 `None` (—)** | 正常产出指标（8.08% / -486U 等） | **BLK-01 阻断机制真实生效** |
| **缺失规格静默放行数** | **0 笔** | **0 笔** | 0.01 兜底 tick 彻底消除 |

### 2. 核心现象深度解读
1. **ARM A 触发 `FAILED` 是门禁修复的必然与正确表现**：
   - 现行默认配置中的 `gap_model="stop_price"` 规定在开盘跳空低开跌破止损时，仍按原来的止损价成交。
   - 对账数据显示，这 16 笔交易的止损价**高出当前 K 线的真实最高价 (`high`) 3.58% $\sim$ 26.79%**（超出幅度达 K 线自身振幅的 2.5 $\sim$ 208 倍）。
   - 在旧代码中，由于缺乏门禁，这些脱离行情的假成交被写入了账本；而在补丁生效后，**价格门禁坚决拒单，并直接将回测打为 `FAILED` 阻断虚假绩效**。这确凿证明了门禁的有效性。
2. **ARM B 证明了门禁在合法行情下完全放行且槽位释放健康**：
   - 在采用真实可成交的 `gap_model="bar_open"` 时，`run_status="OK"`，价格越界数为 0，开仓槽位复用流畅（甲策略开仓 100 笔），无任何误杀与阻断。

---

## 四、移交 Codex 执行 GATE-004 的结论与建议

### 1. 结论
- **代码级审查通过**：针对 `AUDIT-003` 提出的两项阻塞缺陷（P0 价格校验旁路、P1 退出异常漏阻断），Work 实施的补丁逻辑完整、调用点覆盖完备、负向测试完备，**已具备通过技术审计的条件**。
- **业务参数待确认（DEC-01）**：
  回测系统的技术底座已经具备 fail-closed 能力，但现行配置中默认的 `gap_model="stop_price"` 因物理不可成交已被门禁判定为 FAILED。

### 2. 建议下一步行动
建议用户明确参数裁决指令：
> **将策略退出跳空口径明确裁决为 `exit.gap_model = "bar_open"`（按跳空后实际开盘价成交），并在正式获批配置中生效。**

完成该决策后，回测系统即可在全绿灯状态（`run_status="OK"`、越界为 0、无静默绕过）下直接移交 Codex 执行 **`GATE-004` 最终验收**。
