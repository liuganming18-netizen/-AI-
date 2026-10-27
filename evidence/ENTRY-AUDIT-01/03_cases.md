# ENTRY-AUDIT-01 · 8 笔案例（按预先声明的规则抽取）

## 抽取规则（先声明后执行）

- **A1 早期走弱-漏判**：42条回退ATR组中，原判非EARLY_WEAKNESS、独立ATR下变为EARLY_WEAKNESS者；按 (mae_r_snap / mae_r_theirs) 降序取第1，并列取 event_id 字典序最小
- **A2 早期走弱-深跌**：原判EARLY_WEAKNESS且独立ATR下仍为EARLY_WEAKNESS；按 mae_r_snap 降序取第1，并列取 event_id 字典序最小
- **A3 早期走弱-顺序不可判**：原判EARLY_WEAKNESS、独立ATR下首触为UNKNOWN_SAME_BAR；取 event_id 字典序最小
- **B1 浮盈回吐-最大浮盈**：分类为MFE_PULLBACK；按 mfe_r_full 降序取第1，并列取 event_id 字典序最小
- **B2 浮盈回吐-4h内浮盈最大却亏**：net_pnl<0；按 mfe_r_4h_theirs 降序取第1，并列取 event_id 字典序最小
- **C1 盈利-最大净利**：分类为SUSTAINED_TREND；按 net_pnl 降序取第1
- **C2 盈利-标签与结果背离**：原判EARLY_WEAKNESS但 net_pnl>0；按 net_pnl 降序取第1
- **D1 缺失/边界**：exit_why=force_close_eod（数据末端强平）优先；若无则取42条回退ATR组中 mae_r_snap-mae_r_theirs 最大者

## 案例总表

| 槽位 | 层 | 标的 | 入场UTC | 入场价 | 4h最高 | 4h最低 | MAE(R,原) | MAE(R,独立) | 首触(原) | 首触(独立) | 分类(原) | 分类(独立) | 净盈亏 |
| :-- | :-- | :-- | :-- | --: | --: | --: | --: | --: | :-- | :-- | :-- | :-- | --: |
| A1 | 早期走弱-漏判 | DOGEUSDT | 2025-01-17 09:15:00 | 0.41697 | 0.4184 | 0.40812 | 0.089 | 0.733 | NEITHER_REACHED_0.3ATR | FIRST_ADVERSE | CHOPPY_OR_OTHER | EARLY_WEAKNESS | -9.98 |
| A2 | 早期走弱-深跌 | AINUSDT | 2026-03-10 05:15:00 | 0.06172 | 0.06219 | 0.05077 | 4.896 | 8.844 | FIRST_ADVERSE | UNKNOWN_SAME_BAR | EARLY_WEAKNESS | EARLY_WEAKNESS | +9.57 |
| A3 | 早期走弱-顺序不可判 | 1000XECUSDT | 2025-10-26 13:15:00 | 0.017096 | 0.01719 | 0.01583 | 2.552 | 5.471 | FIRST_ADVERSE | UNKNOWN_SAME_BAR | EARLY_WEAKNESS | EARLY_WEAKNESS | -9.88 |
| B1 | 浮盈回吐-最大浮盈 | 1000LUNCUSDT | 2023-06-04 03:15:00 | 0.09213 | 0.0934 | 0.0888 | 1.987 | 3.316 | FIRST_FAVORABLE | UNKNOWN_SAME_BAR | MFE_PULLBACK | MFE_PULLBACK | -3.07 |
| B2 | 浮盈回吐-4h内浮盈最大却亏 | ARUSDT | 2022-11-03 01:15:00 | 13.947 | 17.776 | 13.769 | 0.590 | 0.484 | UNKNOWN_SAME_BAR | UNKNOWN_SAME_BAR | CHOPPY_OR_OTHER | CHOPPY_OR_OTHER | -9.85 |
| C1 | 盈利-最大净利 | INJUSDT | 2023-10-18 05:15:00 | 7.935 | 7.987 | 7.848 | 0.116 | 0.585 | NEITHER_REACHED_0.3ATR | FIRST_FAVORABLE | SUSTAINED_TREND | EARLY_WEAKNESS | +71.34 |
| C2 | 盈利-标签与结果背离 | SOONUSDT | 2025-07-31 08:15:00 | 0.1557 | 0.15557 | 0.14884 | 1.779 | 1.656 | FIRST_ADVERSE | FIRST_ADVERSE | EARLY_WEAKNESS | EARLY_WEAKNESS | +188.26 |
| D1 | 缺失/边界 | METISUSDT | 2026-10-11 20:15:00 | 3.826 | 3.861 | 3.723 | 1.003 | 0.804 | FIRST_ADVERSE | FIRST_ADVERSE | EARLY_WEAKNESS | EARLY_WEAKNESS | +1.43 |

### A1 早期走弱-漏判 · DOGEUSDT

