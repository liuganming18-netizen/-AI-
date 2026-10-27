# BT-LEDGER-PILOT-04｜单组原始订单补证试点

- 工单：`BT-LEDGER-PILOT-04`（接续 `BT-LEDGER-FIX-03`）
- 范围：**仅 1 组（1/45）**。本单**不**扩大为 45 组重跑，**不**调参，**不**修改公共前端 / API
- 证据目录：`evidence/BT-LEDGER-PILOT-04/`（旧结果零覆盖，见 §7）
- 采集时刻：`2026-10-24T12:46:53Z → 12:49:34Z`（挂钟 **161 秒**，引擎 venv、单进程、`--cache none`）
- 状态：**单组试点通过；不可外推到 45 组；待 Codex 独立验收**

---

## 0. 判定摘要

| 工单要求 | 结论 | 硬证据 |
|---|---|---|
| ①运行前固定一组 + 旧 run_id，改动只限事件捕获/导出层 | **做到** | `out/00_engine_raw/run_meta.json` 运行前后三文件 sha256 全等 |
| ②从真实引擎对象取 ID/状态/时间/价/量/费/关联，区分三类 ID | **做到** | `out/03_引擎对象真值核查.json`，26 列全量 + 逐单 `funding_fee` |
| ③不存在的逐次成交 / 部分成交，明确能力边界，不伪造 | **做到** | `out/09_成交_规范导出.csv` 42 行全部标注 `order_level_full_fill`、`fill_id_status=nonexistent_in_backtest` |
| ④新旧逐笔比较，证明行为未变；有差异先解释 | **做到** | `out/07_对账结论.json` → `verdict: PASS`，21 笔 / 42 单 / 104 标量 |
| ⑤生成可导出订单/成交/汇总，按真实关联核对，并与 K 线、止损轨迹、切档对应 | **部分完成** | 关联核对 PASS；**切档链路本组全空，未验证**（见 §5.4） |
| 上游零污染 | **先污染后还原，已取证** | `out/00_engine_raw/12_上游追加写入_取证与还原.json` → `PASS`, `data_loss: false` |
| 报告数字可机械复算 | **做到** | `out/15_报告数字复算.json` → 112 项断言全过 |

**一句话结论**：这一组现在**能查、能导出、能对账**；补证**没有改变任何交易行为**（21/21 笔逐字段相等，期末余额差 `0.0`）。但「逐次成交（fill）」「交易所订单号」在回测引擎里**结构上不存在**，本单**没有**、也**不应**生成它们。

**本单暴露了一个必须先修的缺陷**：试点触发策略基类把审计 CSV **硬编码追加写进 `evidence/WORK-DYNAMIC-BT-01/out`**（`WyckoffLow4hGridBase.py:708-712 / 755 / 916-919`）。这**不是**本单引入的 bug，是既有代码；但它意味着**任何重跑都会刷新旧证据**。这是「是否扩大补证」的**前置条件**，见 §6。

---

## 1. 固定的那一组，以及为什么选它（工单第 1 条）

### 1.1 固定内容（运行前写入，事后不可改）

| 项 | 值 |
|---|---|
| 策略 | `Wyckoff_C1_S05_D05_Vs04_Z02` |
| 旧 run_id | `run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02` |
| 旧结果 | `evidence/WORK-DYNAMIC-BT-01/out/raw_results/backtest-result-Wyckoff_C1_S05_D05_Vs04_Z02.zip`，sha256 `407005948a0f394896a47bb569cb4266ef31ef05a34dcea63c7884b8af51327c` |
| timerange | `20211201-20241231` |
| 策略文件 sha256 | `4eb070b7892ac9008bc446b627f5b0fd5a41c141848a30a39158f37dfd9c8d2e` |
| 基类文件 sha256 | `d5467a2e413ddb29a34774fecdfcce08fc04b07d58de4504ce468e7b50b03608` |
| config sha256 | `88c233f1dfa4676cee34c1453b86bff21d1521058ccdfe4053d0874eda2bc3b3` |

运行器在 `main()` 开头就断言策略/config 哈希 == 旧 `run.json` 登记值，**不一致立即中止**。运行后再算一次，三个哈希全等（`run_meta.json → post.*_unchanged: true`）。

### 1.2 为什么选这一组（不是随便挑的）

三个条件同时满足才选：

1. **笔数适中且非平凡**：21 笔交易 / 42 单 / 12 个币。太小（1~2 笔）证不了「多笔互不串号」，太大（几百笔）人工核不动、报告写不完。
2. **两种退出路径都有**：`trailing_stop_loss` 13 笔 + `stop_loss` 8 笔。若全是同一种退出，无法证明「跟踪止损单与初始止损单在导出层没有被混成一类」。
3. **成本结构完整**：21 笔入场单 `fee_open_cost` 全有值、20 笔出场单 `funding_fee` 非零 + 1 笔为 0。这样「费用」和「资金费」两条线都能被真实数字检验，而不是全 0 通过。

