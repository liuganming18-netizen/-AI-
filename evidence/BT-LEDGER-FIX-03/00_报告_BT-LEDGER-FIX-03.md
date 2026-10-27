# BT-LEDGER-FIX-03｜逐笔订单与成交来源补齐

> 项目根：`/Users/boos/Desktop/威科夫因子共振趋势`
> 上游（**只读，本单 0 个文件被改**）：`evidence/WORK-DYNAMIC-BT-01/`（543 文件）、
> `evidence/WORK-DYNAMIC-BT-02/`（128 文件）、`回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/`（405 文件）
> 本单产物：`evidence/BT-LEDGER-FIX-03/`（23 个文件，逐个 SHA-256 见 `out/14_产物清单.json`）
> **未重跑任何实验、未调参、未改公共图表/API、未覆盖任何旧证据。**
> 上游不可变证明：三棵树逐文件 `(路径,字节,mtime,sha256)` 摘要 + 「无文件 mtime 晚于本单开工时刻」
> ⇒ `out/15_上游不可变快照.json` = **PASS**。

---

## 0. 判定摘要

| 判定项 | 结果 |
|---|---|
| 逐字段来源是否查清 | ✅ **已查清**（49 个字段，四类分明，每个都有源码行号或实证） |
| 导出遗漏是否修复 | ✅ **我们这一层已修**（命名 + 来源标注）；引擎侧裁剪**未修**（需重跑） |
| 45 组对账 | ✅ **45/45 组、3,395 笔、6,790 单全部通过**（16 项校验，0 条异常） |
| 缺失事件补证方案 | ✅ **已给最小方案（1 组试点 + 6 条等价性判据）**，未执行 |
| 字段契约 + 逐币逐笔索引 | ✅ 可交付（`out/12_字段契约.json`、`out/11_逐币逐笔索引.csv`） |
| **`order_id`（引擎真订单号）** | 🔴 **阻塞** —— 引擎有、导出被裁、事后不可恢复，必须重跑 |
| **`fill_id` / 逐次成交明细** | ⛔ **不可补（结构性）** —— 回测环境不存在该概念 |
| **`trade_id`（引擎 `trade.id`）** | 🟡 **部分完成** —— 引擎有、导出被裁；当前值为秩派生，含一个未观测前提 |
| **整体** | **部分完成** —— 可对账的已全部对账通过；不可恢复的如实标阻塞，未宣称补齐 |

**一句话**：6,790 条订单**是真的订单对象**（7 个原始字段逐字来自引擎导出，且有 6 项恒等式同时成立 ——
其中 5 项在 6,790/6,790 行、盈亏项在 3,395/3,395 笔），
但「引擎订单号 / 订单状态原始值 / 逐单资金费 / 成交 ID」这四类**在现有产物里不存在**，
其中真正需要为它重跑的只有两项：`order_id` 与逐单 `funding_fee`（后者导出只剩交易层总额）。

---

## 1. 逐字段来源（工单第 1 条）

### 1.1 四类分法（全单统一口径，禁止混用）

| 类 | 含义 | 本单字段数 |
|---|---|---|
| `raw_export` | 引擎原始导出 JSON 里**逐字存在** | 11 |
| `derived_from_construction` | 引擎内存里有、导出被裁，但**由源码构造规则唯一确定** | 19 |
| `derived_from_raw` | 由已导出原始字段**算得**（公式可复算） | 5 |
| `unrecoverable_rerun` | 引擎有、导出被裁、**事后拿不到**，只能重跑 | 4 |
| `nonexistent_backtest` | 回测环境**根本没有**这个概念 | 3 |
| `internal_key` | 本项目自建（**不是交易所 ID**） | 3 |
| `derived_unverified` | 派生值，与引擎真值的一致性依赖**未观测前提** | 2 |
| `raw_position` / `engine_computed` | 原始位置 / 引擎导出时即时算 | 2 |

明细：`out/01_字段来源清单.csv`（49 行，每行带 `engine_ref` 行号、`remediation`、`note`）。

### 1.2 为什么"引擎有、导出没有" —— 两道裁剪层，都能指到行号

