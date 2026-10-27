# FACTOR-ENTRY-01 实验计划与冻结协议 (Frozen Experiment Plan)

## 1. 工单背景与核心目标
- **工单编号**：FACTOR-ENTRY-01
- **核心问题**：用户逐笔观察到放量买入后回落（入场即弱或冲高浮盈快速回吐）。当前策略信号仅使用 OHLCV + 6h OI，未充分利用已下载的 Taker 主动买卖流与多空持仓结构数据。
- **任务目标**：利用已下载指标（candles 微观成交流、metrics 账户/持仓结构、funding 资金费率）对入场信号质量进行全链路、隔离离线验证。
- **执行边界**：
  1. 纯离线研究，隔离测试，不修改任何实盘与模拟开仓闸门，不清理账本。
  2. 严格保护生产代码，所有产物和中间脚本只写在 `evidence/FACTOR-ENTRY-01/`，不得改动 UI、backend、正式策略源码、共享内核或生产数据。
  3. WorkBuddy 负责的 `evidence/DATA-UI-01` 等采集/API/UI 仅可只读引用，绝不修改。
  4. 结论以客观数据为准（结论可以是无改善，未经审核不替换正式策略）。

## 2. 第一阶段：数据契约与时间可用性 (PIT 契约)
### 2.1 标的池界定
- 仅纳入币安官方合约市场的单一加密货币标的（USDT 永续合约）。
- 排除复合指数及非真实代币合约（例如 `BTCDOMUSDT` 等指数）。
- 不照搬历史 528 旧分母，以实际同时具备基础 K 线与指标的官方加密币为基准。

### 2.2 字段定义、单位与数据来源
1. **微观价量与主动流 (Candles)**:
   - 来源：`数据/市场数据/parquet/candles/binance/um/{symbol}/{timeframe}/*.parquet`
   - 字段与单位：
     - `volume` (基础成交量，标的币)
     - `quote_volume` (成交额，USDT)
     - `taker_buy_volume` (主动买入量，标的币)
     - `taker_buy_quote_volume` (主动买入额，USDT)
     - `trade_count` (成交笔数，整数)
   - 衍生特征：
     - `taker_buy_ratio` = taker_buy_volume / volume (主动买入量占比，[0, 1])
     - `taker_buy_quote_ratio` = taker_buy_quote_volume / quote_volume (主动买入额占比，[0, 1])
     - `taker_ls_ratio` = taker_buy_volume / max(volume - taker_buy_volume, 1e-8) (主动买卖量比)
     - `bar_return` = (close - open) / open (突破 K 线涨幅)
     - `upper_shadow_ratio` = (high - max(open, close)) / max(high - low, 1e-8) (上影线比例，冲高遇阻度)
     - `breakout_atr_dist` = (close - box_high) / atr_4h (突破收盘距箱体上沿的 4h ATR 距离)
2. **多空结构与持仓特征 (Metrics)**:
   - 来源：`数据/市场数据/parquet/metrics/binance/um/{symbol}/*.parquet` (5分钟粒度)
   - 字段与口径限制：
     - `count_long_short_ratio` (全市场多空账户人数比)：散户/全市场账户多空分布，仅反映账户数量偏向，不代表资金量。
     - `sum_toptrader_long_short_ratio` / `top_position_ls_ratio` (大户持仓多空比)：持仓量前 20% 账户的净持仓量多空比。
     - `count_toptrader_long_short_ratio` (大户账户多空比)：大户账户多空人数比。
     - `sum_taker_long_short_vol_ratio` (主动成交量多空比)：周期内主动买入/主动卖出总量之比。
     - `sum_open_interest` / `sum_open_interest_value` (未平仓合约总量与美元价值)。
     - **特别注意（反拟人化与认知契约）**：指标比率不是聪明钱身份/金额/成本均价！大户多空比是币安排名前 20% 账户持仓汇总，多头与空头大户通常互相对冲，不能将大户单向视作神级内幕资金。
3. **资金费率 (Funding)**:
   - 来源：`数据/市场数据/parquet/funding/binance/um/{symbol}.parquet`
   - 字段：`funding_time`, `funding_rate`, `rate_type='settled'`
   - 限制：严格区分预测费率与已结算费率。历史回测只能使用信号时点前最新已结算的真实费率。

### 2.3 PIT 时间对齐与最大陈旧度 (Max Staleness) 规则
- **信号时间戳 $T_{signal}$**：由状态机判定生成 Candidate 的 K 线收盘时刻（例如 1h K 线结束时刻）。
- **向后 As-Of 对齐**：任何外部特征的可用时间戳 $t \le T_{signal}$。严禁向后借用（未来最近邻）或前向回填未来值！
- **最大陈旧时间 (Max Staleness)**：
  - Metrics (5m 序列)：若 $T_{signal} - t_{metrics} > 30\text{ 分钟}$（1,800,000 ms），标记为数据中断（STALE/MISSING），不得使用更早数据伪造。
  - Funding (8h 结算)：若 $T_{signal} - t_{funding} > 12\text{ 小时}$，标记为陈旧。
  - 缺失值一律保持 NaN，不填 0，不制造历史伪数据。若指标缺失，该事件单列归类为 MISSING_DATA 组，不强行纳入有效因子统计。
  - 历史 Parquet 中 `available_at` 为 null，明确说明 PIT 假设：以 K 线结束与 metrics 产生时戳作为历史已知时点，如实揭示无法验证官方毫秒级发布延迟的局限性。