**它唯一的短板，本单必须如实说**：这一组 **21/21 笔零切档**（`stage_events_in_replay = []`）。所以「与切档记录对应」这条链路**本次没有被验证**，不能算通过项（见 §5.4）。

### 1.3 补证改了什么（只有一处）

`code/01_pilot_run.py` 定义 `class PilotBacktesting(Backtesting)`，**只 override `backtest()`**：

```python
ret = super().backtest()          # 完全走引擎原路径
# 返回后、generate_backtest_stats() 之前，此刻 LocalTrade.bt_trades 仍持有全部已平仓对象
for t in LocalTrade.bt_trades: ... # 逐单 to_json(minified=False) + model_columns(26 列) + safe_props
return ret
```

- **没有**改策略文件、基类文件、config、freqtrade 包
- **没有**改任何计算路径（`backtest()` 内部一行未动）
- 捕获点选在 `backtest()` 返回后：`bt_trades` 是进程内对象，**进程退出即销毁**——这就是 BT-03 判定「旧导出缺 14 列」的根因，也是本单唯一能拿到全量字段的窗口

`argv` 与旧 45 组运行完全一致，只把 `--export-directory` 指向本单目录：

```
backtesting --config .../config.json --strategy Wyckoff_C1_S05_D05_Vs04_Z02
  --strategy-path .../user_data/strategies --export-directory <PILOT-04>/out/00_engine_raw/freqtrade_export
  --timerange 20211201-20241231 --cache none --export trades
```

### 1.4 一个耗时差异，必须交代

| 口径 | 值 | 来源 |
|---|---|---|
| 旧 `run.json` 记录的 `elapsed_sec` | **277.3 s** | BT-01 那次运行的引擎自报值 |
| 本次挂钟 | **161 s** | `run_meta.json`：`12:46:53Z → 12:49:34Z` |

**差 116 秒，但交易行为逐位相同**（21 笔 / 42 单全等、`final_balance` 差 0.0）。原因只能是运行环境差异（机器负载、页缓存、`--cache none` 下数据装载路径），**不是**逻辑差异——因为如果逻辑有差，`final_balance` 不可能差 0.0。

**本单不拿这个差异当「变快了」的成果，也不当异常**，只是如实登记：**耗时不可用作行为等价的判据**。

---

## 2. 从真实引擎对象取到的字段（工单第 2 条）

### 2.1 三类 ID，必须分开叫（不许混称）

| 类别 | 本单取值 | 含义 | 能不能当交易所 ID |
|---|---|---|---|
| **引擎内部订单 ID** | `engine_order_id`：`1..42`，无空洞、唯一，且与成交时刻**同序** | `backtesting.py` 的 `order_id_counter` 全局自增 | **不能**。它不是交易所订单号，是回测进程内的计数器 |
| **引擎内部交易 ID** | `engine_trade_id`：`1..21`，无空洞 | `LocalTrade.id`（`trade_id_counter`） | 不适用（交易是项目概念） |
| **项目派生键** | `internal_order_key = <pair>\|<fill_ts_ms>\|E/X`，如 `BCH/USDT:USDT\|1639433700000\|E` | 内容寻址，**不依赖 `trade_id`**，可跨版本对齐 | 不适用 |
| **交易所订单号** | `exchange_order_id`：**42/42 全空** | 回测没有交易所回报 | **不存在，不伪造** |
| **逐次成交 ID** | `fill_id`：**42/42 全空**，`fill_id_status = nonexistent_in_backtest` | 引擎 `Order` 只有标量 `filled/remaining`，**没有成交事件列表** | **不存在，不伪造** |

`order_id_kind` 列把这件事焊在数据里：42/42 行都是 `engine_internal_counter_string`。**任何人拿这一列当交易所单号用，都会被这一列拦住。**

### 2.2 已补齐字段清单（`out/08_订单_规范导出.csv`，42 行 × 43 列）

引擎 `Order` 模型 **26 列全量导出**（旧导出只剩 7 键，被 `to_json(minified=True)` + `BT_DATA_COLUMNS` 白名单裁了两道）：

`id`(→`engine_order_id`) · `ft_trade_id` · `ft_order_side` · `ft_pair` · `ft_is_open` · `ft_amount` · `ft_price` · `ft_cancel_reason` · `order_id` · `status` · `symbol` · `order_type` · `side` · `price` · `average` · `amount` · `filled` · `remaining` · `cost` · `stop_price` · `order_date` · `order_filled_date` · `order_update_date` · `funding_fee` · `ft_fee_base` · `ft_order_tag`