```
引擎内存 Order 对象（26 列，trade_model.py:85-118）
        │
        ├─ 第一道：Trade.to_json(minified=True)          ← Order 只剩 7 个键
        │   trade_model.py:259-311；minified 分支只出
        │   amount / safe_price / ft_order_side / order_filled_timestamp /
        │   ft_is_entry / ft_order_tag / cost
        │   调用点：optimize/backtesting.py:1810  results = trade_list_to_dataframe(bt_trades)
        │           （minified 默认 True）
        │
        ├─ 第二道：columns=BT_DATA_COLUMNS（28 列白名单）
        │   data/btanalysis/bt_fileutils.py:26-55 / 535
        │   → 任何不在白名单的列被整列丢弃（trade_id、fee_open_cost、exit_order_status… 都在这死掉）
        │
        └─ 落盘：optimize/optimize_reports/bt_storage.py:83-89
```

**实证**（不是推断）：45 个 zip 里 `trades[*]` 的键集合**只有 1 种**（29 个键），
`trades[*].orders[*]` 的键集合**只有 1 种**（7 个键）—— 见 `out/02_字段来源结论.json`。

### 1.3 订单层：7 个原始 + 14 个被裁

| 字段 | 来源类 | 现在能不能用 | 备注 |
|---|---|---|---|
| `amount` `safe_price` `ft_order_side` `order_filled_timestamp` `ft_order_tag` `cost` | `raw_export` | ✅ 可用 | 逐字来自引擎 |
| `ft_is_entry` | `engine_computed` | ✅ 可用 | 引擎导出时按 `side==entry_side` 算 |
| `status` | `derived_from_construction` | ✅ 可用（**但非原始**） | `close_bt_order` 恒置 `'closed'`（trade_model.py:309） |
| `filled` / `remaining` | 同上 | ✅ | `filled=amount`、`remaining=0`（:305-306） |
| `is_open` | 同上 | ✅ | 恒 `False`（:308） |
| `order_type` | 同上 | ✅ | 策略未覆盖 `order_types`（基类 1,194 行里 0 处）⇒ freqtrade 默认 `limit`（interface.py:95-101） |
| `price` / `average` | 同上 | ✅ | 回测建单即置 `propose_rate`，成交后不改 ⇒ 都等于 `safe_price` |
| `order_date` / `order_timestamp` / `order_filled_date` | 同上 | ✅ | 建单与成交在同一根 K 线、同一 `current_time` ⇒ 等于成交时刻 |
| `ft_fee_base` | 同上 | ✅（恒 null） | U 本位合约手续费以计价币计 |
| `ft_cancel_reason` / `stop_price` | 同上 | ✅（恒 null） | 本批无撤单；建单未传 stop_price |
| **`order_id`（引擎真订单号）** | `unrecoverable_rerun` | 🔴 **不可用** | 回测里是全局自增计数器字符串（backtesting.py:149/940/1175），导出被裁 |
| **`funding_fee`（逐单归属）** | `unrecoverable_rerun` | 🔴 只有总额 | `close_bt_order` 把 `funding_fee_running` **整体结转并清零**，导出只剩 `trade.funding_fees` |
| **`fill_id`** | `nonexistent_backtest` | ⛔ 永不存在 | 回测无交易所回报 |
| **一单多次成交明细** | `nonexistent_backtest` | ⛔ 永不存在 | 回测 1 单 = 1 次全额成交 |

### 1.4 交易层：`trade_id` 的真相（对 BT-02 的一处澄清）

BT-02 写「引擎导出**没有**该字段，按 `(open_timestamp, 白名单索引)` 升序还原 1..n」——
**前半句对，后半句的前提没有被证明**。本单补充两点事实：

1. **引擎内部是有 trade 身份计数器的**：`self.trade_id_counter`（backtesting.py:149）在每次建仓前 `+= 1`（:1211），
   并作为 `LocalTrade(id=...)` 传入（:1213）。它不是"不存在"，是**被导出裁掉了**。
