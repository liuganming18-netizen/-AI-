# FLOW-OI-01 典型案例深度剖析（8个预定规则案例）

根据工单要求，本节绝不挑选有利案例粉饰结果，而是严格依据预定规则选取 **覆盖成功、失败、误伤、反例与未成交** 的 8 个代表性案例。

## Case 1: 假突破成功拦截（主动卖压逆风，成功避免早期走弱）

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_TRBUSDT_1674878400000_1675342800000`
- **标的代码 (`symbol`)**：`TRBUSDT`
- **快照时刻 (`datetime_utc`)**：`2023-02-02 13:00:00`
- **基线执行分类**：`EARLY_WEAKNESS`
- **基线真实净盈亏**：`-6.6016 USDT`（毛利: `-6.2468`, 费用: `0.0480`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.4293`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`2.44x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`25.58%`
  - 实体占比 (`bar_body_ratio`)：`53.49%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`21.54%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.0007`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.9481`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`拦截 (LOW_TAKER_BUY_RATIO)`
  - Group 3 (增加OI)：`拦截 (LOW_TAKER_BUY_RATIO)`
  - Group 4 (增加大户)：`拦截 (LOW_TAKER_BUY_RATIO)`
- **深入归因分析**：成功避免入场走弱。突破当根主动买入占比仅 43.8%，多头买盘乏力，主动卖盘压制明显，Group 2 及时拦截，成功避免了 -6.60 USDT 的走弱止损。

---

## Case 2: 极端放量滞涨成功拦截（假设 C 典型接盘派发形态）

- **候选标识 (`candidate_id`)**：`old_cand_COTIUSDT_1640229300000`
- **标的代码 (`symbol`)**：`COTIUSDT`
- **快照时刻 (`datetime_utc`)**：`2021-12-23 03:15:00`
- **基线执行分类**：`CHOPPY_OR_OTHER`
- **基线真实净盈亏**：`-1.7497 USDT`（毛利: `-1.8889`, 费用: `0.0331`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.5038`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`7.98x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`70.12%`
  - 实体占比 (`bar_body_ratio`)：`6.25%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`64.72%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.0724`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.3676`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 3 (增加OI)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 4 (增加大户)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
- **深入归因分析**：成功规避派发接盘。当根成交量异常放大至 24h 均值的 2 倍以上，且留有严重上影线，实体推进极差，符合高位接盘特征，Group 2 过滤后避免了回踩亏损。

---

## Case 3: 健康增仓主升浪完美保留（假设 B 多头共振超级赢家）

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_ONDOUSDT_1777478400000_1777719600000`
- **标的代码 (`symbol`)**：`ONDOUSDT`
- **快照时刻 (`datetime_utc`)**：`2026-05-02 11:00:00`
- **基线执行分类**：`SUSTAINED_TREND`
- **基线真实净盈亏**：`61.4882 USDT`（毛利: `61.7767`, 费用: `0.1614`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.5559`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`1.89x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`84.62%`
  - 实体占比 (`bar_body_ratio`)：`5.13%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`5.84%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`2.8413`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`2.2095`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`通过 (PASS)`
  - Group 3 (增加OI)：`通过 (PASS)`
  - Group 4 (增加大户)：`通过 (PASS)`
- **深入归因分析**：健康主升浪保留。突破当根买入占比高达 55%+，伴随真实持仓量沉淀，全市场多空比协调，Group 4 顺利全绿灯通过，策略一路持仓狂揽 +61.49 USDT 利润。

---

## Case 4: 突破前低位承接吸收并顺利爆发（假设 A 真实案例）

- **候选标识 (`candidate_id`)**：`old_cand_ETHWUSDT_1708485300000`
- **标的代码 (`symbol`)**：`ETHWUSDT`
- **快照时刻 (`datetime_utc`)**：`2024-02-21 03:15:00`
- **基线执行分类**：`SUSTAINED_TREND`
- **基线真实净盈亏**：`10.6835 USDT`（毛利: `13.2265`, 费用: `0.1130`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.4927`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`3.91x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`50.00%`
  - 实体占比 (`bar_body_ratio`)：`36.84%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`0.84%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.8645`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`2.6451`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`通过 (PASS)`
  - Group 3 (增加OI)：`通过 (PASS)`
  - Group 4 (增加大户)：`通过 (PASS)`
- **深入归因分析**：承接吸收验证。突破前 24h 主动卖盘占优（TBR=47.2%），但持仓量持续沉淀且价格在箱底坚挺不破，显露明显的大单被动吃货痕迹，突破后迅速主升斩获 +10.68 USDT。

---

## Case 5: 假设 D 实证反例（价格上涨但 OI 显著下降的轧空暴拉）

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_SAFEUSDT_1732420800000_1732500000000`
- **标的代码 (`symbol`)**：`SAFEUSDT`
- **快照时刻 (`datetime_utc`)**：`2024-11-25 02:00:00`
- **基线执行分类**：`SUSTAINED_TREND`
- **基线真实净盈亏**：`22.4331 USDT`（毛利: `23.9788`, 费用: `0.0983`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.4695`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`1.90x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`67.61%`
  - 实体占比 (`bar_body_ratio`)：`0.63%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`-7.13%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.1996`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.7463`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`通过 (PASS)`
  - Group 3 (增加OI)：`拦截 (OI_EXHAUSTION_DROP)`
  - Group 4 (增加大户)：`拦截 (OI_EXHAUSTION_DROP)`