外加 17 个可追溯派生列：`leg` · `order_seq_in_trade` · `ft_is_entry` · `order_date_utc` / `order_filled_date_utc` / `order_update_date_utc` / `order_filled_timestamp` / `order_filled_utc` · `fee_amount_derived` · `fee_rate_derived` · `gross_notional_derived` · `stake_amount_filled_derived` · `internal_order_key` · `order_id_kind` · `fill_id` / `fill_id_status` / `exchange_order_id`（后三个恒空，**故意留空位以显式声明不存在**）。

### 2.3 真实源证据（原始记录，未经任何加工）

```
out/00_engine_raw/
├── orders_full.jsonl      66,305 B   42 行，每行 = 一个 Order 的 to_json(minified=False) + model_columns + safe_props
├── trades_full.jsonl      66,375 B   21 行，每行 = 一个 LocalTrade 的 to_json + model_columns + safe_props
├── run_meta.json           3,657 B   运行前后身份快照 + 捕获统计 + 引擎计数器
├── pilot_run.log         125,824 B   引擎完整 stdout（含 freqtrade 自报 stats 表）
├── freqtrade_export/                 引擎自己导出的 zip（未使用其订单明细，仅用于交叉核对）
└── strategy_audit_append/            上游副作用取证留档（见 §6）
```

### 2.4 引擎真值核查：13 组断言，全部 42/42 或 21/21

| 断言 | 结果 |
|---|---|
| `status` 全为 `closed` | 42/42 |
| `ft_is_open` 全为 `False` | 42/42 |
| `order_type` 全为 `limit` | 42/42 |
| `filled == amount` | 42/42 |
| `remaining == 0` | 42/42 |
| `order_date == order_filled_date == order_update_date` | 42/42 |
| `order_date == order_filled_timestamp` | 42/42 |
| `price == average == safe_price == ft_price` | 42/42 |
| `ft_fee_base` / `ft_cancel_reason` / `stop_price` 全为 `None` | 42/42 × 3 |
| `ft_trade_id == engine_trade_id` | **42/42** |
| 逐单 `Σfunding_fee == trade.funding_fees`（tol 1e-12） | **21/21** |

两条**必须点出来的语义陷阱**：

- **委托时间 ≠ 成交时间，回测里分不开**。`order_date == order_filled_date` 42/42 —— 因为回测在同一根 K 线、同一 `current_time` 里建单即成交。本单**不**把 `order_date` 当「委托挂单时间」用。
- **委托价 ≠ 成交价，回测里也分不开**。`price == average` 42/42 —— 限价单按 `propose_rate` 建单，成交后不改价。真实交易所会有 `average` 偏离 `price` 的情况，**回测没有**。

### 2.5 对 BT-03 一处判断的实测确认

BT-03 说「`bt_trades` 是**平仓顺序**，不是 `trade.id` 顺序」。本单实测 `bt_trades` 的 `engine_trade_id` 序列：

```
[1,2,3,4,5,6,7,8,10,9,11,13,14,12,15,16,17,18,19,20,21]
```

**非升序**（8/10、9/11/13/14/12 处乱序）。且**新 zip 的数组顺序与 `bt_trades` 顺序完全相同**（`zip order == bt_trades order: True`）。⇒ 旧导出数组 = `bt_trades` 追加序 = 平仓序，**不是** `trade.id` 序。这条现在有实测支撑，不再是推断。

---

## 3. 引擎能力边界：不存在的字段（工单第 3 条）

### 3.1 逐次成交：结构上不存在

`out/03_引擎对象真值核查.json → capability_boundary`：

```json
{
  "fill_id_like_columns": [],
  "has_fill_id": false,
  "orders_per_trade_dist": {"2": 21},
  "nr_of_successful_entries_dist": {"1": 21},
  "nr_of_successful_exits_dist": {"1": 21}
}
```

**结论**：freqtrade 回测的 `Order` 对象只有标量 `filled` / `remaining`，**没有成交事件列表、没有 `fill_id`、没有部分成交拆分**。回测不存在交易所回报，撮合是 `low <= rate <= high` 的布尔判定。

所以 `out/09_成交_规范导出.csv` 的 42 行，**每一行都不是独立成交事件**，而是**订单级成交投影**：

- `record_kind = order_level_full_fill`（42/42）
- `fill_seq_in_order = 1`（42/42，因为每单只可能有一次全量成交）
- `is_partial = False`（42/42）
- `fill_id = 空`，`fill_id_status = nonexistent_in_backtest`
- `note` 列逐行写明：`引擎 Order 只有标量 filled/remaining，无成交事件列表 ⇒ 本行是订单级成交投影，非独立成交事件`