2. **导出数组的顺序是"平仓顺序"，不是 `trade.id` 顺序**：45/45 run 的 `trades` 数组按 `close_timestamp`
   升序排列，按 `open_timestamp` 升序的只有 **0/45** —— 因为 `add_bt_trade` 只在 `trade.is_open == False`
   时才 append 到 `bt_trades`（trade_model.py:1559-1565）。
   ⇒ 数组下标 `raw_trade_array_index` 是**可复现的稳定序号**，但它是平仓序，不是 `trade.id`。
3. **同刻并列确实存在**：35 个 run 里有 **356 笔**落在 **178 组**同一个 `open_timestamp` 上。
   这 356 笔的先后只能靠"同一根 K 线内按 pairlist 顺序建仓"这个**未观测前提**来排。
   ⇒ 当前 `trade_id` 标 `derived_unverified`，**不能宣称等于引擎 `trade.id`**。

好消息：**订单级关联不需要 `trade_id`**。`(run_id, pair, order_filled_timestamp, is_entry)` 在
6,790/6,790 条上唯一（0 冲突）⇒ 本单用它做内容寻址键 `internal_order_key`，与 `trade_id` 解耦。

---

## 2. 修复导出遗漏（工单第 2 条）

### 2.1 先说实话：**我们这一层没有丢原始字段**

逐字段比对结论：BT-01 `14_订单与逐次成交` 的 18 列里，7 个原始字段**一个不少**，
与原始 JSON 的价格/数量差 **0**，`cost` 差 ≤5.68e-14（2 ULP）。
所以"导出遗漏"发生在**引擎侧**（§1.2），不是我们少抄了列。

### 2.2 我们这一层真正的问题是**命名**，而且是会害人的那种

| 位置 | 现状 | 问题 | 本单处理 |
|---|---|---|---|
| `回测日志/.../<run>/orders.jsonl` | `order_id = "ord_<策略>_<trade>_<腿>"`，**6,790/6,790 全是这个形态** | **合成内部键冒充订单号**。生成处：`BT-01 code/05_export.py:335` | 规范导出里改名为 `internal_order_key`，并显式置 `engine_order_id_missing = null` |
| 同文件 | `created_at` 的值取自 `fill_ts_ms`（`05_export.py:339`） | **字段名错**：它是成交时刻，不是委托创建时刻 | 改名为 `fill_utc` / `fill_ts_ms_raw` |
| `框架/backend/backtest_store.py:849` | 注释写「`orders.jsonl` 本身有真 `order_id`」 | **注释与事实不符**：那里的值是合成的 | 本单报告明确纠正；未改公共后端（不在授权范围） |
| BT-02 `10_逐笔复盘索引` | `order_id = "MISSING"` | 对的；但**同一个词**在 run 目录里却有值 ⇒ 下游按字段名对齐必然踩坑 | 本单统一为 `null` + `engine_order_id_missing` |

### 2.3 规范导出：用**字段名后缀**把来源焊死在列名上

`out/03_订单规范导出.csv.gz`（6,790 行 × 40 列）：

| 后缀 | 列例 | 含义 |
|---|---|---|
| `_raw` | `qty_raw` `price_raw` `cost_raw` `fill_ts_ms_raw` `ft_order_tag_raw` | 引擎原始值，可当原始记录 |
| `_derived` | `status_derived` `filled_derived` `fee_amount_derived` `limit_price_derived` `created_ts_ms_derived` | 算出来 / 源码构造出来的，**不是原始记录** |
| `_missing` | `engine_order_id_missing` `engine_trade_id_missing` `fill_id_missing` `funding_fee_per_order_missing` | 引擎可能有但拿不到，**全列留空**，不填 0 不填哨兵 |
| `internal_` | `internal_order_key` | 本项目自建键 |

这样做的直接好处：**任何人把 `status_derived` 当原始状态用，字段名上就写着"derived"**，
不需要读文档也知道不能用。行级还带一列 `provenance_summary` 复述四段来源。

### 2.4 "不得编造 / 不得拆两行" —— 本单如何保证

- **不编造交易所 ID**：`engine_order_id_missing` 与 `fill_id_missing` 两列**恒为 null**（6,790/6,790），
  没有生成任何形如交易所订单号的字符串。旧 `ord_*` 键保留在 `internal_order_key` 并标注为内部键。
