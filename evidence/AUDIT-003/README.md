# [AUDIT-003] 针对提交 d472c244 的独立只读复核与真实验收报告

- **对比基线**：`e5e82640` $\rightarrow$ `d472c244`（分支 `wyckoff-v3.1-main`）
- **审计角色**：主代理 / 独立只读复核方
- **执行原则**：基于真实调用链与实际运行结果取证，零代码/配置/账本修改

---

## 总体结论：【不具备进入 GATE-004 最终验收条件，需退回 Work】

- **判定理由**：
  1. **FIX-001 存在隐蔽的“带病产出报告”漏洞**：退出推进发生未捕获异常时，仓位残留且回测继续运行，账本与上层跑分器未打上 `FAILED` / `INVALID` 阻断标记，仍照常输出净值与胜率。
  2. **FIX-002 核心价格校验函数存在严重的“全调用链被绕过（Bypass）”缺陷**：新增的 K 线价格区间校验函数 `validate_execution_price_in_bar` **仅在单元测试中被直接调用**；正式成交链路（`paper_engine.py`）根本未接入该校验，而 `replay_calc.py` 内的调用点又因未传可选参数 `[bar_low, bar_high]` 被条件分支静默跳过。

---

## 一、重点项 1（FIX-001 退出异常处理）核查证据与结论

### 1. 退出异常捕获点定位（文件与具体行号）
在 `框架/backend/accum_early_entry.py`（第 2171-2180 行）：
```python
2171:        for pid in list(cache.get(scheme) or []):
2172:            try:
2173:                r = drive_position_exit(scheme, int(pid), feed, cfg=cfg,
2174:                                        end_ms=int(until_ms),
2175:                                        legacy_4h_exit=legacy_4h_exit, store=store)
2176:            except Exception as exc:                          # noqa: BLE001
2177:                keep.append(int(pid))
2178:                out.append({"ok": False, "scheme": scheme, "position_id": int(pid),
2179:                            "reason": "drive_error", "error": repr(exc)})
2180:                continue
```
以及后续调用链路：
- `research_run` 调用 `_replay_timeline`（第 2307-2315 行）：
  ```python
  2307:        if use_timeline:
  2308:            try:
  2309:                tl_exits, tl_stats = _replay_timeline(...)
  2314:            except Exception as exc:                          # noqa: BLE001
  2315:                out["errors"].append({"stage": "timeline_replay", "error": repr(exc)})
  ```
- `research_run` 调用收尾 `drive_all_exits`（第 2318-2328 行）：
  ```python
  2327:            except Exception as exc:                          # noqa: BLE001
  2328:                out["errors"].append({"stage": "drive_exits", "error": repr(exc)})
  ```

### 2. 状态标记与账本阻断检查
1. **持仓状态残留**：`keep.append(int(pid))` 将退出异常的持仓保留在内存持仓缓存中，该持仓在 SQLite 账本 `paper_position` 中依然维持 `status='open'`，未完成平仓结算。
2. **回测未中断**：`_advance_open_positions` 仅在返回的退出列表追加了一条错误字典（`{"ok": False, "reason": "drive_error"}`），但未向外抛出异常，整个回测回放循环继续推进后续候选开仓。
3. **账本状态无失效标记**：随后在收尾阶段，`research_run` 依然调用 `ledger_snapshot(scheme)` 导出账本，返回的字典中既无 `status: FAILED` 字段，也未在 SQLite 写入任何失效标记。
4. **上层跑分器静默放行**：查看 `evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py`（第 553-558 行）：
   ```python
   status = "OK"
   if drift:
       status = "INVALID_CODE_DRIFT"
   elif cfg_brief.get("kind") != "approved" or not cfg_brief.get("approved"):
       status = "UNVERIFIED_CONFIG_UNAPPROVED"
   ```
   该脚本完全未检查 `raw.get("errors")` 或 `exits` 中的 `drive_error`，直接调用 `ledger_metrics` 照常计算胜率、盈亏比与收益率。

### 3. 核查结论
> **【确认缺陷需退回 Work】**：系统**确实存在“带病产出有效绩效报告”的严重漏洞**。一旦发生退出推进异常，未平仓位将永远留在开仓状态，回测不仅不中止，反而会基于其余已平仓记录计算胜率、回撤和盈亏，产出看似正常（甚至可能虚高）的绩效报告。必须在出现 `drive_error` 或 `timeline_replay` 异常时强制设置运行状态为 `FAILED` 并中断绩效产出。