## 3. 第二阶段：唯一事件去重与微观路径归因
### 3.1 消除旧 296 笔窗口截断伪重复 (De-duplication Contract)
- **旧 296 笔缺陷排查**：在 `evidence/FULL_SWEEP_200x100/trades_rich.jsonl` 中，因 200 个重叠滑动窗口（如 group 0, group 1），存在 72 组重复进场（92 笔重复记录），共 204 个唯一 `(symbol, t_in)`。在较早 group 中，由于窗口结束触发 `force_close_eod` 导致持仓被截断，而下一 group 却正常持有至 `max_holding_expired`，产生了虚假的不同离场。
- **唯一事件体系构建**：
  - 统一建立唯一事件 ID：`event_id = f"{symbol}_{t_candidate}"`。
  - 完整包含所有漏斗事件：`ALL_CANDIDATES`（通过箱体、扫低、回踩突破到达 CANDIDATE 状态的所有事件，含最终被引擎拒绝者）。
  - 对成交的入场事件，固定唯一的真实全生命周期（不受人为窗口边界强制截断），若遭遇数据集边界则独立标记 `WINDOW_BOUNDARY_TRUNCATED`。

### 3.2 价格推进与入场后微观路径
- **特征集关联**：
  1. 主动买入强度：15m / 1h 突破 K 线的 `taker_buy_ratio`, `taker_buy_quote_ratio`, `taker_ls_ratio`。
  2. 几何突破形态：突破 K 线的上影线占比 `upper_shadow_ratio`、当根涨幅 `bar_return`、距箱体上沿 ATR 距离 `breakout_atr_dist`。
  3. 持仓拥挤度：对齐信号时刻的 `retail_account_ls_ratio`, `top_position_ls_ratio`, `count_toptrader_ls_ratio`, `taker_ls_vol_ratio`, `funding_rate`, 以及 4h OI 变化率 `delta_oi_4h`。
  4. 大周期背景状态 (Regime)：突破时刻前已闭合的日线 EMA(20)/EMA(50) 趋势状态（多头趋势 / 震荡 / 空头反弹）。
- **微观路径解耦与评估（后验仅作评价，不作选参未来信息）**：
  - 统计入场后 15m、1h、4h 的超额收益分布。
  - 记录持仓期间的真实 `MAE` (最大不利偏离) 与 `MFE` (最大有利偏离) 以及达到的时间戳顺序。
  - 明确解耦两类回落：
    1. **入场后即弱 (Early Adverse)**：入场后未产生有效 MFE（MFE < 0.5 ATR）即直接下杀跌破入场价，发生早期深回撤。
    2. **浮盈回吐 (MFE Pullback)**：曾产生显著 MFE（MFE > 1.5 ATR），但因未达到激进止盈点而回落保本或止损。

## 4. 第三阶段：消融对比与严谨验证方案 (Ablation Protocol)
### 4.1 实验对照组设计（最多 4 组）
1. **组 1：冻结基线组 (Baseline)**：
   - 保持当前 event_trend_v0_1 原始规则（箱体 + 扫低 + 回踩 + 突破时 1h 阳线放量 1.5x + 6h OI 增幅 2%）。
2. **组 2：主动流量过滤组 (Flow Filter)**：
   - 在基线基础上，增加突破 K 线主动买入真实驱动力过滤（排除冲高长上影线虚假突破，且要求突破 K 线主动买入额占比显著高于中位数）。
3. **组 3：持仓与拥挤度过滤组 (Crowding Filter)**：
   - 在基线基础上，排除极端拥挤（全市场散户极端做多、大户仓位极度做空对冲、或资金费率处于极端高位）。
4. **组 4：合并精选组 (Combined Filter)**：
   - 融合主动流量与持仓结构健康度，日线多头状态优先作为分层。

### 4.2 样本隔离与防止过拟合规约
- **样本时间隔离**：
  - **训练探索期 (In-Sample Training)**: 2021-12-01 ~ 2023-12-31（确定具体阈值与特征有效性）。
  - **验证隔离期 (Out-of-Sample Validation)**: 2024-01-01 ~ 2024-12-31（测试泛化能力）。
  - **末段留出期 (Strict Test Holdout)**: 2025-01-01 ~ 2026-10-18（完全冻结参数，只做终局验收）。
- **严格复用共享内核**：
  - 严禁另写撮合逻辑！严格复用 `策略/WyckoffMaster/trading_core`。
  - 成本模型、滑点、手续费（Taker 0.05% / 0.04%）、资金费结算、初始资金与仓位预算（固定 2000 USDT，固定风险预算与 ATR 止损）完全一致，绝不通过调整止损距离或降低费率来美化收益。
  - 在共同可用样本上配对 (Paired Comparison)，并完整列报缺失字段被排除数量。

### 4.3 报告指标规约
- 拒绝任何单一胜率误导！必须综合报告：
  - 候选事件总数、拒单数（引擎拒单 vs 因子拒单）、唯一入场数。
  - 早期深回落比率 (Early Adverse Rate)、浮盈回吐比率。
  - 净盈亏、期望收益 (Expectancy/R)、盈亏比 (Profit Factor)、真实胜率。
  - 平均 MAE / 平均 MFE。
  - 漏掉的赢家 (Missed Big Winners)。
  - 利润前 5 笔集中度 (Top 5 Profit Concentration)。
  - 分年份与市场状态分层统计。
  - 若样本量过小，客观承认置信区间不足，严禁以偏概全。