**这是本单最容易被误解的一处**：`09` 有 42 行、字段齐全、看起来像成交明细，但它是**投影**。谁把它当逐次成交用，就错了。本单在文件内、报告内、`fill_id_status` 列内三处重复声明，就是为了防这个。

### 3.2 资金费：是真实结转，不是分摊

BT-03 当时不敢确认「逐单资金费是否存在」。本单实测**存在**：

| 腿 | `funding_fee` 状态 | 笔数 |
|---|---|---|
| 入场单 | `None` | 21 |
| 出场单 | `0.0` | 1 |
| 出场单 | 非零 | 20 |

机制：`close_bt_order()` 把持仓期间累积的 `funding_fee_running` **结转**到出场单的 `funding_fee` 上，然后清零。所以

$$\sum_{\text{逐单}} \text{funding\_fee} \;=\; \text{trade.funding\_fees}$$

实测 **21/21 相等（tol 1e-12）**。⇒ **这是引擎真实归属，不是事后分摊**，可以当原始事实写进账本。

全组资金费合计 `-1.0905316279221875` USDT。

### 3.3 出场手续费：交易层**没有**字段

| 字段 | 可用数 |
|---|---|
| `fee_open_cost` | **21/21** |
| `fee_close_cost` | **0/21**（恒 `None`） |

`fee_close_cost` 在回测路径下**不被写入**（只有 `_set_fee` 路径写，回测不走）。⇒ 出场费只能由 `order.cost − amount × price` 推导。本单用两条独立路径交叉验证入场费：

- 路线 A：`order.cost − amount×price`，最大偏差 **2.49e-14**
- 路线 B：`trade.fee_open_cost`
- 两路线一致：**21/21**

---

## 4. 新旧逐笔比较：补证有没有改变交易行为（工单第 4 条）

### 4.1 交易层：21 笔 × 7 项，全等

`out/01_新旧行为对照.csv`（21 行 × 27 列），7 个行为字段逐笔相等：

| 字段 | 相等数 |
|---|---|
| `open_rate` | 21/21 |
| `close_rate` | 21/21 |
| `amount` | 21/21 |
| `profit_abs` | 21/21 |
| `funding_fees` | 21/21 |
| `exit_reason` | 21/21 |
| `enter_tag` | 21/21 |

### 4.2 订单层：42 单 × 7 字段，全等

`out/02_订单七字段对照.csv`（42 行 × 19 列），按 `(pair, ft_is_entry, fill_ts_ms)` 关联：

| 字段 | 相等数 | 最大偏差 |
|---|---|---|
| `amount` | 42/42 | 0.0 |
| `price` | 42/42 | 0.0 |
| `cost` | 42/42 | 0.0 |
| `side` | 42/42 | — |
| 时间戳 | 42/42 | — |
| `is_entry` | 42/42 | — |
| `order_tag` | 42/42 | — |

### 4.3 统计块：104 个标量，102 全等，2 个是进程挂钟

`out/05_统计块对照.csv`（104 行）。唯二不同的两个字段：

| 字段 | old | new | 差值 |
|---|---|---|---|
| `backtest_run_start_ts` | 1792826919 | 1792846051 | 19,132 秒 |
| `backtest_run_end_ts` | 1792827144 | 1792846172 | 19,028 秒 |

这两个是**本次进程的挂钟起止时间**，与交易行为无关。其余 **102 个标量逐字段完全相等**，含 `backtest_start` / `backtest_end`（数据窗口）、`profit_total_abs`、`max_drawdown`、`sharpe`、`sortino`、`calmar`、`sqn`、`p_value` 等。

`stats_behavior_fields_unequal: []` ⇒ **行为层面零差异**。

> **这里我改过一次判定，必须记下来**：`02_compare.py` 初版给的是 `verdict: FAIL`，因为 `stats_block_all_equal = false`。定位后发现唯二差异是挂钟时间。如果当时图省事把「全等」放宽成「差不多」，就等于自己骗自己。最终处理是**显式建白名单** `RUNTIME_CLOCK = {backtest_run_start_ts, backtest_run_end_ts}` + `expected_to_differ` 列 + `behavior_equal` 列 + `explanation_for_raw_diff` 文字说明，**原始差异照样写进 CSV，不隐藏**。

### 4.4 引擎计数器：全等

`out/04_引擎计数器对照.json`：`rejected_signals` / `timedout_entry_orders` / `timedout_exit_orders` / `canceled_trade_entries` / `canceled_entry_orders` / `replaced_entry_orders` 全部 `old == new == 0`；`total_trades` 21 == 21；`final_balance` **1899.11520196 == 1899.11520196**（`abs_diff: 0.0`）。