---

## 二、重点项 2（FIX-002 价格区间校验全路径覆盖）清单与结论

### 1. 校验函数定义签名与行为
在 `框架/backend/replay_calc.py`（第 129-145 行）：
```python
def validate_execution_price_in_bar(fill_price: float, bar_low: float, bar_high: float,
                                    tol: float = 0.001) -> tuple[bool, float, Optional[str]]:
```
- 若 `fill_price is None or bar_low is None or bar_high is None`，返回 `(False, 0.0, "EXEC_PRICE_OUT_OF_RANGE")`。
- 若 `px < lo * (1 - tol) or px > hi * (1 + tol)`，返回 `(False, dev, "EXEC_PRICE_OUT_OF_RANGE")`。

### 2. 全局正式成交路径调用点追踪清单

| 业务路径 | 调用入口文件与行号 | 实际执行函数 | 是否调用 `validate_execution_price_in_bar` | 是否传入真实 `[low, high]` | 审计核验结论 |
|---|---|---|---|---|---|
| **组合回测开仓** | `accum_early_entry.py:1667` | `paper_engine.open_position` | **否**（完全未调用） | **否** | **绕过**：`open_position` 全程未接入该函数，直接落库 |
| **组合回测平仓** | `accum_early_entry.py:1814` | `paper_engine.close_at_price` | **否**（完全未调用） | **否** | **绕过**：`close_at_price` 全程未接入该函数，直接落库 |
| **单事件回放开仓** | `replay_calc.py:603` (`replay_one`) | `replay_calc.plan_entry` | **否**（条件分支绕过） | **否**（默认 `None`） | **绕过**：`replay_one` 调用 `plan_entry` 时未传 `bar_low/bar_high` |
| **单事件回放退出** | `replay_calc.py:372, 388` (`scan_exit`) | 内部直接调用 | **是** | **是** | **生效**（仅覆盖单事件模拟退出阶段） |
| **单元测试验证** | `test_fix002_precision_and_gates.py:157` | 单元测试直接断言 | **是** | **是**（Mock 构造数据） | **仅单测独立生效** |

### 3. 可复现绕过案例（代码追踪）
在 `框架/backend/replay_calc.py`（第 217-226 行）：
```python
def plan_entry(..., bar_low: Optional[float] = None, bar_high: Optional[float] = None, tol: float = 0.001):
    ...
    # [FIX-002] 强门禁：成交价格落在对应执行 K 线区间 [low, high] 校验
    if bar_low is not None and bar_high is not None:
        valid_px, dev, rej_px_code = validate_execution_price_in_bar(
            entry_price, bar_low, bar_high, tol=tol)
        ...
```
- **漏洞复现**：生产代码 `replay_calc.py:603` 在调用 `plan_entry` 时：
  ```python
  entry = plan_entry(symbol=symbol, raw_open=float(entry_bar["open"]),
                     prev_quote_volume=pqv, equity=eq,
                     gross_open_notional=gross_open_notional,
                     free_cash=eq, portfolio_risk_open=portfolio_risk_open,
                     caliber=cal,
                     direction=("long" if int(direction) == 1 else "short"))
  ```
  **未传 `bar_low` 与 `bar_high`**！由于默认为 `None`，`if bar_low is not None` 分支根本不会进入，价格越界校验被静默跳过！

### 4. 核查结论
> **【确认缺陷需退回 Work】**：价格校验函数目前属于“仅单测可见的伪保护”。真实组合回测核心链路（`paper_engine.open_position` / `paper_engine.close_at_price`）压根没有接入该校验；且 `plan_entry` 的参数设计为可选，生产调用点未传递边界参数导致校验被静默穿透。

---

## 三、组合回测与甲乙丙策略复核实测取证

在真实行情库环境下（40 个标的，2026-06-27 至 2026-10-26，共 121 天窗口，单进程同口径）执行完整验证，取证结果如下：

### 1. 仓位释放与顺序无关性实测验证
- **槽位释放效果显著**：
  - 在旧版（`e5e82640`）下，由于逐标的遍历且未按时间轴及时平仓，`max_positions=10` 槽位被前 10 个标的永久锁死，40 标的全池每组仅成交 10 笔（69 笔被拒）。
  - 在 `d472c244`（统一时间轴）下，同一时刻先触发退出释放槽位，**`max_positions_reached` 拒单数归零（0 笔）**！甲策略成功开仓 **101 笔**（100 笔已平仓复用，1 笔持仓到期）。
