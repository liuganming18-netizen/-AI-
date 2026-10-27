# FRAME-VERIFY-01｜02. PIT 与防拟合反例设计矩阵

## 一、反例设计方法论与纪律

本反例矩阵严格遵循“**黑盒反例注入（Counter-example Injection）**”原则：
1. **不以实现方的自测报告为准**：无论实现方声称如何完备，必须通过构造恶意的、边界的或前视的样本进行真实调用验证；
2. **打在真实入口，而非孤立模块**：测试必须直接调用回测 runner、因子提取函数、数据仓储或策略适配器；
3. **零污染与隔离执行**：所有反例数据均在临时内存对象或隔离沙箱目录中构造，绝不写入生产库与原始归档；
4. **状态标记纪律**：在尚未收到 `FRAME-PIT-01` 与 `FRAME-FIT-01` 明确版本回执之前，矩阵中所有验证项统一保持**“待测（等待版本回执）”**，不得提前宣告通过。

---

## 二、PIT 反例用例矩阵（Point-in-Time Counter-examples）

| 反例编号 | 反例名称 | 核心测试意图 | 注入构造方法与输入数据 | 受控框架预期防御行为 (Expected) | 现有漏洞表现 (Actual Defect) | 验证入口与断言 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **PIT-CE-01** | **未来未收盘 K 线注入** | 检验整点决策时是否误读当根未收盘 1h 蜡烛 | 决策时刻为 `ts=12:00:00`。注入一根 `open_time=12:00:00`、`close_time=12:59:59`、`volume=999999` 的极端未来蜡烛。 | **严格物理拒绝**：受控入口必须只读 `close_time < ts`（已收盘蜡烛），该未收盘蜡烛被彻底过滤，特征值不受其任何影响。 | `generate_flow_oi_01.py` 使用 `open_time <= ts`，直接读取该未来未收盘蜡烛，`bar_volume` 瞬间变成 999999。 | 入口：`generate_flow_oi_01` / `backtest_runner`<br>断言：`bar.open_time + bar_interval <= ts` |
| **PIT-CE-02** | **延迟发布时延未满消费** | 检验指标是否在真实发布前被乐观读取 | 指标事件时间 `ts_event=12:00:00`，交易所实际发布滞后 15 分钟，即 `available_at=12:15:00`。在 `ts_decision=12:05:00` 尝试读取该指标。 | **拒绝消费**：`available_at > ts_decision`，判定该数据在决策时尚未发布，标记为不可用或抛出未就绪异常。 | 现有入口仅比对 `create_time <= ts_decision`，在 12:05:00 直接读取 12:15:00 才能拿到的数据。 | 入口：`MarketDataRepository.oi` / `load_symbol_data`<br>断言：`row.available_at <= ts_decision` |
| **PIT-CE-03** | **资金费事件时间冒充发布时间** | 检验是否将资金费结算点直接当成已到达时刻 | 资金费结算事件发生在 `00:00:00.000`，但账单与接口实际发布于 `00:00:45.000`。在 `00:00:05.000` 发起交易决策。 | **不可用拦截**：无经过验证的发布时延凭证时，不得将事件时间直接作为发布时间，在发布凭证到达前拒绝将其计入策略决策。 | `backtest_runner.py` 直接按 `rate_type == "settled"` 且 `ts <= win_end` 吃入，把物理事件发生点等同于已发布可用。 | 入口：`backtest_runner.load_inputs`<br>断言：`funding_available_at > ts` 则不可见 |
| **PIT-CE-04** | **延后 5 分钟伪证明穿透** | 检验是否拿固定 5m 经验延迟当作通用的 PIT 证明 | 对于计算耗时需 10 分钟或因网络拥堵滞后 15 分钟的统计指标，强行以“固定延后 5 分钟”作为已通过 PIT 的凭据。 | **凭证不足拒绝**：必须提供数据源发布的确定性凭证（如交易所消息回执戳、官方发布日历），缺少凭据时必须降级为 `pit_proven=False`。 | 历史部分脚本简单以为“按 5m 桶对齐即可解决 PIT”，掩盖了跨桶计算与发布延迟的真实空洞。 | 入口：`trading_core/pit.py`<br>断言：`declared_lag` 必须由物理凭证支撑 |
| **PIT-CE-05** | **缺失 available_at 字段乐观假定** | 检验遇到缺失时序元数据时是否静默设 lag=0 | 喂入缺失 `available_at` 或 `oi_publication_lag_ms=None` 的指标序列。 | **Fail-Closed 拒绝**：宁可标记为不可用（`decision_usable=False`），也绝不乐观假定即时可用（lag=0）。 | 部分分析代码在字段缺失时直接填充默认 0，并伪标 `COMPLETE`。 | 入口：`trading_core.pit.annotate_oi_pit`<br>断言：`policy.declared is False => usable is False` |
| **PIT-CE-06** | **修改未来数据扰动过去特征** | 检验改变未来数据是否导致过去的因子或决策发生变动 | 保持历史数据 `t <= ts` 严格不动，对 `t > ts` 的未来 K 线、持仓量进行大幅修改（如翻倍或清零），重新执行回测。 | **历史确定性不变**：过去特征向量与信号生成的哈希必须与修改前 100% 一致（Bit-level identical）。 | 若存在全局均值归一化（Lookahead Normalization）或整段回放引用未来，历史决策将发生漂移。 | 入口：`backtest_runner` / `features.py`<br>断言：`hash(features_before) == hash(features_after)` |