### 4.5 总判定

```json
{"trades_matched": 21, "orders_matched": 42, "behavior_unchanged": true,
 "final_balance": {"abs_diff": 0.0}, "verdict": "PASS"}
```

---

## 5. 可导出产物与关联核对（工单第 5 条）

### 5.1 三张可直接交给下游的表

| 文件 | 形状 | 说明 |
|---|---|---|
| `out/08_订单_规范导出.csv` | 42 × 43 | 逐单，26 列引擎全量 + 17 派生/声明列 |
| `out/09_成交_规范导出.csv` | 42 × 17 | **订单级投影**，非逐次成交（每行 `record_kind` 已标） |
| `out/10_交易汇总.csv` | 21 × 33 | 逐笔交易，含 `initial_stop_loss_abs` / `stop_loss_abs` / `stoploss_last_update_timestamp` |

交易汇总关键分布：`exit_reason` = `trailing_stop_loss` 13 / `stop_loss` 8；`profit_abs` 合计 `-100.8848`；`funding_fees` 合计 `-1.0905`；12 个币；`is_open` 全 `False`。

### 5.2 关联核对（`out/11_关联核对.json`，`verdict: PASS`）

| 校验 | 结果 |
|---|---|
| 订单 ↔ 交易：每笔 2 单，且 `== nr_of_successful_entries + nr_of_successful_exits` | **21/21** |
| `Σ(逐单 funding_fee) == trade.funding_fees`（tol 1e-12） | **21/21** |
| `cost == qty × price × (1 + fee)` | 最大偏差 **0.0** |
| 盈亏重建（`profit_abs` 由开平价、数量、费用、资金费重算） | 最大偏差 **4.965e-09**（tol 1e-8），**21/21** |
| 因果性（`entry_ts <= exit_ts`，入场单早于出场单） | **21/21** |
| 订单 ID 唯一 / 无空洞 / 与成交时刻同序 | 42 唯一、`1..42` 无空洞、同序 |

### 5.3 与 K 线、止损轨迹的对应（`out/06_K线止损切档对应.csv`，21 × 22）

- `replay_found`：**21/21**（全部在 BT-01 replay 里找到对应记录）
- `entry_ts_match` / `exit_ts_match` / `entry_price_match` / `exit_price_match`：各 **21/21**
- `trade_id_match_engine`：**21/21**（replay 里的 `bt01_trade_id_in_replay` 与引擎 `engine_trade_id` 一致）
- `stop_trace_has_entry_event`：**21/21** True
- 止损轨迹：合计 **452 行**，单笔最少 2 行、最多 89 行

### 5.4 切档记录：**本组全空，未验证**

`stage_events_in_replay` 分布：`{0: 21}`。

⇒ 该组 21 笔**全部零切档**。工单要求的「与切档记录对应」这一项，**本组恰好没有可对应的对象**，所以：

> **切档链路本次未被验证，不计入通过项。**

这不是「通过了但没数据」，是「**没有样本，无法验证**」。谁要验证切档，必须另选一组有切档事件的实验。**这是本单最重要的未覆盖项**，不允许在汇总里被算成 PASS。

---

## 6. 上游副作用事故：取证、还原、以及它意味着什么

### 6.1 发生了什么

试点跑完后，我发现 `evidence/WORK-DYNAMIC-BT-01/out/` 下两个审计 CSV 的**字节数变了**：

| 文件 | 快照 | 试点后 | 增量 |
|---|---|---|---|
| `audit_confirmation_Wyckoff_C1_S05_D05_Vs04_Z02.csv` | 39,867 B | 79,542 B | +39,675 B / +173 行 |
| `audit_orders_Wyckoff_C1_S05_D05_Vs04_Z02.csv` | 7,050 B | 13,905 B | +6,855 B / +21 行 |

### 6.2 根因（既有缺陷，不是本单引入）

`WyckoffLow4hGridBase.py` 把审计输出目录**硬编码**成绝对路径，并以 `mode="a"` 追加：

- `:708-712` — `out_dir = os.path.join(BASE_DIR, "evidence/WORK-DYNAMIC-BT-01/out")`
- `:755` — `audit_orders_*.csv` 追加写
- `:916-919` — `audit_confirmation_*.csv` 追加写

**任何**跑这个基类的进程都会往那里追加，不管 `--export-directory` 指到哪。本单只是第一个撞上的。

### 6.3 处理：先证明无损，再还原

`code/04_restore_upstream.py` 做三件事：

