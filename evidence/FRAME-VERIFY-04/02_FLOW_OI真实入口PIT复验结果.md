# FRAME-VERIFY-04｜02. FLOW-OI 真实入口 PIT 复验结果

## 一、复验范围与执行原则

本部分严格遵循工单第一部分指令，对 `FRAME-PIT-02` 交付后的系统真实因子入口与四组实验入口进行全面、独立的隔离复验：
1. **真实入口原则**：直接调用 `evidence/FLOW-OI-01/generate_flow_oi_01.py::extract_features_for_candidate` 与 `evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py::run_four_groups`，不调用自造旁路函数；
2. **反例穿透原则**：复用并升级前序 VERIFY 反例，执行未收盘 K 线、未结束 5m 统计桶、已结束但声明延迟的极端注入；
3. **合法可用原则**：验证合规历史输入能正常处理，确保防护未陷入“一律拒绝”的破坏性懒惰实现；
4. **非空与不变性原则**：显式断言实际特征非空，并在未来数据极端扰动及物理截断输入下比对全部业务特征的一致性。

---

## 二、分项验证详述与对账证据

### 验证项 1: FLOW-OI真实因子入口不使用未收盘、未结束或尚未发布的数据

#### 1.1 未收盘 1h K 线穿透防护验证 (`VERIFY-PIT-1-1-UNCLOSED-CANDLE-REJECTION`)
- **注入设计**：在决策时刻 $T = 1600000000000$（整点），注入一根刚好在 $T$ 时刻开始的未来 1h K 线（`open_time=T`, `is_closed=0`, 伪造未来极端数值 `volume=8888888.0`, `close=149.0`）。上一根历史已收盘 K 线为 `open_time=T-1h`, `volume=1000.0`, `close=101.0`。
- **实测输出**：
  - 提取特征使用 `bar_volume = 1000.0`（非 8888888.0）；
  - `bar_close = 101.0`（非 149.0）；
  - 审计字段 `bar_available_at_ms = 1600000000000 <= T`。
- **判定结论**：**PASS**。真实入口物理剔除未收盘 K 线，上一根已收盘 K 线被准确消费。

#### 1.1b 异常篡改测试：显式 `is_closed` 校验 (`VERIFY-PIT-1-1B-EXPLICIT-IS-CLOSED-FILTER`)
- **注入设计**：构造一根 `is_closed=0` 的异常蜡烛，将其 `close_time` 伪造在决策时刻之前（看似时间戳已闭合，但业务标志为未收盘，`volume=7777777.0`, `close=199.0`）。
- **实测输出**：真实入口 `_prepare_candles` 预处理显式执行 `df = df[df["is_closed"] == 1]`，该异常 bar 被物理剔除，实际消费 `bar_volume = 1000.0`, `bar_close = 101.0`。
- **判定结论**：**PASS**。具备时间戳与闭合状态双重硬拦截。

#### 1.2 未结束 5m 统计桶防护验证 (`VERIFY-PIT-1-2-UNFINISHED-5M-BUCKET-REJECTION`)
- **注入设计**：在决策时刻 $T$ 注入 `ts = T` 的 5m 统计桶（该桶区间为 $T \sim T+5m$，在决策时刻尚未结束，伪造 `oi_base = 99999.0`）。已结束的上一桶为 `ts = T - 5m`，`oi_base = 1359.0`。
- **实测输出**：
  - 提取特征 `oi_base = 1359.0`（与已结束桶完全一致，非 99999.0）；
  - `metrics_available_at_ms = 1600000000000 <= T`。
- **判定结论**：**PASS**。真实入口遵循桶区间结束时间，未结束桶杜绝穿透。

#### 1.3 已结束但未发布数据防护验证 (`VERIFY-PIT-1-3-UNPUBLISHED-DELAYED-DATA-REJECTION`)
- **注入设计**：虽然 $T - 5m$ 桶在 $T$ 时刻区间结束，但调用入口时声明人工延迟 `metrics_declared_lag_ms = 60_000`（60秒）。此时该桶可用时点为 $T + 60s > T$，在决策时刻属于“已结束但尚未发布”状态。
- **实测输出**：真实入口自动回退读取更早且已发布的桶（$T - 10m$ 桶），提取 `oi_base = 1358.0`（正是 $T - 10m$ 桶对应数值）。
- **判定结论**：**PASS**。未发布数据被正确推迟，不提前读取。

---

### 验证项 2: 可用时点未知时拒用或明确不具正式资格