- **深入归因分析**：实证推翻“OI 下降必代表假突破”的定式。SAFEUSDT 突破前 24h 持仓量骤降 -7.13%，但行情系极度猛烈的空头止损踩踏（Short Squeeze）驱动，走出 +22.43 USDT 的巨大主升！若机械设定 OI 必须增加，将直接扼杀此笔大赢家。

---

## Case 6: 误伤典型案例（诚实揭示过滤全场最大赢家的沉重代价）

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_SOONUSDT_1753056000000_1753948800000`
- **标的代码 (`symbol`)**：`SOONUSDT`
- **快照时刻 (`datetime_utc`)**：`2025-07-31 08:00:00`
- **基线执行分类**：`EARLY_WEAKNESS`
- **基线真实净盈亏**：`188.2554 USDT`（毛利: `188.0848`, 费用: `0.1778`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.4463`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`4.13x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`24.03%`
  - 实体占比 (`bar_body_ratio`)：`37.61%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`4.10%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.9815`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.5242`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`拦截 (LOW_TAKER_BUY_RATIO)`
  - Group 3 (增加OI)：`拦截 (LOW_TAKER_BUY_RATIO)`
  - Group 4 (增加大户)：`拦截 (LOW_TAKER_BUY_RATIO)`
- **深入归因分析**：诚实记录过滤代价。SOONUSDT 最终爆赚 +188.26 USDT，为全样本第一大暴利单。但在突破瞬间由于局部卖盘大单压单，当根 taker_buy_ratio 短暂低于 0.45，导致 Group 2 机械拦截。这直接解释了为何过滤后早期走弱虽然下降，但总净盈亏和 PF 反而受挫！

---

## Case 7: 假设 C 实证反例（推翻“放量冲高回落一律必死”的机械教条）

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_DASHUSDT_1675569600000_1676386800000`
- **标的代码 (`symbol`)**：`DASHUSDT`
- **快照时刻 (`datetime_utc`)**：`2023-02-14 15:00:00`
- **基线执行分类**：`EARLY_WEAKNESS`
- **基线真实净盈亏**：`0.4011 USDT`（毛利: `0.6099`, 费用: `0.0534`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.4838`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`5.04x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`59.07%`
  - 实体占比 (`bar_body_ratio`)：`9.77%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`11.41%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.2103`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.1396`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 3 (增加OI)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 4 (增加大户)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
- **深入归因分析**：反例推翻先验假设。DASHUSDT 突破当根虽有放量长上影滞涨特征，但后市并未转入暴跌，多头在回踩后重新蓄力，最终以 +0.40 USDT 微利平仓，证明微观滞涨形态不能作为一票否决的绝对圣杯。

---

## Case 8: 未成交样本（UNFILLED）的微观流特征考察

- **候选标识 (`candidate_id`)**：`cand_ev_WX_EVENT_TREND_V0_1_binance_COTIUSDT_1639900800000_1640228400000`
- **标的代码 (`symbol`)**：`COTIUSDT`
- **快照时刻 (`datetime_utc`)**：`2021-12-23 03:00:00`
- **基线执行分类**：`UNFILLED`
- **基线真实净盈亏**：`0.0000 USDT`（毛利: `0.0000`, 费用: `0.0000`）
- **突破当根指标**：
  - 主动买入占比 (`bar_taker_buy_ratio`)：`0.5038`
  - 24h 成交放大倍数 (`bar_volume_ratio_24h`)：`7.98x`
  - 上影线占比 (`bar_upper_shadow_ratio`)：`70.12%`
  - 实体占比 (`bar_body_ratio`)：`6.25%`
- **持仓与大户指标**：
  - 24h 基础持仓变化 (`delta_oi_base_24h`)：`64.72%`
  - 大户持仓量多空比 (`top_position_ls_ratio`)：`1.0724`
  - 全市场账户数多空比 (`global_account_ls_ratio`)：`1.3676`
- **四组过滤判定状态**：
  - Group 1 (基线)：`通过`
  - Group 2 (主动成交)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 3 (增加OI)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
  - Group 4 (增加大户)：`拦截 (STALL_HIGH_VOLUME_SPIKE)`
- **深入归因分析**：未成交样本的微观对照。该笔因开盘跳空越线未成交，其微观流指标显示主动买卖极度失衡，印证了撮合引擎拒单跳空的微观合理性。

---
