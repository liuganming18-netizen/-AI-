# Wyckoff V3.1 工程收尾与模拟准入（主代理已审核，参数待用户裁决）

日期：2026-10-27（北京时间）｜性质：证据索引与后续工作草案，不是参数批准或最终验收。

## 1. 对象与当前状态

- 实际工程：`/Users/boos/Desktop/威科夫因子共振趋势`；分支 `wyckoff-v3.1-main`；HEAD `9e3d60cfaf57f521baf6da849a9c7a71e09c3402`。工作区有 119 项未提交变化；不覆盖、不清理。
- 本轮目标是同一工程中的吸筹提前入场研究/回测链路（`accum_early_entry.py`、`accum_research.py`），不是另一个研究项目。当前统一交接仍停在 ACCUM-EARLY-06，记录存在滞后。
- FIX-001（统一时间轴与释放槽位）和 FIX-002（成交精度、规格与价格区间门禁）已完成代码审计；AUDIT-003-PATCH 的代码修复经 `AUDIT-003/RECHECK.md` 二次只读复核通过。主代理已核对 `d26f33fb→HEAD` 六个核心/跑分文件无差异。
- 实际审计运行：ARM A `run_status=FAILED / EXIT_CLOSE_REJECTED`；ARM B `run_status=OK` 但 `table_status=UNVERIFIED_CONFIG_UNAPPROVED`。A/B 都不是获批正式成绩。

## 2. 分类状态

### 已证缺陷（代码修复已审计）

- FIX-001：组合回测按标的扫完才统一退出，造成槽位不复用。证据：`evidence/FIX-001/README.md`、`out/01_code_diff.patch`、`out/02_test_output.txt`。修复代码审计完成；报告列出的17项回归通过属于执行证据，不单独替代审计。
- FIX-002：合约规格回退及成交价门禁不足。证据：`evidence/FIX-002/README.md`。
- AUDIT-003 两个退回项：退出异常带病产出绩效、价格区间校验未全链路接入。补丁及复核：`evidence/AUDIT-003-PATCH/README.md`、`evidence/AUDIT-003/RECHECK.md`。代码级修复已审计，但正式获批配置/正式结果仍未验收。

### 验收待证 / 明确风险

- 正式窗口资金费覆盖及冷热读入尚待核实。生产 `paper_engine.py` 的 `funding_events` 仍直接查询 `funding_history`，尚未见回测/`run_abc_pool` 接入冷热适配器的证据。可先评估复用 `evidence/ACCUM-EARLY-06-06/` 的适配器；不另建采集器。不要把既有“380小时缺口”当作当前数据库状态，需针对正式窗口复核。
- 历史合约规格仍有 `unverified_historical`；正式回测纳入范围与资格规则需明确并逐标的出证据。来源：`evidence/FIX-002/README.md`。
- `run_status.json` 只由 `run_abc_pool.py` 落盘，其他入口可能使 `compute_metrics` 读不到状态文件、不能阻断无效绩效。来源：`evidence/AUDIT-003-PATCH/README.md` §七。进入正式回测前须证明所用入口具备等价阻断，或补齐缺口。
- `test_paper_live_01::test_03` 尚未通过；DEC-01 后须重跑。另有全仓直接写 `paper_realized` 的静态证明未完成、`scan_exit` 缺 low 时不可判、退出返回元组易错位等风险，均见 PATCH §七。
- GATE-004 尚无验收报告，RECHECK 仅提出移交建议。不得将“DEC-01 已裁决”视为自动通过 GATE-004。

### 未批准参数（未全部批准前，禁止更新正式配置）

以下13项在同一 `accum_research.PENDING_PARAMS` 与 `accum_early_entry.PENDING_TO_CONFIG` 一一对应；待决值均不能由执行方代填。配置路径指 `ACCUM-EARLY-05-B` 配置结构，实际批准文件应由后续工单指定的新目录产出。