#### 2.1 未知时点在 `strict` 模式下安全拒用 (`VERIFY-PIT-2-1-UNPROVEN-STRICT-REFUSAL`)
- **实测设定**：对包含未证明数据源（Metrics 5m / OI 5m 无发布时点证据）的历史输入，传参 `mode="strict"` 调用真实入口。
- **实测输出**：
  - `pit_status = "REFUSED"`；
  - `pit_qualified = False`，`formal_eligible = False`；
  - `has_metrics = False`, `has_oi = False`, `oi_base = None`；
  - 能证明闭合的 1h K 线照常处理：`has_c1h = True`, `bar_close = 101.0`；
  - `formal_block_reasons = ['metrics_5m:refused:unproven_source:unproven', 'oi_5m:refused:unproven_source:unproven', 'coverage_missing_metrics']`；
  - 进程平稳返回，未发生未捕获异常崩溃（crashing）。
- **判定结论**：**PASS**。未证明源在严格模式下被安全拒用并如实记录原因，未粗暴导致系统崩溃。

#### 2.2 未知时点在 `declared_assumption` 模式下明确不具正式资格 (`VERIFY-PIT-2-2-UNPROVEN-DECLARED-UNQUALIFIED`)
- **实测设定**：默认研究模式（`mode="declared_assumption"`）调用真实入口。
- **实测输出**：
  - 数据齐全，成功计算出特征数值：`oi_base = 1359.0`；
  - 审计字段明确标记：`pit_status = "ASSUMED"`，`pit_qualified = False`，`formal_eligible = False`；
  - 阻断原因如实记录：`metrics_5m:assumed_bucket_end_as_available:按假设时刻取用（无发布时点证据，不得视为已证明）`。
- **判定结论**：**PASS**。覆盖 100% 不等于 PIT 可证，正式资格被代码彻底否决。

#### 2.3 声明延迟不提升资格，零延迟直接拒绝 (`VERIFY-PIT-2-3-DECLARED-LAG-NO-UPGRADE-AND-ZERO-REJECT`)
- **实测设定**：
  1. 传入 `metrics_declared_lag_ms = 30_000`；
  2. 传入 `metrics_declared_lag_ms = 0`。
- **实测输出**：
  1. 声明延迟 30s 时，`formal_eligible` 恒为 `False`（未被洗白为合格）；
  2. 声明 `lag = 0` 时，函数直接抛出 `ValueError: metrics_5m: declared_lag_ms 必须是正整数（0 不是证据，等价于把桶结束当发布时间）`。
- **判定结论**：**PASS**。声明延迟仅推迟取数时刻，不升级资格等级；0 延迟被物理阻断。

#### 2.4 正式用途物理阻断 (`VERIFY-PIT-2-4-REQUIRE-QUALIFIED-BLOCKS-FORMAL`)
- **实测设定**：调用真实入口传参 `require_qualified = True`。
- **实测输出**：由于 Metrics 与 OI 属于 unproven 源，函数物理抛出 `PitUnavailable: PIT_UNAVAILABLE source=BTCUSDT@1600000000000 decision_time=1600000000000: metrics_5m / oi_5m 未取得正式合格资格`。
- **判定结论**：**PASS**。正式调用链路具备物理熔断门禁。

---

### 验证项 3: 合法历史输入可正常处理

#### 3.1 真实历史样本端到端处理 (`VERIFY-PIT-3-1-LEGITIMATE-HISTORY-PROCESSED`)
- **输入样本**：真实候选 `COTIUSDT`（`snapshot_time_ms = 1640228400000`）。
- **实测输出**：
  - `coverage_status = "COMPLETE"`；
  - `bar_volume = 159031613.0 > 0`, `bar_close = 0.3985 > 0`；
  - `candle_24h_bars = 24`（24 根 1h K 线完整提取）；
  - `delta_oi_base_24h = 0.52265`（OI 指标正常计算）；
  - 成功返回 78 个特征字段，未产生任何未捕获异常。
- **判定结论**：**PASS**。合法历史数据提取完全合规，系统未陷入一律拒绝的极端。

#### 3.2 决策时钟自洽性验证 (`VERIFY-PIT-3-2-DECISION-CLOCK-CONSISTENCY`)
- **实测输出**：
  - `decision_time_ms = 1640228400000`（与候选快照时间戳严格一致）；
  - `event_time_ms = 1640224800000`（决策时刻前 1 小时）；
  - `close_time_ms = 1640228399999`（严格等于 `event_time + 1h - 1ms`，且早于决策时刻）。
- **判定结论**：**PASS**。时钟自洽严密，无挪动信号时刻现象。

---

### 验证项 4: 实际特征非空，未来扰动与截断输入不改变此前结果

