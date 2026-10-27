# ACCUM-EARLY-05-E｜规则与实现独立行为核对报告

- **执行窗口**：`ACCUM-EARLY-05-E`（独立行为核对窗口）
- **核验对象**：`evidence/ACCUM-EARLY-04-A`（规则定义） vs `evidence/ACCUM-EARLY-04-B`（工程实现）
- **工程原则**：本窗只读共享工程，只写本窗隔离核验脚本与证据；不承担最终 Codex 验收，不修改其他窗口实现。
- **免责声明**：所有运行均在隔离测试环境（`fixture`）下进行，**仅用于验证工程链路、时序逻辑与隔离边界，不输出任何正式策略绩效**。

---

## 0. 一句话核心结论

**04-B 在工程管道与物理隔离上执行严密（缺配置确实无法开仓，账本与生产完全隔离，重启恢复具备严格幂等性）；但在核心策略语义上存在关键偏离与时序错位——特别是「承接指标时序颠倒」以及「压制位合格性缺失」，已整理成专项清单移交 05-B 整改。**

---

## 1. 规则与实现独立行为核对结果（五大核心任务逐项结论）

| 任务项 | 核对重点 | 验证状态 | 关键行为证据与发现 |
|---|---|---|---|
| **任务 1** | 规则与实现行为对照 | **发现多处严重语义偏离** | 发现 3 处严重语义错位/遗漏（承接时序颠倒、压制位缺失合格性与摆动高点、扫低缺少 s_loc > L 约束）与 2 处逻辑遗漏（见第 2 节）。 |
| **任务 2** | 缺批准配置硬阻断 | **完全通过 (4/4)** | 无配置或未批准时，`try_open()` 严格拦截，`called_paper_engine=False`，生产与测试账本零写入；同时数据读取与观察记录正常工作。 |
| **任务 3** | 真实历史逐时点核对 | **已完成并留存案例** | 从真实历史提炼 7 笔候选事件（SOLUSDT、AVAXUSDT），还原建档、扫低、压制位、确认、成交等全部时点输入真值表（测试配置不构成绩效）。 |
| **任务 4** | 隔离与恢复幂等性 | **完全通过 (2/2)** | 生产账本与库零研究污染，越界强制阻断；重复委托与真重启恢复后 100% 幂等拦截（`duplicate_signal_uid`），零重复下单/成交。 |
| **任务 5** | 交接 05-B 与保留反例 | **已交付** | 编写 `05_B_交接问题清单.md`，保留所有历史反例与差异样例，待 05-B 修复后使用同一输入重跑核验。 |

---

## 2. 详细核验事实与差异取证

### 2.1 任务 1：04-A 规则 vs 04-B 实现的 6 大维度对照

数据来源：`evidence/ACCUM-EARLY-05-E/out/01_rule_impl_diff.json`

1. **扫低对象（`sweep_object`）**：
   - 04-A 规范：要求 `local_support`（前 N 根最低价），且具备硬性前提 `s_loc > box['L']`（必须在箱底上方，否则属于原策略箱底扫低）；
   - 04-B 实现：仅实现了 `prior_n_low`，**完全未校验 `ref > box['low_l']`**。一旦行情严重破位，跌破箱底后的假反弹会被误判为提前入场。
2. **局部压制位（`local_resistance`）**：
   - 04-A 规范：推荐首选 `swing_high_k3_lookback48`（摆动高点）；核心门槛 `resistance_qualified` 要求 `L + 0.5A <= R <= H - 0.5A`，空间不足终结为 `NO_RESISTANCE`；
   - 04-B 实现：未实现摆动高点；**完全遗漏合格性校验，无 `NO_RESISTANCE` 终态**。压制位贴近箱顶（提前量消失）或贴近箱底（止损容不下）时依然放行。
3. **承接与卖压指标（`absorption` / `sell_pressure`）**：
   - 04-A 规范：在 **扫低根（`i_sweep`）** 判定！作为进入武装阶段的硬门槛（`absorption_is_gate=True`）。要求 `taker_buy_quote_volume / quote_volume`（名义额）与基准乘数比对，且收盘在上半区；
   - 04-B 实现：**重大时序颠倒**！在扫低时完全不测承接，反而挪到 **收上压制位的确认突破根** 判定；且使用的是币数（`taker_buy_volume / volume`）对比固定绝对值，未实际使用基准中位数。
4. **失效机制（`invalidation`）**：
   - 04-A 规范：包含箱体全局破位（连续 2 根收盘破 L 或最低价破 L-0.5A）与压制位假突破跌回（连续 2 根收盘破 R-0.1A）；
   - 04-B 实现：仅实现了跌破当次扫低最低点（`sweep_low - buffer_atr * atr`），缺失上述两项全局与二次跌破保护。
5. **冷却机制与重复候选（`cooldown` & `candidate`）**：
   - 04-B 在生成 CANDIDATE 后标记 `obs.done=True`，实现了单事件单候选约束；但在未批准配置下将冷却时间硬设为 0，可能导致同标的在未批准扫描时高频建档。

---

### 2.2 任务 2：缺批准配置硬阻断核查

数据来源：`evidence/ACCUM-EARLY-05-E/out/02_gate_unapproved.json`

- **无配置文件场景**：
  `load_config` 检出缺少 36 项必填字段，`open_allowed=False`，`blocked_by=["config_file_missing"]`。