- **不把交易汇总拆成两行**：这一点有**独立的硬证据**，不是靠自觉 ——
  6,790/6,790 行上同时成立：
  - `cost = amount × safe_price × (1 + fee_side)`，**最大相对偏差 0.0**（完全相等，不是"接近"）
  - `cost` 带独立的浮点尾数（如 `198.075438225`、`194.29334812500002`），拆出来的行不会有这种尾数
  - 逐笔 `profit_abs = amount×(close−open) − 双边费 + funding_fees`，最大绝对偏差 **5.000050e-09**
    （引擎 `round(profit_abs, 8)` 的上界就是 5e-9）—— 拆出来的两行不可能**加上 funding 后**正好还原
  - `ft_order_tag` 逐条等于 `enter_tag`/`exit_reason`，两腿各自不同
- **不把订单复制成"逐次成交"**：`out/04_成交规范导出.csv.gz` 每行带
  `record_kind = "derived_1to1_from_order"`、`fill_granularity_derived = "order_level_full_fill"`、
  `is_partial_derived = False`，并在 `note` 里写明"本行不是独立成交事件，是订单级成交的 1:1 投影"。

---

## 3. 45 组全量对账（工单第 3 条）

### 3.1 覆盖范围（不是 4 组 84 笔）

| 项 | 值 |
|---|---|
| run | **45 / 45** |
| 交易 | **3,395**（= 45 组 `total_trades` 之和，逐组核对一致） |
| 订单 | **6,790**（每笔恰好 2 单，`{2: 3395}`） |
| 标的 | 51 |
| 校验项 | 16（含行级 13 项 + 组级 3 项） |
| 通过 | **45/45 组全通过，异常明细 0 条**（`out/07_对账异常明细.csv` 只有表头） |

### 3.2 逐项结果（`out/05_45组对账明细.csv` 的机械汇总）

| 校验 | 口径 | 通过/总数 | 最大偏差 | 门槛与依据 |
|---|---|---|---|---|
| C01 每笔恰好 2 单 | 交易 | 3,395/3,395 | 0 | 整数 |
| C02 一入一出且 side 自洽 | 交易 | 3,395/3,395 | 0 | 0 空头、0 未平仓 |
| C03 入场成交时刻 = `open_timestamp` | 订单 | 3,395/3,395 | 0 | 整数毫秒，严格相等 |
| C04 出场成交时刻 = `close_timestamp` | 订单 | 3,395/3,395 | 0 | 同上 |
| C05 入场价 = `open_rate` | 订单 | 3,395/3,395 | 0 | 严格相等 |
| C06 出场价 = `close_rate` | 订单 | 3,395/3,395 | 0 | 严格相等 |
| C07 两腿数量 = `trade.amount` | 订单 | 6,790/6,790 | 0 | 相对 1e-12 |
| C08 `cost = qty×price×(1+fee)` | 订单 | 6,790/6,790 | **0.0** | 相对 1e-12；同一表达式同一 float64 |
| C09 手续费 = `cost − qty×price` ≈ `gross×fee_rate` | 订单 | 6,790/6,790 | 4.96e-14 | 1e-9×gross |
| C10 逐笔盈亏重建 = `profit_abs` | 交易 | 3,395/3,395 | **5.000e-09** | 1e-8；引擎 `round(profit_abs,8)` 上界 5e-9 |
| C13 `ft_order_tag` = `enter_tag`/`exit_reason` | 订单 | 6,790/6,790 | 0 | 字符串相等 |
| C14 因果链 `open ≤ entry ≤ exit ≤ close` | 交易 | 3,395/3,395 | 0 | 整数比较 |
| C15 `internal_order_key` 唯一 | 订单 | 6,790/6,790 | 0 | 0 重复 |
| C16 零成交订单数 = 0 | run | 45/45 | 0 | 每单 `qty>0 & cost>0 & fill_ts>0` |
| C17 订单生命周期事件 = 0 | run | 45/45 | 0 | `canceled/timedout/replaced_entry_orders` 全 0 |
| 组级 Σ`profit_abs` = 引擎 `profit_total_abs` | run | 45/45 | **1.71e-13** | 相对 1e-9 |
| 组级 笔数 = `total_trades` | run | 45/45 | 0 | 整数 |