#### 4.1 实际特征非空强断言 (`VERIFY-PIT-4-1-NON-EMPTY-FEATURE-ASSERTION`)
- **断言设计**：对真实历史样本提取的特征表，提取关键业务数值属性（`bar_open`, `bar_high`, `bar_low`, `bar_close`, `bar_volume`, `quote_cvd_24h`, `delta_oi_base_24h`, `top_position_ls_ratio`, `taker_buy_ratio_24h` 等 9 项核心指标），显式断言值非 None 且非 NaN。
- **实测输出**：9 项核心数值指标全部为有效浮点数值，非空强校验通过。
- **判定结论**：**PASS**。杜绝了 `0 == 0` 或全空特征造成的假通过漏洞。

#### 4.2 未来剧烈扰动不变性验证 (`VERIFY-PIT-4-2-FUTURE-PERTURBATION-INVARIANCE`)
- **注入设计**：
  1. 将决策时刻 $T$ 之后已有的 K 线成交量放大 3 倍、收盘价篡改至 999.0；
  2. 将决策时刻之后的 Metrics 持仓量放大 7 倍、大户多空比篡改至 9.9；
  3. 将决策时刻之后的 OI 放大 7 倍；
  4. 额外追加未来 10 小时的极端蜡烛（成交量 555 万、价格 999.0）与极端 OI 桶。
- **实测对比**：重新调用真实入口提取决策时刻 $T$ 的特征向量，与基准特征逐字段全等比对 42 项业务属性（`FEATURE_COMPARE_KEYS`）。
- **实测输出**：**差异字段数为 0**（`pert_diffs = []`）。
- **判定结论**：**PASS**。真实因子提取入口严格具备未来扰动不变性。

#### 4.3 物理截断输入完全等价性验证 (`VERIFY-PIT-4-3-TRUNCATION-FULL-EQUIVALENCE`)
- **截断设计**：将输入历史数据严格物理截断至决策时刻 $T$（完全剔除 `open_time >= T` 的 K 线与 `ts >= T` 的 Metrics/OI 桶）。
- **实测对比**：截断输入提取的决策特征向量与全量输入提取的决策特征向量逐字段全等比对 42 项业务属性。
- **实测输出**：**差异字段数为 0**（`trunc_diffs = []`）。
- **判定结论**：**PASS**。证实算法只消费决策时刻及此前数据，未来截断前后完全等价。

---

### 验证项 5: 四组实验入口确实消费受控特征，不能接受旧产物冒充合格

#### 5.1 四组实验入口消费受控特征 (`VERIFY-PIT-5-1-FOUR-GROUPS-CONSUME-CONTROLLED-FEATURES`)
- **被测入口**：`evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py::run_four_groups`。
- **实测输出**：
  - `groups_consumed_from = "controlled_entry_only"`；
  - `formal_eligible = False`；
  - `formal_statement = "研究结论，不具备正式资格（不得用于 Gate / 上线 / 正式回测通过判定）"`；
  - 四组（G1 基线、G2 主动成交、G3 +OI、G4 +大户）整体指标结构齐全完整。
- **判定结论**：**PASS**。四组实验入口唯一绑定受控特征，资格状态忠实呈现。

#### 5.2 物理拒绝旧产物与非受控特征表（双保险验证） (`VERIFY-PIT-5-2-REJECT-UNCONTROLLED-LEGACY-TABLE`)
- **注入设计**：构造缺少 `pit_status`、`formal_eligible` 等 PIT 契约字段的旧特征表，分别输入：
  1. `flow_oi_four_groups_v2.py::run_four_groups`；
  2. `FLOW-OI-01/generate_flow_oi_01.py::step4_run_four_groups`。
- **实测输出**：
  1. `flow_oi_four_groups_v2` 抛出 `UncontrolledFeatureError: 特征表缺少 PIT 字段...这不是受控入口的产物，不得用于四组实验`；
  2. `generate_flow_oi_01` 抛出 `RuntimeError: 四组实验拒绝非受控特征表...旧路径不得生成正式合格结果`。
- **判定结论**：**PASS**。双保险机制彻底杜绝旧产物冒充受控合格特征。

---

## 三、第一部分复验结论

经过 13 项独立排他测试，确证：
- **`FRAME-PIT-02` 真实因子提取入口与四组实验入口的所有 PIT 防护均已严密生效并闭环！**
- **未收盘 K 线、未结束桶、未发布数据穿透已彻底消除；**
- **未证明源的正式资格已被代码级剥夺（`formal_eligible 恒为 False`）；**
- **合规历史数据可正常处理，且严格满足特征非空、未来扰动不变性与截断等价性。**
- **第一部分复验判定：全面通过（13/13 PASS）。**