| # | 参数 / 决策问题 | 配置字段路径 | 现有依据、待选定义及影响 |
|---|---|---|---|
| 1 | `local_resistance_definition`：局部压制位如何定义 | `shared.local_resistance.mode`, `.lookback_bars`, `.swing_k` | 现有候选实现：`swing_high_k3_lookback48`、`prior_n_high`、`box_high`；尚无已批准定义。影响甲乙丙入场触发。 |
| 2 | `local_resistance_confirm`：压制位如何确认 | `shared.local_resistance_confirm.mode`, `.buffer_atr` | 已实现候选 `1h_close` / `intrabar`，缓冲 ATR 值待定；影响全部三组确认条件。 |
| 3 | `sweep_bars`：扫低收回需几根完成 | `shared.sweep_recovery_scope.mode`, `.max_bars` | 单根/多根候选已实现；冻结原策略单根收盘回收只适用于原策略，不能自动套给新方案。影响事件链起点；多根扫低参照根定义另有证据缺口。 |
| 4 | `sell_pressure_baseline`：卖压/主动买入比较基准 | `shared.sell_pressure.baseline`, `.n` | 已实现候选 `v20_median` / `prior_n_mean`，N 未定；影响甲乙丙证据判定。 |
| 5 | `sell_pressure_threshold`：卖压阈值及是否硬门槛 | `shared.sell_pressure.buy_ratio_mult`, `.require` | 倍率与是否要求待用户定；影响甲乙丙入场门槛。 |
| 6 | `observe_deadline_hours`：观察期限 | `shared.observe_deadline_hours` | 原策略336小时仅为原策略依据，新方案期限待定；影响事件生命周期。 |
| 7 | `pullback_deadline_hours`：回抽等待期限 | `schemes.4c.pullback.deadline_hours` | 无已批准新方案取值；影响丙组。 |
| 8 | `intrabar_break_cancel`：盘中破位是否取消 | `shared.intrabar_break_cancel.mode`, `.buffer_atr` | 已实现候选 `close_only` / `intrabar`；原策略按收盘不自动适用于新方案。影响候选取消。 |
| 9 | `entry_distance_limit`：入场价格偏离上限 | `shared.entry_gate.entry_price_deviation_cap` | 已有参与率限制不是该参数的批准；数值待定。影响成交可行性/入场拒绝。 |
| 10 | `oi_necessity`：OI 必要还是辅助证据 | `shared.oi_role` | 候选 `evidence_only` / `required` / `ignored`；无批准依据。影响全部入场门槛。 |
| 11 | `repeat_candidate_limit`：重复候选及冷却 | `shared.repeat_candidate_limit.per_event`, `.cooldown_hours` | 数量和冷却待定；24小时只是建议，当前0也不是批准。影响候选重复/再次入场。 |
| 12 | `risk_budget_new_scheme`：新方案风险配置 | `conflict_resolution.risk_config` | 3仓/10仓等现存口径冲突；必须由用户指名采用哪套，影响开仓、仓位和组合风险。 |
| 13 | `slippage_source`：滑点口径 | `conflict_resolution.slippage_source` | 策略包3bps、生效视图5bps冲突；必须指名来源，影响成本与盈亏。 |

另有显式参数 **DEC-01 `exit.gap_model`**，路径 `exit.gap_model`，候选 `stop_price`、`bar_open`、`midpoint`。`stop_price` 作为现有默认在补丁运行中产生越界并使 ARM A FAILED；`bar_open` 是复核建议、非批准；`midpoint` 仅敏感性选项。影响跳空止损成交价及正式盈亏，须用户裁决。

批准纪律：上述13项与 DEC-01 均须逐项有用户批准依据并写入批准引用；只有全部正式回测所需参数完成批准、校验为支持值，才可生成 `approved=true` 的正式配置。不可仅凭 DEC-01 裁决把 fixture 改为 approved。

配置映射证据：`框架/backend/accum_early_entry.py:174-253`（`PENDING_TO_CONFIG`、`REQUIRED_PATHS`、`IMPLEMENTED`）；问题及影响：`框架/backend/accum_research.py:67-151`（`PENDING_PARAMS`）。

### 非阻塞优化（本轮不派施工）

- 退出结果元组改结构化对象、全仓 AST/数据流证明、界面改版和架构整理，均不作为收尾新增施工范围。正式入口的实际行为核验仍须完成；发现真实旁路再按具体缺陷处理。
- 状态文件缺失风险先限定到本次正式入口取证；不以此自动要求重写全部历史回测入口。
- 不设盈利或胜率目标逼迫修改参数；正式回测即使亏损也必须如实交付。工程验收通过不等于策略有效。

## 3. 21项清单只作去重交叉提醒

`evidence/ACCUM-EARLY-06-W1/03_R1/待决表_R1.md` 将31规则项、8冲突、12问去重为21项：12参数、6证据、3工程。它与本节13个配置待决项不是同一计数口径；P-10 与 `exit.gap_model`/入场偏离有交叉，P-06/P-07 与风险/滑点相交，其余不应机械并入本表。R1将资金费时点、生产/研究止损有效时刻与其他问题另分类。勿引用早期 ACCUM-EARLY-01 的14项代替代码当前13项。