---

## 三、防拟合反例用例矩阵（Anti-overfitting Counter-examples）

| 反例编号 | 反例名称 | 核心测试意图 | 注入构造方法与输入数据 | 受控框架预期防御行为 (Expected) | 现有漏洞表现 (Actual Defect) | 验证入口与断言 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **FIT-CE-01** | **未冻结直接查看 Holdout 留出集** | 检验选型与调参阶段是否物理阻断留出样本 | 策略配置 `is_frozen=False`（未生成全域不可篡改冻结哈希），尝试调用或读取 Holdout 期间（如 2026 年）的样本数据。 | **物理拦截**：抛出 `HoldoutAccessForbiddenError`，终止回测执行，严禁向未冻结策略返回留出数据。 | 现有框架完全没有任何 Holdout 拦截守卫，任何未冻结代码均可自由加载全部最新数据。 | 入口：`MarketDataRepository` / `backtest_runner`<br>断言：`if not is_frozen: assert_no_holdout(t)` |
| **FIT-CE-02** | **查看 Holdout 后改参沿用原资格** | 检验查看过留出集的参数被修改后是否被取消资格 | 在读取并评估了 Holdout 表现后，开发者调整了阈值（如改动 `oi_min_resonance` 或 `shadow_ratio`），并试图以“原资格试验”提交。 | **资格剥夺**：识别该参数组合已受留出集污染，在试验注册表中强制标记为 `DISQUALIFIED_CONTAMINATED`，严禁作为生产候选。 | 现有框架无试验生命周期状态机，开发者可无限次手工微调参数直至拟合出完美收益。 | 入口：`ExperimentRegistry.submit`<br>断言：`state != "QUALIFIED"` |
| **FIT-CE-03** | **重命名已观察样本伪装新样本** | 检验是否能识破通过改 ID 逃避留出审计的行径 | 将同一币种同一时间窗口的样本修改 `candidate_id` 或 `run_id` 重新包装提交。 | **物理去重拦截**：基于样本的底层物理指纹（`symbol + open_time + price + volume`）识别重复，拒绝重复登记。 | 现有系统仅认字符串 ID，换名即可绕过检查。 | 入口：`CandidateRegistry`<br>断言：`hash(physical_event) in seen_events => Reject` |
| **FIT-CE-04** | **遗漏/隐瞒失败试验（抽奖隐瞒）** | 检验是否强制记录所有试错，杜绝幸存者偏差 | 发起 10 次不同参数的回测，其中 9 次亏损、1 次盈利，回测脚本仅上报并归档该盈利试验。 | **全量原子登记**：每次运行回测强制自增生成全局序列号并自动入库，缺号或未登记运行直接导致整体验收失败。 | 现有脚本自由在本地运行，用户可只选择好看的 run 放入报告，隐瞒海量试错代价。 | 入口：`backtest_store.py` / `batch_runner`<br>断言：`audit_missing_trial_ids() == 0` |
| **FIT-CE-05** | **重复样本注入** | 检验回测输入是否允许重复样本干扰统计分母 | 在回测候选清单中注入两笔具有相同交易 ID 或同一时间戳的重复信号记录。 | **输入清洗拒绝**：抛出 `DuplicateCandidateError`，拒绝将重复样本纳入胜率与盈亏统计。 | 部分研究脚本仅做简易拼接，容易造成分母虚增或盈亏双计。 | 入口：`load_inputs` / `run_event_trend_backtest`<br>断言：`len(ids) == len(set(ids))` |
| **FIT-CE-06** | **事后走弱标签泄露冒充事前因子** | 检验是否将因变量（未来跌势）直接当成自变量过滤规则 | 构造一个使用未来 1h 跌幅 `fwd_ret_1h <= 0` 作为过滤条件的特征，宣称其为“有效降低走弱数量”的事前因子。 | **因果性阻断**：严格区分特征提取（只能使用 $t \le ts$ 变量）与事后标签（$t > ts$）。任何依赖事后表现的规则严禁作为信号过滤网关。 | `FLOW-OI-01` 历史出现利用当根 1h K 线表现筛除走弱交易的伪因果结论。 | 入口：`Gatekeeper.evaluate`<br>断言：`feature.input_horizon <= 0` |

---

## 三、当前验证状态与版本依赖

| 维度 | 反例编号集合 | 依赖版本回执 | 当前第一阶段核查状态 | 第二阶段行动指引 |
| :--- | :--- | :--- | :--- | :--- |
| **PIT 约束验证** | PIT-CE-01 ~ PIT-CE-06 | `FRAME-PIT-01` 明确版本回执及实现代码 Commit | **待测（PENDING_RECEIPT）**<br>（注：静态审查已明确现有代码存在严重漏洞） | 收到回执后，在独立沙箱中运行注入脚本，验证是否成功物理拦截。 |
| **FIT 约束验证** | FIT-CE-01 ~ FIT-CE-06 | `FRAME-FIT-01` 明确版本回执及实现代码 Commit | **待测（PENDING_RECEIPT）**<br>（注：静态审查已明确当前活跃代码无任何防拟合守卫） | 收到回执后，运行非法访问与改参用例，验证是否强制抛错并作废资格。 |
| **真实入口旁路封堵** | E01 ~ E12 | 两个 Work 窗口联合回执 | **已测出旁路待封堵（BYPASS_EXPOSED）** | 验证生产入口是否强制接入受控网关，绝不允许 `read_parquet` 直接越权读数。 |