- 事件ID：`DOGEUSDT_1737105300000`
- 入场：2025-01-17 09:15:00 UTC，价 0.41697
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/DOGEUSDT/15m/2025.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/DOGEUSDT/4h/2025.parquet']`
- ATR：原口径 0.099760307（来源 initial_risk_r(回退)）；独立口径 0.012065489
- 箱体：上沿 0.41697 / 下沿 0.31720969；宽度÷ATR = 1.000
- 窗口：16 根；最高 0.4184（第 0 根 2025-01-17 09:15:00）；最低 0.40812（第 1 根 2025-01-17 09:30:00）
- 计算：MFE%=0.343，MAE%=2.122；MAE(R)=原 0.089 / 独立 0.733；MFE(R)=原 0.014 / 独立 0.119
- 4h 收益：第17根收盘口径 -1.648%；窗口末根口径 -1.564%
- 首触：原 NEITHER_REACHED_0.3ATR；独立 FIRST_ADVERSE
- 分类：原 CHOPPY_OR_OTHER；独立 EARLY_WEAKNESS
- 结果：净盈亏 -9.98 USDT，离场 intrabar_stop @ 2025-01-27 06:30:00；全周期 MFE 0.171R / MAE 1.045R

### A2 早期走弱-深跌 · AINUSDT

- 事件ID：`AINUSDT_1773118800000`
- 入场：2026-03-10 05:15:00 UTC，价 0.06172
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/AINUSDT/15m/2026.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/AINUSDT/4h/2026.parquet']`
- ATR：原口径 0.0022364999（来源 box_atr_4h）；独立口径 0.0012380725
- 箱体：上沿 0.04955 / 下沿 0.03454；宽度÷ATR = 6.711
- 窗口：16 根；最高 0.06219（第 0 根 2026-03-10 05:15:00）；最低 0.05077（第 10 根 2026-03-10 07:45:00）
- 计算：MFE%=0.762，MAE%=17.741；MAE(R)=原 4.896 / 独立 8.844；MFE(R)=原 0.210 / 独立 0.380
- 4h 收益：第17根收盘口径 -12.946%；窗口末根口径 -13.302%
- 首触：原 FIRST_ADVERSE；独立 UNKNOWN_SAME_BAR
- 分类：原 EARLY_WEAKNESS；独立 EARLY_WEAKNESS
- 结果：净盈亏 +9.57 USDT，离场 max_holding_expired @ 2026-03-20 05:15:00；全周期 MFE 1.885R / MAE 0.550R

### A3 早期走弱-顺序不可判 · 1000XECUSDT

- 事件ID：`1000XECUSDT_1761483600000`
- 入场：2025-10-26 13:15:00 UTC，价 0.017096
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/1000XECUSDT/15m/2025.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/1000XECUSDT/4h/2025.parquet']`
- ATR：原口径 0.00049610088（来源 box_atr_4h）；独立口径 0.00023138773
- 箱体：上沿 0.01647 / 下沿 0.01361；宽度÷ATR = 5.765
- 窗口：16 根；最高 0.01719（第 0 根 2025-10-26 13:15:00）；最低 0.01583（第 15 根 2025-10-26 17:00:00）
- 计算：MFE%=0.550，MAE%=7.405；MAE(R)=原 2.552 / 独立 5.471；MFE(R)=原 0.189 / 独立 0.406
- 4h 收益：第17根收盘口径 -6.645%；窗口末根口径 -6.762%
- 首触：原 FIRST_ADVERSE；独立 UNKNOWN_SAME_BAR
- 分类：原 EARLY_WEAKNESS；独立 EARLY_WEAKNESS
- 结果：净盈亏 -9.88 USDT，离场 intrabar_stop @ 2025-11-03 10:45:00；全周期 MFE 0.029R / MAE 1.080R

### B1 浮盈回吐-最大浮盈 · 1000LUNCUSDT

- 事件ID：`1000LUNCUSDT_1685847600000`
- 入场：2023-06-04 03:15:00 UTC，价 0.09213
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/1000LUNCUSDT/15m/2023.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/1000LUNCUSDT/4h/2023.parquet']`
- ATR：原口径 0.0016757914（来源 box_atr_4h）；独立口径 0.0010041349
- 箱体：上沿 0.0909 / 下沿 0.0825；宽度÷ATR = 5.013
- 窗口：16 根；最高 0.0934（第 1 根 2023-06-04 03:30:00）；最低 0.0888（第 14 根 2023-06-04 06:45:00）
- 计算：MFE%=1.378，MAE%=3.614；MAE(R)=原 1.987 / 独立 3.316；MFE(R)=原 0.758 / 独立 1.265
- 4h 收益：第17根收盘口径 -2.746%；窗口末根口径 -2.638%
- 首触：原 FIRST_FAVORABLE；独立 UNKNOWN_SAME_BAR
- 分类：原 MFE_PULLBACK；独立 MFE_PULLBACK
- 结果：净盈亏 -3.07 USDT，离场 intrabar_stop @ 2023-06-06 08:00:00；全周期 MFE 2.588R / MAE 0.378R

### B2 浮盈回吐-4h内浮盈最大却亏 · ARUSDT