## 4. 后续步骤、负责人及拟定完成标准

以下完成标准为本草案提出，除明确注明的代码审计证据外，不冒称既有用户批准标准。工程/正式回测工单 **待用户转发**，尚未启动。

| 阶段 | 负责人 | 完成标准及状态 | 拟定证据路径 |
|---|---|---|---|
| A. 参数裁决 | 用户决定；主代理汇总逐项定义、选项、影响 | 13项及 DEC-01 逐项明确取值；已确认止损规则不重复询问。未完成。 | `evidence/ACCUM-EARLY-CLOSEOUT-01/01_参数裁决记录.md` |
| B. 批准配置与工程缺口核实 | Work 实施（工单待用户转发）；主代理复核 | 全部必需参数有批准依据后才生成正式配置；13字段映射完整、无unsupported值、身份/hash固定。核实资金费正式窗口覆盖与冷热接入、历史规格资格、各回测入口失败状态阻断；仅对确实未覆盖项做最小修复。未启动。 | `evidence/ACCUM-EARLY-CLOSEOUT-01/02_配置与门禁证据/`；资金费适配评估引用 `evidence/ACCUM-EARLY-06-06/` |
| C. 正式回测 | Work 执行（工单待用户转发） | 拟定：使用获批配置及锁定代码/数据身份；每次运行状态有效且失败时不产出绩效；逐笔价格/费用/资金费/规格资格有明细；完整原始账本、输入清单和复现命令。先由主代理审核门槛，不把 fixture 敏感性结果计为正式成绩。未启动。 | `evidence/ACCUM-EARLY-CLOSEOUT-01/03_正式回测/` |
| D. 独立审计与 GATE-004 | 主代理/Codex，和实施者分离 | 拟定：复算运行身份、信号/成交/拒单/退出、费用资金费与汇总；检查失败闭锁、规格、数据时间可得性和反例；逐条执行事先冻结的 GATE-004 清单并留报告。代码补丁审计已完成，但正式结果审计与 GATE-004 未完成。 | `evidence/ACCUM-EARLY-CLOSEOUT-01/04_独立审计/`、`05_GATE-004/` |
| E. 条件式模拟运行 | 主代理拟定并先补准入标准；Work执行；Codex独立验收 | 用户已授权作为条件式后续，但观察时长/阈值尚无已批准标准，不在此自定。先整理既有决定并提出缺口；由用户确认所需新增标准后，按批准的观察窗口核对服务/数据健康、逐事件决策链、自然成交或无信号解释、账本和异常恢复。达标前不进入；不涉及真实交易授权。 | `evidence/ACCUM-EARLY-CLOSEOUT-01/06_模拟准入标准提案.md`（待审核）及 `07_模拟运行/`（仅准入通过后创建） |

正式回测交付必须同时包含甲乙丙逐组完整订单、成交、持仓、已实现与未实现账本、权益曲线、手续费与逐事件资金费；运行状态及异常/拒单原因；净盈亏、胜率、盈亏比、最大回撤等绩效及口径。原始 SQLite 账本需随证据包保存并附校验清单，不以被 Git 忽略为由只交汇总。

主代理审核补充：13项是决策类别而非13个标量，风险项须展开本金、仓位和风险限额。21项交叉表内手续费、参与率及资金费同刻顺序等若仍属正式配置必需且未获批准，必须随对应类别一并揭示，不可遗漏后自动批准。正式币池和历史窗口也须固定并给出依据；原40标的/121天敏感性窗口不是自动获批的正式范围。

## 5. 收尾结论

- **代码审计完成**：FIX-001、FIX-002 及 AUDIT-003-PATCH 的代码级修复已由 RECHECK 复核通过。
- **仍阻塞正式配置/正式结果**：13项配置待决 + DEC-01 未全部获批；正式窗口资金费覆盖/冷热接入、历史规格资格、状态文件路径风险仍待证；ARM A 已失败，ARM B 配置未批准。
- **需用户决定**：上表13项与 DEC-01；不得让执行方自行挑选。
- **模拟运行**：属于本轮用户授权的条件式后续；当前准入标准和观察时长缺口，尚不能进入。由主代理整理标准供用户确认，不在本文虚构时长。
- **待用户转发**：上述工程核实、正式回测及条件式模拟工作单均尚未转发/启动。