1. **证明追加非改写**：新文件**前 `old_bytes` 字节**的 sha256 == BT-03 快照值 ⇒ 原有内容**一个字节没动**，`data_loss: false`
2. **留档**：把追加段（39,675 B / 6,855 B）另存到 `out/00_engine_raw/strategy_audit_append/`，**不丢**
3. **还原**：`truncate(old_bytes)`，复核两文件 sha256 **完全等于快照值**

```json
{"data_loss": false, "verdict": "PASS",
 "records": [
   {"file": "...audit_confirmation...", "prefix_equals_snapshot": true,
    "appended_bytes": 39675, "appended_lines": 173,
    "restored_sha256": "788355f2...eea2b", "restored_equals_snapshot": true, "status": "restored"},
   {"file": "...audit_orders...", "prefix_equals_snapshot": true,
    "appended_bytes": 6855, "appended_lines": 21,
    "restored_sha256": "6f29fabf...170d4", "restored_equals_snapshot": true, "status": "restored"}
 ]}
```

### 6.4 全树上游复核（`out/14_上游不可变快照_内容判据.json`，`verdict: PASS`）

BT-03 用的判据是「上游所有文件 mtime < 本单开工时刻」。**本单不能用这条**——我们确实动过上游。改用双判据：

| 判据 | 规则 | 结果 |
|---|---|---|
| **C1 内容零漂移** | 三棵树逐文件 sha256 == BT-03 快照 | **PASS**：543→543 / 128→128 / 405→405，**0 改值 / 0 新增 / 0 删除**（共 1,076 文件） |
| **C2 mtime 变化可归因** | 所有 mtime > 开工时刻的文件必须在事故登记表内 | **PASS**：`mtime_after = 2`，`unattributed = 0` |
| **C3 身份锁定** | strategy / base / config sha256 == 运行前值 | **PASS**：3/3 相等 |

> 这一条写在这里，是为了防止一种常见的自我欺骗：**「内容没变，所以等于没动过」**。事实是**动过**，只是还原了。mtime 变了就是变了，本单如实记录 2 个文件的 mtime 变化，而不是拿「sha256 一样」把这件事抹掉。

**这里发生过一次判据空转，必须记下来。** `05_manifest.py` 初版把「开工时刻」取成 `ME.stat().st_mtime`（本单证据目录的 mtime）。但本单会不断往这个目录里写报告、写脚本 ⇒ 目录 mtime 一直前移，最后**前移到事故之后** ⇒ `mtime_after` 恒为 0，C2 判据**看着通过，其实一个文件都没检查**。修法两条：

1. 开工时刻改读 `run_meta.json → pre.started_at`（`2026-10-24T12:46:53Z`，运行前写入，不可变）
2. 加**空转守卫** `c2_vacuous`：事故登记了 N 个文件，若这 N 个**一个都没被 mtime 抓到**，判定判据失效（`pass: false`），不是通过

修复后 `mtime_after = 2`、`incident_files_caught_by_mtime = 2`、`vacuous: false` ⇒ 判据这次真的咬到了东西。

> 这个坑的一般形态值得记住：**任何「时间窗口」类判据，如果窗口端点会随时间漂移，判据迟早会空转，而且空转时长得跟通过一模一样。** 判据本身必须带「我确实检查到了东西」的自证。

### 6.5 这意味着什么（**扩大补证的前置条件**）

只要不重定向审计目录，**每重跑一组，就会往 BT-01 旧证据里追加一次**。45 组全跑 = 45 次追加 = 旧证据被反复刷新。

⇒ **扩大补证之前，必须先做一件事：把 `WyckoffLow4hGridBase` 的审计输出目录改成可配置（env 或构造参数），并加一条「写入前校验目标目录 ∈ 本单证据目录」的断言。**

这件事**本单没做**（改了基类就破坏「零改动」前提，且基类在冻结集合内）。它属于独立工单。

---

## 7. 是否需要扩大补证：理由与最小范围（**本单不启动**）

### 7.1 本单证明了的

- 补证**方法可行**：单进程捕获 `bt_trades`，26 列全量落盘，**行为零变化**（21 笔 / 42 单 / 104 标量，余额差 0.0）
- 补证**成本可控**：单组 **161 秒**、单进程、`--cache none`、不改任何公共代码
- 补证**产物可对账**：关联 5 项全 PASS，与 K 线 21/21 对上

### 7.2 本单**没有**证明的（不许外推）

