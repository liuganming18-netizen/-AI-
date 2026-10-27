# FLOW-OI-01 字段契约规范引用与定义说明

**工单编号**：FLOW-OI-01  
**对齐标准**：Work FLOW-DATA-01 字段契约  
**数据来源**：币安官方合约市场数据（Candles 1h/15m、Metrics、OI）

---

## 一、主动成交与 CVD 字段契约

所有主动成交相关字段直接由币安 K 线原始数据计算派生，单位与命名严格遵循以下契约：

| 契约字段名 | 物理源字段 | 计算公式 / 规范定义 | 单位 / 口径 | 零分母与缺失处理 |
| :--- | :--- | :--- | :---: | :--- |
| **`quote_volume`** | `quote_volume` | 原始 K 线的总计价货币成交额 | USDT | 缺失标 `null` |
| **`taker_buy_quote_volume`**| `taker_buy_quote_volume`| 原始 K 线的主动买入成交额 | USDT | 缺失标 `null` |
| **`taker_sell_quote_volume`**| 派生 | `quote_volume - taker_buy_quote_volume` | USDT | 缺失标 `null` |
| **`taker_buy_net_quote`** | 派生 | `2 * taker_buy_quote_volume - quote_volume` | USDT | 正=净主动买入，负=净主动卖出 |
| **`taker_buy_ratio`** | 派生 | `taker_buy_quote_volume / quote_volume` | 比率 [0, 1] | 若 `quote_volume <= 0` 标 `null`，严禁填 0 |
| **`base_cvd`** | 派生 | $\sum_{t} (2 \times \text{taker\_buy\_volume} - \text{volume})$ | 基础资产币数 | 固定窗口重置；断档标 `null`，严禁跨缺口连续累加 |
| **`quote_cvd`** | 派生 | $\sum_{t} (2 \times \text{taker\_buy\_quote\_volume} - \text{quote\_volume})$ | USDT 计价金额 | **单独命名，严禁与 base_cvd 混用** |
| **`norm_cvd_24h`** | 派生 | $\frac{\text{quote\_cvd\_24h}}{\text{total\_quote\_volume\_24h}}$ | 比率 [-1, 1] | 跨币种无量纲归一化指标 |

---

## 二、持仓量（OI）字段契约

为避免将价格上涨引起的名义金额膨胀误认为资金真实加仓，基础资产数量与名义价值严格分立命名：

| 契约字段名 | 物理源字段 | 字段语义与计算定义 | 单位 / 口径 | 异常与缺失处理 |
| :--- | :--- | :--- | :---: | :--- |
| **`oi_base`** | `sum_open_interest` | 币安合约全市场未平仓合约总张数/币数 | 基础币数量 | 真实资金头寸规模；缺失标 `null` |
| **`oi_quote`** | `sum_open_interest_value`| 币安合约全市场未平仓合约名义价值 | USDT 名义金额 | 包含价格波动乘数效应；缺失标 `null` |
| **`delta_oi_base_24h`**| 派生 | $\frac{\text{oi\_base}_{t_0} - \text{oi\_base}_{t_0 - 24h}}{\text{oi\_base}_{t_0 - 24h}}$ | 变化率（百分比）| 分母 $\le 0$ 或缺历史数据标 `null` |
| **`delta_oi_quote_24h`**| 派生 | $\frac{\text{oi\_quote}_{t_0} - \text{oi\_quote}_{t_0 - 24h}}{\text{oi\_quote}_{t_0 - 24h}}$ | 变化率（百分比）| 分母 $\le 0$ 或缺历史数据标 `null` |

---

## 三、币安大户与市场情绪指标契约

币安大户多空指标来自币安官方 Metrics 接口（5 分钟频度采集），三类多空比率具有完全不同的统计口径，各自独立命名：

| 契约字段名 | 物理源字段 | 字段语义与统计口径 | 缺失处理 |
| :--- | :--- | :--- | :--- |
| **`top_position_ls_ratio`** | `sum_toptrader_long_short_ratio` | **大户持仓量多空比**：按保证金余额排名前 20% 的大户净多头总持仓量 / 净空头总持仓量 | 缺失标 `null` |
| **`top_account_ls_ratio`** | `count_toptrader_long_short_ratio`| **大户账户数多空比**：排名前 20% 的大户中多头账户数 / 空头账户数 | 缺失标 `null` |
| **`global_account_ls_ratio`**| `count_long_short_ratio` | **全市场账户数多空比**：全市场持仓用户中净多头账户数 / 净空头账户数 | 缺失标 `null` |

---

## 四、不可用字段与来源合规边界声明

1. **币安独立“聪明钱（Smart Signal）”数据**：
   - 实测确认：币安仅在前端提供当前公开只读快照 XHR，官方历史下载源（`data.binance.vision`）与本地历史数据仓库均**不包含历史时序数据**；
   - **执行原则**：因无完整历史数据，按工单要求**单列记录说明，严禁纳入历史回测**，防止在时间戳截断上造成虚假前视偏差。
2. **多空平均持仓价**：
   - 实测确认：币安官方公开历史指标中不存在全市场或大户的多空平均持仓价字段；
   - **执行原则**：**严格保持缺失（`null`），绝对禁止使用主动成交均价或 VWAP 冒充多空持仓均价**。
3. **数据完整性保障**：
   - 严禁对零分母或缺失值填默认值 0；
   - 严禁跨断档跨缺口强行伪造连续累加。