- **开仓尝试阻断**：
  对候选调用 `try_open()`，返回 `ok=False`，`called_paper_engine=False`；`accum04_open_attempt` 记录 `allowed=0`，`reason="blocked_by_unapproved_config"`。
- **测试配置未开环境开关场景**：
  给定 `fixture_rules.json` 但未显式设置 `ACCUM04_FIXTURE_OK=1` 时，`open_allowed=False`，`blocked_by=["not_approved", "fixture_without_explicit_env"]`，`called_paper_engine=False`。
- **阻断下数据与观察正常运行**：
  在缺失批准配置下，`DataFeed` 成功读取 SOLUSDT 真实 4h（402 根）、1h（1608 根）、15m（672 根）K 线，成功识别 27 个箱体并建档 3 个方案事件，记录 3 条 `blocked_config` 状态记录，产出候选数为 0。证实：**读数据、记观察照常进行，开仓被严格阻断**。

---

### 2.3 任务 3：真实历史事件逐时点输入与判据对撞

数据来源：`evidence/ACCUM-EARLY-05-E/out/03_event_trace.json` 及 `cases/` 目录

选取 2026 年真实冷档（SOLUSDT，`2026-08-01 ~ 2026-09-05`）与真实热库（AVAXUSDT，`2026-09-12 ~ 2026-10-25`）运行，提取出 7 笔完整候选事件。

以典型案例 `SOLUSDT 4a`（见 [`cases/案例_1_SOLUSDT_4a.md`](cases/案例_1_SOLUSDT_4a.md)）为例：
- **建档时刻**：`2026-08-01T03:59:59Z`，箱体下沿 `L=72.26`，`ATR=0.918`；
- **扫低收回时刻**：`2026-08-02T01:59:59Z`，当根最低价 `72.07 < 72.26`，收盘 `72.62 >= 72.35`，满足扫低收回；
- **压制位与确认时刻**：`2026-08-02T02:59:59Z`，压制位取值 `73.08`，收盘 `73.10 > 73.08` 确认；
- **委托与成交时刻**：`2026-08-02T03:15:00Z`（确认后第 1 根 15m 开盘，延迟 15 分钟），入场参考价 `73.10`；
- **差异对照发现**：
  1. 扫低根 `72.26` 恰为箱底，由于未校验 `s_loc > L`，实际退化为箱底破位扫低；
  2. 压制位 `73.08` 离箱顶距离未判别；
  3. 承接力在扫低时未测，在确认根收盘时被置为 `not_required`（测试配置关掉了卖压门槛）。

---

### 2.4 任务 4：研究生产物理隔离与恢复幂等性核查

数据来源：`evidence/ACCUM-EARLY-05-E/out/04_isolation_idempotency.json`

1. **物理隔离检查**：
   - 生产目录 `数据/paper` 中无任何研究臂 `4a/4b/4c` 账本文件（匹配结果为 0）；
   - 越界测试：当 `PAPER_LEDGER_DIR` 指向生产目录或未显式指定时，`try_open()` 100% 触发 `isolation_violation` 拦截，`called_paper_engine=False`。
2. **重复委托与重启幂等性**：
   - 在隔离账本内首次提交真实候选开仓：`first_open_ok=True`，生成订单 1 笔、成交 1 笔；
   - 未关进程重复提交同一候选：`repeat_open_ok=False`，原因 `duplicate_signal_uid`，订单数与成交数保持 1，无重复成交；
   - 模拟真进程中断重启（全新 Python 连接载入原账本）：重新提交同一候选，`resume_open_ok=False`，原因 `duplicate_signal_uid`，重启前后订单总数与成交总数严格等于 1，**零重复委托、零重复成交**。

---

## 3. 产物与证据索引

| 产物路径 | 类型 | 内容说明 |
|---|---|---|
| `evidence/ACCUM-EARLY-05-E/05_B_交接问题清单.md` | 交接文档 | 交付 05-B 的详细问题清单与修改建议 |
| `evidence/ACCUM-EARLY-05-E/scripts/01_rule_impl_diff.py` | 验证脚本 | 04-A 规则与 04-B 实现多维度静态与逻辑比对 |
| `evidence/ACCUM-EARLY-05-E/scripts/02_gate_unapproved.py` | 验证脚本 | 缺批准配置硬阻断与数据观察正常运行核验 |
| `evidence/ACCUM-EARLY-05-E/scripts/03_event_trace.py` | 验证脚本 | 真实历史完整事件提取与逐时点输入真值面对撞 |
| `evidence/ACCUM-EARLY-05-E/scripts/04_isolation_idempotency.py` | 验证脚本 | 隔离边界越界阻断与重启恢复幂等性核验 |
| `evidence/ACCUM-EARLY-05-E/out/*.json` | 原始证据 | 全部脚本的结构化机器输出 JSON（保留反例，不覆盖） |
| `evidence/ACCUM-EARLY-05-E/cases/*.md` | 案例报告 | 真实事件逐时点 Markdown 案例分析 |

---

## 4. 下一步行动

1. 确认交接工单 `05_B_交接问题清单.md` 已落盘；
2. 保持本窗所有失败样例与差异证据原样归档；
3. 待 05-B 完成相关整改后，本窗将使用完全同一批真实输入重新执行核对，比对修复效果。