1. **45 组中其余 44 组**：本单只跑 1 组。BT-03 已用 6,790 单离线重建验证过导出器，但那是**离线重建**，不是**真跑**。
2. **切档链路**：本组零切档，无样本。
3. **有同刻并列交易（`open_ts` 相同）的组**：本组恰好无并列，`ft_trade_id == engine_trade_id` 42/42 才成立。BT-03 记录 35 run / 356 笔落在 178 组同刻并列——**那些组用内容寻址键 `internal_order_key` 才对得上，用 `trade_id` 会错**。本单**没有**验证这一点。
4. **零成交订单 / 拒单 / 部分成交**：本组 42 单全 `closed`、全 `filled == amount`，这三类**全都没出现**。BT-03 的 6,790 单里有零成交订单（被 `select_filled_or_open_orders()` 过滤），本单没碰到。
5. **资金费为 0 的整组**：本组 20/21 非零，样本够；但若某组全 0，`Σ逐单 == 交易层` 这条校验会「全 0 通过」，**区分不了「真的 0」和「字段没写」**。

### 7.3 如果要扩大，最小范围建议

**不建议 45 组全跑。** 按「每一类边界各覆盖一组」的最小集，需要 4 组：

| # | 要覆盖的边界 | 选取依据 |
|---|---|---|
| 1 | **同刻并列交易**（`open_ts` 重复） | BT-03 的 178 组同刻并列里挑一组，验证 `internal_order_key` 在 `trade_id` 失效时仍能对齐 |
| 2 | **有切档事件** | `stage_events_in_replay` 非空的组，补上本单 §5.4 的空白 |
| 3 | **含零成交订单 / 拒单** | 引擎 `rejected_signals` 或 `canceled_*` 非零的组 |
| 4 | **资金费全 0** | 验证「0 是真实 0」而不是「字段缺失」 |

**前置条件（硬性）**：先修 §6.5 的审计目录硬编码，否则每跑一组都污染 BT-01。**在修好之前，一组都不该再跑。**

**预计成本**：4 组 × 约 160 秒 ≈ 11 分钟机时。相比 45 组（约 2 小时 + 45 次污染风险），这是合理的最小集。

> **本单到此为止，不自动启动任何后续重跑。** 是否扩大、选哪几组、先修不修基类，等 Codex 验收结论 + 用户裁决。

---

## 8. 产物与复现

### 8.1 产物（30 文件，`out/13_产物清单.json` 逐文件 sha256）

```
BT-LEDGER-PILOT-04/
├── 00_报告_BT-LEDGER-PILOT-04.md        本文件
├── code/
│   ├── 01_pilot_run.py                  试点运行器（引擎 venv，唯一改动点）
│   ├── 02_compare.py                    新旧逐笔对照 → out/01..07
│   ├── 03_export.py                     规范导出 + 关联核对 → out/06,08,09,10,11
│   ├── 04_restore_upstream.py           上游副作用取证与还原 → out/00_engine_raw/12
│   ├── 05_manifest.py                   产物清单 + 上游内容判据 → out/13,14（必须最后跑）
│   └── 06_verify_report.py              报告数字机械复算 → out/15
└── out/
    ├── 00_engine_raw/                   真实源证据（jsonl / zip / log / run_meta / 追加段留档）
    ├── 01_新旧行为对照.csv              21 × 27
    ├── 02_订单七字段对照.csv            42 × 19
    ├── 03_引擎对象真值核查.json         13 组断言
    ├── 04_引擎计数器对照.json           9 个计数器
    ├── 05_统计块对照.csv                104 标量
    ├── 06_K线止损切档对应.csv           21 × 22
    ├── 07_对账结论.json                 PASS
    ├── 08_订单_规范导出.csv             42 × 43
    ├── 09_成交_规范导出.csv             42 × 17（订单级投影）
    ├── 10_交易汇总.csv                  21 × 33
    ├── 11_关联核对.json                 PASS
    ├── 13_产物清单.json
    ├── 14_上游不可变快照_内容判据.json  PASS（C1/C2/C3，C2 带空转守卫）
    └── 15_报告数字复算.json             PASS（112 项断言）
```

### 8.2 报告数字的机械复算（本单强制项）

`code/06_verify_report.py` 把本报告里引用的**每一个关键数字**回查 `out/*.json` / `out/*.csv` 原始字段，逐条断言：

```json
{"n_checks": 112, "n_pass": 112, "n_fail": 0, "verdict": "PASS"}
```

覆盖：身份 4 个 sha256、耗时 161s、21 笔/42 单/12 币、三表形状、13 组引擎真值、能力边界 6 项、资金费六分类、费用两路线、7×21 + 7×42 字段相等、104 标量 + 2 个挂钟差（19,132 / 19,028）、关联核对 12 项、切档全 0、上游事故 8 项、C1/C2/C3、1,076 文件 / 2 个 mtime / 0 未归因。

另含 4 条**文字层**断言，防止报告正文夸大：必须出现「切档链路本次未被验证」「单组通过 ≠ 45 组通过」「nonexistent_backtest」，且**不得**出现「逐次成交已补齐/已完成/齐全」这类说法。