- **标的顺序无关性通过**：`test_fix001_timeline.py` 实测打乱标的输入顺序 50 次，事件时间轴排序、撮合序列与持仓明细逐字段完全一致。

### 2. 甲乙丙三策略数据对账表

| 对账项 | 甲 (4a) | 乙 (4b) | 丙 (4c) | 审计备注 |
|---|---|---|---|---|
| **候选信号数** | 105 | 87 | 97 | 候选产出与扫描完全一致 |
| **实际成交数** | **101** | **24** | **26** | 槽位复用释放，打破旧版恒为 10 笔的限制 |
| **拒单总数** | 4 | 63 | 71 | 均有清晰拒单账本记录 |
| **拒单原因码分布** | | | | |
| ├─ `position_already_open_no_add` | 4 笔 | 1 笔 | 1 笔 | 同币已有持仓，禁止重复加仓（已批准规则） |
| └─ `account_drawdown_locked` | 0 笔 | **62 笔** | **70 笔** | **乙丙两组触发 20% 回撤熔断硬闸门** |
| └─ `max_positions_reached` | 0 笔 | 0 笔 | 0 笔 | 槽位及时释放，彻底消除槽位耗尽假象 |
| **价格越界数 (`EXEC_PRICE_OUT_OF_RANGE`)** | **0** | **0** | **0** | 新运行中未发现真实越界异常 |
| **缺失规格静默放行数** | **0** | **0** | **0** | 彻底移除了 0.01 默认 tick 回退兜底 |

### 3. 时间戳与未来函数核查
- **核查结论**：**【已确认安全】**
- **技术证据**：统一时间轴排序严格以入场 K 线开盘时刻 `entry_bar_open_ms` 驱动；持仓跟踪止损与退出推进仅传入 `until_ms = e["t"]`，读取已闭合的 15m/4h K 线；撮合时钟校验 `bar_open <= attempt_ms` 确保只用当前开盘价撮合，不存在未来数据泄漏。

### 4. 参数身份核查
- **核查结论**：**【需用户决策】**
- **技术证据**：
  - 正式获批配置路径 `数据/research/ACCUM-EARLY-05/approved_rules.json` 在磁盘上依然不存在。
  - 当前测试与运行全量使用 `evidence/ACCUM-EARLY-06-W2/01/config/fixture_rules_06w2.json`，其内字段明确标注 `"approved": false`，包含 13 项待决的研究占位参数。
  - **当前产出的对比数据仅代表占位工程链路表现，绝对不得作为正式策略实盘上线依据**。

---

## 四、发现的阻塞项或剩余风险清单

| 编号 | 审计发现项 | 风险等级 | 判定状态 | 整改/应对建议 |
|---|---|---|---|---|
| **BLK-01** | **退出推进异常缺少全局阻断与状态标记** | P1 | **确认缺陷需退回 Work** | 在 `_advance_open_positions` / `research_run` 捕获到退出异常时，必须强制将返回的运行状态置为 `FAILED`，并在上层汇总中阻断绩效与指标产出，禁止带病输出报告。 |
| **BLK-02** | **K 线价格区间校验全链路绕过 (Bypass)** | P0 | **确认缺陷需退回 Work** | 1. 在正式撮合引擎 `paper_engine.open_position` 与 `close_at_price` 中强制执行 `validate_execution_price_in_bar`；<br>2. 将 `plan_entry` 中的 `bar_low/bar_high` 设为必填参数（不允许默认 `None`），并在 `replay_one` 等所有调用点补齐传参。 |
| **RSK-01** | **乙组与丙组触发账户 20% 回撤锁止** | P2 | **已确认安全（策略逻辑）** | 乙组（24 笔）与丙组（26 笔）在占位参数下回撤达到 20%，风控闸门正确执行并拒绝了后续 60+ 笔委托，证明风控熔断机制有效。 |
| **DEC-01** | **策略正式参数尚未获批与固化** | P1 | **需用户决策** | 正式 `approved_rules.json` 仍未提供，当前所有成绩属于占位参数研究模式。需用户正式裁决 13 项经济口径（滑点、手续费、资金费、参与率）与入场阈值。 |