### 3.3 三类边界怎么处理（工单点名要求）

| 情形 | 本批次事实 | 处理 |
|---|---|---|
| **一对多成交**（一单多次成交） | **不存在**。6,790 单全部一次性 `close_bt_order`（`filled` 一次写成 `amount`，trade_model.py:305），没有任何逐次累加的路径 | 逐次成交表按「订单级 1:1 投影」给出并**显式标注**，不伪造 `fill_seq=2,3…` |
| **零成交订单** | **不存在**。`select_filled_or_open_orders()`（trade_model.py:1401-1414）只放行 `filled>0 且 status∈NON_OPEN` 的单 ⇒ 零成交单在导出层就被过滤；另有 C16/C17 独立佐证 | 计数 0，写明「不是没查，是被引擎过滤 + 计数器为 0 双重证明」 |
| **拒单** | 回测里拒单发生在**建单之前**，不产生 Order 对象 | 明确分开：策略侧拒绝 11,505（BT-02 `06b` 全量分布，Σ 已校验）/ 引擎 `rejected_signals` **982**（逐根计数，非逐信号）/ 确认通过但未成交 390（槽满）。**三者口径不同，不可相加、不可互校** |
| **收尾强平** | 17 笔 `force_exit`（15 个 run 有） | 与普通退出同口径对账，全部 2 单、全部通过 |

### 3.4 与旧三层交叉核对（全量，不是抽样）

| 对比对象 | 行数 | 关联 | 结果 |
|---|---|---|---|
| BT-01 `14_订单与逐次成交` | 6,790 | `(strategy, trade_id, order_seq)` 外连接 | 左独有 **0**、右独有 **0**；价格/数量差 **0**；`cost` 差 ≤5.68e-14；时刻/标签/方向不一致 **0** |
| BT-02 `02_逐笔订单核对` | 6,790 | `(run_id, trade_id, order_seq)` | 左独有 **0**、右独有 **0**；`fee` 差 ≤9.7e-17；BT-02 的 `order_id`/`status` 列确认全空 |
| 平台 `orders.jsonl`（45 个 run 目录） | 6,790 | `(trade_id, order_seq)` | 行数 45/45 一致；**逐字段比值 6,790 行 0 不一致**；合成 `order_id` **6,790/6,790（100%）** |

> 三层独立实现对同一批订单给出**逐条相同**的值 ⇒ 说明这批数据不是某一层"加工"出来的。

---

## 4. 缺失事件的补证方式（工单第 4 条）

### 4.1 缺什么、影响多少（`out/08_缺失字段与补证方案.csv`）

| 字段 | 影响组 | 影响行 | 状态 |
|---|---|---|---|
| `order_id`（引擎真订单号） | 45 | 6,790 | 🔴 阻塞（必须重跑） |
| `trade_id`（引擎 `trade.id`） | 45 | 3,395 | 🟡 部分完成（现值为秩派生） |
| `funding_fee` 逐单归属 | 45 | 6,790 | 🔴 阻塞（总额有，拆分不可证） |
| `amount_requested` | 45 | 3,395 | 🔴 阻塞（低价值） |
| `status`/`filled`/`remaining`/`order_type`/`price`/`order_date` 的**原始值** | 45 | 6,790 | 🟡 部分完成（有源码可证的 derived 值） |
| `fill_id` / 交易所订单号 / 部分成交明细 | 45 | 6,790 | ⛔ 不可补（结构性） |

### 4.2 为什么事后补不出来

`LocalTrade.bt_trades` 是**进程内 Python 对象**，回测进程退出即销毁；落盘的只有
`BT_DATA_COLUMNS` 那 28 列（且 `orders` 列里只嵌了 7 键子字典）。
所以 `order_id` 不是"没导出来，去别的文件找找"，而是**那一刻之后就不存在了**。

### 4.3 三个方案，推荐 B（`out/09_补证方案.json`）