- 事件ID：`ARUSDT_1667437200000`
- 入场：2022-11-03 01:15:00 UTC，价 13.947
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/ARUSDT/15m/2022.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/ARUSDT/4h/2022.parquet']`
- ATR：原口径 0.30185541（来源 box_atr_4h）；独立口径 0.36747578
- 箱体：上沿 11.327 / 下沿 9.88；宽度÷ATR = 4.794
- 窗口：16 根；最高 17.776（第 9 根 2022-11-03 03:30:00）；最低 13.769（第 1 根 2022-11-03 01:30:00）
- 计算：MFE%=27.454，MAE%=1.276；MAE(R)=原 0.590 / 独立 0.484；MFE(R)=原 12.685 / 独立 10.420
- 4h 收益：第17根收盘口径 19.990%；窗口末根口径 23.711%
- 首触：原 UNKNOWN_SAME_BAR；独立 UNKNOWN_SAME_BAR
- 分类：原 CHOPPY_OR_OTHER；独立 CHOPPY_OR_OTHER
- 结果：净盈亏 -9.85 USDT，离场 intrabar_stop @ 2022-11-08 19:30:00；全周期 MFE 0.927R / MAE 1.050R

### C1 盈利-最大净利 · INJUSDT

- 事件ID：`INJUSDT_1697606100000`
- 入场：2023-10-18 05:15:00 UTC，价 7.935
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/INJUSDT/15m/2023.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/INJUSDT/4h/2023.parquet']`
- ATR：原口径 0.74982234（来源 initial_risk_r(回退)）；独立口径 0.14864204
- 箱体：上沿 7.935 / 下沿 7.1851777；宽度÷ATR = 1.000
- 窗口：16 根；最高 7.987（第 1 根 2023-10-18 05:30:00）；最低 7.848（第 7 根 2023-10-18 07:00:00）
- 计算：MFE%=0.655，MAE%=1.096；MAE(R)=原 0.116 / 独立 0.585；MFE(R)=原 0.069 / 独立 0.350
- 4h 收益：第17根收盘口径 -0.416%；窗口末根口径 -0.353%
- 首触：原 NEITHER_REACHED_0.3ATR；独立 FIRST_FAVORABLE
- 分类：原 SUSTAINED_TREND；独立 EARLY_WEAKNESS
- 结果：净盈亏 +71.34 USDT，离场 max_holding_expired @ 2023-10-28 05:15:00；全周期 MFE 7.911R / MAE 0.124R

### C2 盈利-标签与结果背离 · SOONUSDT

- 事件ID：`SOONUSDT_1753948800000`
- 入场：2025-07-31 08:15:00 UTC，价 0.1557
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/SOONUSDT/15m/2025.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/SOONUSDT/4h/2025.parquet']`
- ATR：原口径 0.0038551831（来源 box_atr_4h）；独立口径 0.0041431615
- 箱体：上沿 0.15375 / 下沿 0.1369；宽度÷ATR = 4.371
- 窗口：16 根；最高 0.15557（第 0 根 2025-07-31 08:15:00）；最低 0.14884（第 1 根 2025-07-31 08:30:00）
- 计算：MFE%=-0.083，MAE%=4.406；MAE(R)=原 1.779 / 独立 1.656；MFE(R)=原 -0.034 / 独立 -0.031
- 4h 收益：第17根收盘口径 -3.121%；窗口末根口径 -3.096%
- 首触：原 FIRST_ADVERSE；独立 FIRST_ADVERSE
- 分类：原 EARLY_WEAKNESS；独立 EARLY_WEAKNESS
- 结果：净盈亏 +188.26 USDT，离场 max_holding_expired @ 2025-08-10 08:15:00；全周期 MFE 25.825R / MAE 0.914R

### D1 缺失/边界 · METISUSDT

- 事件ID：`METISUSDT_1791748800000`
- 入场：2026-10-11 20:15:00 UTC，价 3.826
- 原始 15m：`['数据/市场数据/parquet/candles/binance/um/METISUSDT/15m/2026.parquet']`；4h：`['数据/市场数据/parquet/candles/binance/um/METISUSDT/4h/2026.parquet']`
- ATR：原口径 0.10271542（来源 box_atr_4h）；独立口径 0.1280782
- 箱体：上沿 3.79 / 下沿 3.108；宽度÷ATR = 6.640
- 窗口：16 根；最高 3.861（第 1 根 2026-10-11 20:30:00）；最低 3.723（第 1 根 2026-10-11 20:30:00）
- 计算：MFE%=0.915，MAE%=2.692；MAE(R)=原 1.003 / 独立 0.804；MFE(R)=原 0.341 / 独立 0.273
- 4h 收益：第17根收盘口径 -2.117%；窗口末根口径 -1.124%
- 首触：原 FIRST_ADVERSE；独立 FIRST_ADVERSE
- 分类：原 EARLY_WEAKNESS；独立 EARLY_WEAKNESS
- 结果：净盈亏 +1.43 USDT，离场 force_close_eod @ 2026-10-17 23:45:00；全周期 MFE 0.721R / MAE 0.684R

