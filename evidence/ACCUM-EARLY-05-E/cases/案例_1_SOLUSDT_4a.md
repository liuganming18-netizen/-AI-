# 真实事件逐时点行为核对案例 1：SOLUSDT（方案 4a）

> **免责声明**：本案例采用工程测试配置 (fixture) 运行，旨在核实代码时点、状态迁移与判据行为，**不输出任何正式策略绩效**。

- **候选 ID**：`cd_ev_4a_binance_SOLUSDT_box_SOLUSDT_1785556799999_1785556799999_s1`
- **事件 ID**：`ev_4a_binance_SOLUSDT_box_SOLUSDT_1785556799999_1785556799999`
- **标的**：`SOLUSDT`
- **方案**：`4a`
- **确认时刻**：`2026-08-02T02:59:59Z`
- **入场 Bar 开盘时刻**：`2026-08-02T03:15:00Z`
- **入场参考价**：`73.1`
- **压制位**：`73.08`
- **候选状态**：`WAIT_ENTRY`（`条件成立，待委托`）

## 1. 状态机迁移轨迹（时间序列可见）

| 序号 | 从状态 | 到状态 | 触发时刻 (UTC) | 迁移原因 |
|---|---|---|---|---|
| 1 | None | BOX | 2026-08-01T03:59:59Z | 事件建档（初始锚点冻结） |
| 2 | SWEEP | SWEEP | 2026-08-02T01:59:59Z | 扫低收回成立 |
| 3 | None | WAIT_ENTRY | 2026-08-02T02:59:59Z | 候选生成 |

## 2. 候选判定条件真值表（可见输入与判定结果）

| 判定项 | 观测值 | 门槛要求 | 判定结果 | 备注 |
|---|---|---|---|---|
| `sweep_lowest_below_reference` | `72.07` | `< 72.26` | **pass** | - |
| `sweep_overshoot_within` | `72.07` | `>= 71.80085671` | **pass** | - |
| `sweep_recover_close` | `72.62` | `>= 72.35182866` | **pass** | - |
| `local_resistance` | `73.08` | `{'mode': 'prior_n_high', 'n': 20, 'window_bars': 20}` | **pass** | - |
| `confirm_above_resistance` | `73.1` | `1h_close > 73.08` | **pass** | - |
| `sell_pressure` | `None` | `require=false` | **not_required** | - |
| `observe_deadline_hours` | `23.0` | `<= 168` | **pass** | - |
| `entry_15m_bar_exists` | `2026-08-02T03:15:00Z` | `存在 open_time>=2026-08-02T03:14:59Z 的已闭合 15m bar` | **pass** | - |
| `entry_delay_respected` | `2026-08-02T03:15:00Z` | `bar_open >= confirm+15min` | **pass** | - |
| `entry_price_deviation` | `0.0` | `|x| <= 0.02` | **pass** | - |
| `entry_bar_available_at_proven` | `None` | `有值（否则只能声称计算不超前）` | **not_proven** | 成交 bar 来自 cold，该层没有 available_at 证据 ⇒ 只能声称「计算不超前」，不能证明「当时可得」（缺口已落 data_gap，不补造） |
| `oi_role` | `evidence_only` | `required ⇒ 必须通过；evidence_only ⇒ 只记录` | **not_required** | 本单未把 OI 接进入场门槛，角色由配置决定 |

## 3. 04-A 规则与 04-B 实现差异分析

| 检查点 | 04-B 实际实现行为 | 04-A 规则规范要求 | 潜在策略影响 |
|---|---|---|---|
| 扫低对象 s_loc > L 约束 | 未校验 s_loc 是否高于箱底 L (仅取前20根最低值) | 必须 s_loc > box['L']，否则属于破L箱底扫低，不得计为提前入场 | 语义偏离／门槛遗漏 |
| 局部压制位定义与合格性 | 取 prior_20_high: 73.08，未检查离箱底箱顶空间 | 要求 swing_high 摆动高点，且合格性 L + 0.5A <= R <= H - 0.5A | 语义偏离／门槛遗漏 |
| 承接与卖压指标时序 | 在确认收上压制位时 (confirm) 检查 sell_pressure (当前测试配置为 require=false) | 必须在扫低根 (sweep) 检查承接主动买占比 tbr 与主动卖压 sell | 语义偏离／门槛遗漏 |