| 方案 | 做法 | 运行身份影响 | 结论 |
|---|---|---|---|
| A | 在隔离策略副本里实现 `order_filled` 钩子落盘 | **改策略文件 ⇒ `strategy_sha256` 变 ⇒ 45 个 run 身份全变** | ❌ 与工单第 5 条「保持现有运行身份」冲突 |
| **B** | **进程内全量导出**：`Backtesting.backtest()` 返回后立即遍历 `LocalTrade.bt_trades`，逐单 `to_json(entry_side, minified=False)` | 策略/config **都不改** ⇒ 身份不变 | ✅ **推荐**（唯一既拿真 ID 又不改身份的路子） |
| C | 改 freqtrade 包内 `minified` 默认值 | 改引擎包 ⇒ 语义漂移，历史结果失去可比性 | ⛔ 禁止 |

方案 B 的风险已写进 JSON：① 需自己复现 CLI 调用顺序；② 必须同进程内导出；③ 仍是"重跑"，要用等价性判据证明计算未变。

### 4.4 最小补证（**已写好代码，但未对任何已闭合实验执行**）

**Step 1 — 单组试点**：只跑 `Wyckoff_C1_S05_D05_Vs04_Z02`（21 笔 / 42 单 / 实测 277.3 秒）。
选它的理由：笔数少、同时含 `trailing_stop_loss` 与 `stop_loss` 两种退出，能覆盖两条退出分支。

**Step 2 — 等价性判据（必须 6 条全过才允许扩到 45 组）**：

1. 笔数、每笔订单数（2）与旧结果完全一致；
2. **7 个原始字段逐条 bit 级相同**（不是"接近"，是 `==`）；
3. 逐笔 `profit_abs` 与旧结果差 ≤1e-8；
4. Σ`profit_abs` 相对差 ≤1e-9；
5. 新拿到的 `order_id` 是 1..N 连续整数串（与 `order_id_counter` 语义一致）；
6. `trade.id` 为 1..N 且**无空洞** —— 若出现空洞，说明存在被丢弃交易，
   则旧的派生 `trade_id` **必须整体重算**（这是本单最想验证的一条）。

任一条不过 ⇒ **停**，不改 45 组，把差异登记为新的阻塞项。

**Step 3 — 试点通过后**：45 组按同法重跑，登记为**新批次**（新 `batch_id`，lineage 指回
`runs_work_dynamic_bt_01`），旧批次 45 组证据原样保留。

**成本（实测，不估）**：45 组 `elapsed_sec` 求和 = **18,612.2 秒 ≈ 5.17 小时**（`out/09_补证方案.json`）；
导出开销预计 <2%。

### 4.5 导出器本身靠不靠得住 —— 已离线验证（`out/10_导出器离线验证.json`）

用**引擎自己的类**重建 6,790 个 `Order` 对象，调 `to_json(entry_side, minified=False)`：

| 项 | 结果 |
|---|---|
| 检查订单数 | **6,790** |
| 7 个原始字段 bit 级相同 | **6,790 / 6,790** |
| `minified=True` 输出键集合 == 原始 JSON 键集合 | ✅（7 个，完全一致） |
| `minified=False` 会多出 | **14 个键**：`order_id status filled remaining order_type price average is_open order_date order_filled_date order_timestamp ft_fee_base funding_fee pair` |
| 也就是重跑可新增的单元格 | 14 × 6,790 = **95,060 个** |

结论：**导出器没有问题，缺的只是"那次运行没导出"**。这条把"要不要重跑"从猜测变成了
「重跑一定能拿到 95,060 个新值，其中 1 个（`order_id`）不可替代」。

### 4.6 重跑也补不出来的

- `fill_id`、交易所订单号、交易所回报时间；
- 一单多次部分成交的逐次明细（回测 1 单 = 1 次全额成交）；
- 真实撮合语义（回测成交判定是 `low ≤ price ≤ high`，价格取当根开盘价 ⇒ 必然成交，与实盘被动成交不同）。

---

## 5. 字段契约 + 逐币逐笔索引（工单第 5 条）