### 8.3 复现（顺序敏感）

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
P=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

# 0) 先重定向策略审计输出目录，否则会再次刷新 BT-01 旧证据（§6）
evidence/FT-BASELINE-01/.venv/bin/python evidence/BT-LEDGER-PILOT-04/code/01_pilot_run.py
$P evidence/BT-LEDGER-PILOT-04/code/02_compare.py            # → out/01..07
$P evidence/BT-LEDGER-PILOT-04/code/03_export.py             # → out/06,08,09,10,11
$P evidence/BT-LEDGER-PILOT-04/code/04_restore_upstream.py   # → out/00_engine_raw/12
$P evidence/BT-LEDGER-PILOT-04/code/06_verify_report.py      # → out/15（112 项断言）
$P evidence/BT-LEDGER-PILOT-04/code/05_manifest.py           # → out/13,14（必须最后跑）
```

`01` 必须用**引擎 venv**、cwd = 项目根（config 里 `datadir` 是相对路径）。`05` 必须**最后跑**，否则清单里的 sha256 会因为后续产物变化而失效。

---

## 9. 最终回答

### 9.1 逐笔订单和成交明细，是否已达「可查、可导出、可对账」？

**分三档回答，不含糊：**

| 问题 | 回答 |
|---|---|
| **逐笔订单** 可查？ | **可以**。26 列引擎全量 + 17 派生列，42/42 单齐全，`out/08` 直接可读 |
| **逐笔订单** 可导出？ | **可以**。`out/08_订单_规范导出.csv`（42×43），UTF-8 CSV，字段名自带来源后缀 |
| **逐笔订单** 可对账？ | **可以**。关联 5 项 PASS、`cost` 偏差 0.0、盈亏重建 4.97e-09、与 K 线 21/21 对齐 |
| **逐次成交** 可查/可导出？ | **不存在**。回测引擎没有成交事件列表、没有 `fill_id`。`out/09` 是**订单级投影**，已在 3 处显式声明 |
| **交易所订单号** 可查？ | **不存在**。`exchange_order_id` 42/42 空，**不伪造** |

**所以准确说法是**：**订单级**已达可查、可导出、可对账；**成交级**在回测语境下**不可得**，不是「暂时缺」而是「结构上没有」。

### 9.2 哪些是真实原始记录，哪些仍缺失

**真实原始记录（引擎进程内对象直接序列化，无人工加工）**
- 42 单的 26 列全量（含 `status` / `filled` / `remaining` / `cost` / `order_date` / `funding_fee`）
- 21 笔的 `LocalTrade` 全量（含 `initial_stop_loss_abs` / `stop_loss_abs` / `stoploss_last_update_timestamp`）
- **逐单 `funding_fee`**：引擎 `close_bt_order()` 真实结转，`Σ逐单 == 交易层` 21/21
- 引擎 9 个计数器 + `final_balance`

**仍缺失（已分类，非「忘了导出」）**
- `fill_id` / 逐次成交事件 / 部分成交拆分 → `nonexistent_backtest`（引擎无此结构）
- `exchange_order_id` → `nonexistent_backtest`（回测无交易所回报）
- `fee_close_cost` → 交易层**不写该列**，只能由 `order.cost − amount×price` 推导
- 委托挂单时间（≠ 成交时间）→ 回测同一根 K 线内完成，**语义上不可分**
- 委托价（≠ 成交价）→ 同上

### 9.3 边界（不许忘）

- **单组通过 ≠ 45 组通过**。本单只跑 `Wyckoff_C1_S05_D05_Vs04_Z02` 一组，其余 44 组未真跑。
- **切档链路未验证**（本组 21/21 零切档）。
- **同刻并列 / 零成交单 / 拒单 / 部分成交 / 资金费全 0** 五类边界，本组**全未出现**，故**全未验证**。
- **上游被动过**（追加后还原）。内容零漂移已用 1,076 文件 sha256 证明，但**这不是「没动过」**。
- 本单**不**宣称「逐笔订单与成交明细已完整交付」。准确状态：**订单级完成，成交级不可得，切档未验证**。

### 9.4 状态标记（按纪律，不升格）

- 订单级逐笔补证（单组）：**通过**
- 成交级逐次明细：**不可得（引擎能力边界）**
- 切档对应：**未验证**
- 45 组覆盖：**未验证**
- 上游零污染：**污染后已还原，内容零漂移**
- 整体：**待 Codex 独立验收**

---

*本单结束后**未**自动启动其余组重跑。扩大补证需先修 `WyckoffLow4hGridBase` 审计目录硬编码（§6.5），再按 §7.3 的 4 组最小集执行，且需用户明确授权。*