| 产物 | 内容 |
|---|---|
| `字段契约_反重力.md` | 人读版：三张表 / 关联链 / 后缀语义 / 缺失清单 / **5 条接入禁令** |
| `out/12_字段契约.json` | 机器可读：每字段 `type/unit/tz/provenance/formula/why/nullable` + `forbidden` 列表 |
| `out/11_逐币逐笔索引.csv` | **3,395 行**，按 `symbol` 排序；含 `run_id/strategy/trade_id/raw_trade_array_index/` 入出两腿 `internal_order_key`、成交时刻与价格、`profit_abs`、**K 线位置**（`replay_file#L<line>` + `replay_sha256` + 三周期 bar 数 + 行情文件路径） |
| `out/13_逐币汇总.csv` | 51 个标的：笔数 / 涉及 run 数 / 首末时间 / Σ盈亏 / K 线完整性 |

**身份与 K 线关联未被改动**：`run_id`、`strategy_sha256`、`config_sha256` 全部沿用 BT-01 登记值；
K 线位置**直接复用** BT-02 索引（未重建、未重算），因此 `replay_sha256` 与 BT-02 逐字一致。

**索引键的改进**：BT-02 的 `entry_order_key = run|symbol|trade_id|seq` 依赖 `trade_id`（未验证）；
本单的 `internal_order_key = run|pair|fill_ts|E/X` **不依赖 `trade_id`**，且 6,790/6,790 唯一。
两套键同时保留（`out/11` 里给的是新键，`out/03` 可与 BT-02 的 `order_seq` 对齐），下游可平滑迁移。

---

## 6. 保留的失败与纠正记录

| 项 | 处理 |
|---|---|
| BT-02「引擎导出没有 `trade_id`」 | 前半句保留（导出确实没有）；本单补充「引擎内部有 `trade_id_counter`」，故 `trade_id` 属**可恢复未恢复**，不是"不存在" |
| BT-02 `order_id = "MISSING"` | 保留正确；本单额外指出 run 目录里同名 `order_id` 是**合成键**，两者不能混 |
| `框架/backend/backtest_store.py:849` 注释「orders.jsonl 本身有真 order_id」 | **原样保留未改**（公共后端不在本单授权内），在此登记为**注释与事实不符**，供后续修 |
| BT-01 `05_export.py:335/339` 生成 `ord_*` 与 `created_at=fill_time` | 原样保留未改；本单在规范导出层改名 |
| 所有旧证据 | 543 + 128 + 405 个文件 **0 个被修改**（`out/15_上游不可变快照.json` = PASS） |

---

## 7. 产物与复现

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势/evidence/BT-LEDGER-FIX-03
P=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
$P code/01_field_inventory.py    # → out/01,02
$P code/02_reconcile_45.py       # → out/03,04,05,06,07
$P code/03_missing_plan.py       # → out/08,09
/Users/boos/Desktop/威科夫因子共振趋势/evidence/FT-BASELINE-01/.venv/bin/python \
   code/04_exporter_dryrun.py    # → out/10（必须用引擎 venv）
$P code/05_contract_index.py     # → out/11,12,13 + 字段契约_反重力.md
$P code/06_manifest.py           # → out/14,15
```

| 文件 | 内容 |
|---|---|
| `out/01_字段来源清单.csv` | 49 字段 × 来源类 × 源码行号 × 补证路径 |
| `out/02_字段来源结论.json` | 45 个 zip 的字段并集实证 + 两道裁剪层 |
| `out/03_订单规范导出.csv.gz` | **6,790 × 40**，`_raw/_derived/_missing` 后缀 |
| `out/04_成交规范导出.csv.gz` | 6,790，显式 `derived_1to1_from_order` |
| `out/05_45组对账明细.csv` | 45 × 每组 16 项校验的通过数/总数/最大偏差 |
| `out/06_45组对账结论.json` | 汇总判定 + 交叉核对 + 不可对账清单 |
| `out/07_对账异常明细.csv` | 0 条（只有表头） |
| `out/08_缺失字段与补证方案.csv` | 12 项缺失 × 影响面 × 是否需重跑 |
| `out/09_补证方案.json` | A/B/C 对比 + 最小补证 3 步 + 实测成本 |
| `out/10_导出器离线验证.json` | 6,790 单 bit 级复现 + 14 个可新增字段 |
| `out/11_逐币逐笔索引.csv` | 3,395 × 39，含 K 线位置 |
| `out/12_字段契约.json` | 机器可读契约 |
| `out/13_逐币汇总.csv` | 51 标的 |
| `out/14_产物清单.json` / `out/15_上游不可变快照.json` | 逐个 SHA-256 / 上游不可变证明 |

---

## 8. 最终回答：用户能不能看和导出「完整逐笔订单、逐次成交」？

### 8.1 能看到的（**真实原始记录**）

| 内容 | 规模 | 性质 |
|---|---|---|
| 逐笔订单的 **7 个引擎原始字段**（方向/是否入场/标签/数量/成交价/含费成本/成交时刻） | 6,790 单 | ✅ **原始记录**，逐字来自 `backtest-result-*.json` |
| 交易汇总的**全部原始字段**（开平价、时刻、费率、盈亏、资金费、退出原因） | 3,395 笔 | ✅ 原始记录 |
| 逐笔盈亏 | 3,395 笔 | ✅ 可机械复算（最大偏差 5.0e-9，引擎舍入上界） |
| 每笔的 **4h/1h/15m 真实 K 线位置** | 3,395 笔 | ✅ 精确到 `文件#行号` + SHA-256 |
| 手续费金额 | 6,790 单 | ✅ 可由 `cost − qty×price` 复算（Σ = 650.6932 USDT） |

**导出能力**：`out/03_订单规范导出.csv.gz`、`out/04_成交规范导出.csv.gz`、
`out/11_逐币逐笔索引.csv` 三张表覆盖 45/45 组，可直接下载/接入。

### 8.2 看不到的（**仍缺失，且已分类**）

| 缺失项 | 状态 | 能不能补 |
|---|---|---|
| **引擎订单号 `order_id`** | 🔴 **阻塞** | 能，但**必须重跑**（方案 B，1 组试点 277 秒 / 45 组 5.17 小时） |
| **订单状态 / 成交量 / 剩余量 / 委托类型 / 委托价 / 委托时刻的原始值** | 🟡 **部分完成** | 已有**源码可证的 derived 值**（`status=closed`、`filled=amount`、`remaining=0`、`type=limit`、`price=safe_price`、`created=fill_ts`），但**不是原始记录**；要原始值得重跑 |
| **`trade_id`（引擎 `trade.id`）** | 🟡 **部分完成** | 现值为秩派生，178 笔同刻并列依赖未观测前提；重跑可直接取 `LocalTrade.id` |
| **逐单资金费归属** | 🔴 **阻塞** | 总额有（`trade_funding_fees_raw`），逐单拆分不可证；需重跑 |
| **成交 ID `fill_id` / 交易所订单号 / 部分成交明细** | ⛔ **不可补** | 回测环境不存在该概念，**永远补不出来**，也**不许自造** |

### 8.3 一句话结论

> **逐笔订单能看、能导、能对账 —— 但只有 7 个字段是真原始记录。**
> **"逐次成交"在回测里等于"逐笔订单"（1 单 1 次全额成交），没有更细的粒度可给；`fill_id` 不存在。**
> 缺失的 4 类已逐项定性：1 类值得重跑（`order_id`）、2 类有可证派生值但仍非原始（状态族 / `trade_id`）、
> 1 类结构性不可补（`fill_id`）。

### 8.4 状态标记（按纪律，不升格）

- 可对账部分：**通过**（45/45 组、16 项校验、0 异常）
- `order_id`、逐单资金费：**阻塞**
- 状态族、`trade_id`：**部分完成**
- `fill_id`：**不可补（结构性）**
- **整体：部分完成** —— 待 Codex 独立验收；本单**不宣称缺失已补齐**。

### 8.5 边界

- 本单**不改**策略准入结论（`WX-GATE-v2.0` 未通过）、不改候选速率、不改任何指标数值。
- 样本仍非独立、9 组参数高度重叠，**不构成样本外证据**。
- 「订单字段补齐」与「策略有效」是两件事：本单只证明前者，不证明后者。
