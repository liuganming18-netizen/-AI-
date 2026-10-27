# SIGNAL-ORDER-TRACE-04｜候选到订单、成交、复盘的完整追溯

> 生成时刻：2026-10-25T03:14Z（结论绑定此刻；`manifest.json` 自带 `generated_at_utc`）｜ 项目：`/Users/boos/Desktop/威科夫因子共振趋势`
> 本单**只做追溯与验证**，不改策略、不改冻结规则、不改引擎撮合逻辑。
> 所有数字都能回到 `out/*.json` 的原始字段（抽取脚本：`code/10_report_numbers.py` → `out/10_报告数字.json`）。

---

## 0. 一句话结论

**链路是通的，但「自然成交」依然是 0，且这是规则决定的、不是缺陷。**

- **自然运行**：生产纸面账本里 **3 笔持仓全部 `origin='manual'` + `voided=1` + `void_reason='synthetic_test_isolation'`** ⇒ **自然成交 = 0**。180 天内 65 个真实候选**全部早于 paper epoch（2026-10-14T01:00Z）**，严格按生产规则跑 ⇒ **65/65 被 `before_paper_epoch_no_backfill` 拒绝，0 成交**（`out/04_prod_epoch.json`）。
- **隔离回放**：把 epoch 下移到回放窗口起点（引擎自带的 `epoch_ms` 正式参数，不是改门槛）后，**48 笔开仓且 48 笔全部平仓**，17 笔被 `account_drawdown_locked` 拒绝（真实闸门，未绕过）。48/48 关联链 8 环全通、三方数值逐字段对得上（`out/05_核对_replay.json`）；**12 段再绕过本单产物用 sqlite 直连账本独立复算，净额恒等式 / 悬空外键 / 资金费两层全部为 0 违规**（`out/12_独立复算.json`）。
- **重复输入不重复下单**：同一 `signal_uid` 连提 3 次（含推进时钟后重提），第 2、3 次都返回 `duplicate_signal_uid`，账本计数一字不变（`out/04b_重复输入_open.json`）。
- **导出与工作台**：60 个 run 注册进既有工作台，`/trades` `/orders` `/fills` 端点 60/60 命中、行数 0 不符、账本对账 0 不符；浏览器里真点「导出」按钮拿到 3 份 CSV（2/4/4 行），页面自算指标与账本 4 项全对（`out/07_导出核验.json`、`out/08_UI导出核验.json`）。
- **交付判定**：**不能据此宣布「策略可上线」**。本单证明的是「**候选→订单→成交→复盘这条链能完整走通且可核对**」，不是「策略有效」。48 笔回放净亏 −637.90 USDT、胜率 18.75%，且样本远不足 30 笔。

---

## 1. 本单做了什么 / 没做什么

| | 内容 |
|---|---|
| **做了** | 四类身份机械取证；180 天真实候选扫描；隔离行情库自建；四类 pass（生产 epoch / 隔离回放 / 闸门关闭 / 重复输入）实跑；逐笔订单·逐次成交·交易汇总导出 + 关联链 8 环 + 三方对账 + **独立复算**；工作台注册 + K 线重开 + K 线窗口覆盖核对 + 订单定位；真实 HTTP 端点与浏览器按钮双路导出核对 |
| **没做（并说明原因）** | ① 没改策略/冻结参数/引擎撮合——工单明令「不改规则制造成交」；② 没驱动生产 8787、没写生产 runs 目录、没碰生产账本（前后 SHA 相同）；③ 没把回放成交当自然成交（`origin='replay'`、`research_period=true`、`not_natural_signal=true` 三重标记）；④ 没为「凑成交」放宽任何闸门——17 笔 `account_drawdown_locked` 原样保留 |

---

## 2. 四类身份（工单第一条）

`out/01_A_身份静态取证.json`（原文摘录 + 文件 SHA）｜`out/01_C_生产账本只读实证.json`（列名实证）

| 类别 | 字段 | 跨运行稳定 | 证据 |
|---|---|---|---|
| **运行内 ID** | `event_id`、`candidate_id`、`transition_id`、`paper_order.order_id`、`paper_fill.fill_id`、`paper_position.position_id`、`paper_realized.id` | 否（`paper_*` 自增主键**每个账本文件各自从 1 开始**） | 账本列名实测 |
| **跨运行匹配键** | `candidate_id`（本单实测可用）、`cross_run_key`（本单新建，形态语义量 SHA-256） | **是** | B1 + B3 |
| **引擎订单号** | `paper_order.order_id`（`INTEGER PRIMARY KEY AUTOINCREMENT`） | 否（账本内唯一，跨库不可比） | 同上 |
| **交易所订单号** | —— | —— | **不存在**：7 个 token（`exchange_order_id` / `exchangeOrderId` / `clientOrderId` / `client_order_id` / `newOrderResp` / `orderId` / `order_id_exchange`）在后端 **0 命中** |

### 2.1 身份生成点（确定性，无计数器、无挂钟）

```
state_machine.py:241  event_id  = f"ev_{strategy_name}_{venue}_{symbol}_{bar_4h_close_ms}"
state_machine.py:584  cand_id   = f"cand_{event.event_id}_{close_time_ms}"
unified_runtime.py:1309  signal_uid = c.candidate_id
```

⇒ `candidate_id` **只由「标的 + 箱体建档 4h bar 收盘 + 确认 15m 收盘」决定**，不含窗口起点、不含计数器、不含挂钟。

### 2.2 三个实测（`out/01_B_身份确定性验证.json`）

- **B1 同窗口两次运行**：`candidate_id` 逐位相同 = **True**，`cross_run_key` 逐位相同 = **True**（NEARUSDT，n=2）。
- **B2 窗口位移 30 天**：两者都变 —— 但这是**候选集合整体变了**（早段箱体消失），**不是同一候选换了 ID**。单看 B2 无法归因，脚本已如实标注。
- **B3 交集对照（有效对照）**：按 `available_at`（窗口无关的确认 bar 时刻）取交集，共同候选 **1 个**，其 `candidate_id` **逐位相同**。

> **修正一条此前流传的说法**：曾记录「`candidate_id` 含箱体建档时刻 ⇒ 换窗口即变」。**B3 + 构造原文证明不成立**——`bar_4h_close_ms` 是「箱体建档那根 4h bar 的收盘」，同一根 bar 在哪个窗口里被看到都是同一根。**只要预热足够，`candidate_id` 就是窗口无关的跨运行键。**
> **但样本极小（n_common=1）**：60 个标的 65 个候选、每标的平均 1.08 个，交集检验天然做不大。结论按「构造 + B1 + B3」三者合起来看，**不宣称统计意义上的强证据**。

### 2.3 能力边界（必须写清的字段缺口）

- `paper_order` **只有 `event_uid`（= candidate_id），没有 `signal_uid` 列**；`paper_fill` **有** `signal_uid` 且直接带 `order_id` / `position_id` ⇒ 三条关联都是真实外键，**不需要按顺序猜**。
- **拒单订单在账本里 `qty`/`price`/`fee` 本来就是 NULL**（`account_drawdown_locked` 等前置闸门在定量之前就拒了）⇒ 导出 CSV 里这几列为空是**如实**，不是取数失败。

---

## 3. 隔离环境（工单交付边界）

所有隔离**只设环境变量，不改被测代码**：

| 变量 | 值 |
|---|---|
| `MARKET_DB` | `out/iso_market.sqlite3`（本单自建，353.3 MB） |
| `PAPER_LEDGER_DIR` | `out/iso_ledger/<pass>/`（每个 pass 独立账本） |
| `PAPER_GATE_FILE` | `out/iso_gate.json` |
| `LIVE_DIR` | `out/iso_live` |
| `EVENT_TREND_RUNS_DIR` | `out/iso_runs`（60 个 run） |
| `EVENT_TREND_BATCH_ROOT` | `out/iso_batch_root`（**空目录 ⇒ 0 个批次**） |

**数据源等价性**（`out/02_数据源等价性.json`）：`FastMemWatcher`（本单为提速写的 bisect 实现）与原生 `_MemWatcher` 在 6 个标的的 47 个计数项上**逐位相同**（候选集 / 迁移数 / processed_4h·1h / atr_bars_seen / box_gate_bars / skipped_bars）⇒ 扫描结果不是「换个实现算出来的」。

**隔离行情库范围（不外推）**：60 个标的、15m 14,016 根/标的、1h 3,504 根、4h 875 根、真实资金费 43,599 条，窗口 `2026-06-01T02:00Z ~ 2026-10-25T02:00Z`（146 天）。`downloaded_anything=false`、`wrote_production=false`。**不把本库可用外推为全池可用。**

> **隔离踩到的坑（已修，记录在案）**：只设 `EVENT_TREND_RUNS_DIR` 时，页面「回测范围」列出的仍是**生产批次**（`/batches` 走的是另一个变量 `EVENT_TREND_BATCH_ROOT`，默认回落生产）。这是「写隔离 ≠ 零接触」的又一例。补上后 `/batches` 返回 `total=0`、`batch_root` 落在本单目录内（`out/07_导出核验.json → batches_endpoint`）。
> 另试过把本单 runs 目录软链成批次 `runs_sot04`，`/batches` 直接 500（`'runs_sot04'`）⇒ 放弃软链，改用空批次根。

---

## 4. 真实历史候选（修复后规则）

`out/02_候选扫描.json`：526 个合规标的全扫，**65 个候选 / 60 个不同标的**，149 秒，0 错误，时间跨度 `2026-06-03T14:00Z ~ 2026-10-11T20:00Z`。

用 `UR.RealSignalEngine.process_market()` 逐根 4h 推进（与生产 `bootstrap` 同一方法、同一 config），**不用 `bootstrap()`** 的原因：它把窗口写死成 `warmup_ms = (200*4+360)h`，与「从数据起点开始扫」不是一回事。

---

## 5. 四类场景实测（工单第二、三、六条）

| pass | 候选 | 开仓 | 结果 | 关键证据 |
|---|---|---|---|---|
| **`prod_epoch`**（严格生产规则，不传 `epoch_ms`） | 65 | **0** | `before_paper_epoch_no_backfill` × **65** | 65 张 rejected 订单 + 65 条 blocked + 65 条 request anchor，**0 成交** |
| **`replay`**（`epoch_ms=1780272000000` = 2026-06-01T00:00Z） | 65 | **48** | 48 开仓 / **48 全部平仓**；17 笔 `account_drawdown_locked` | `origin='replay'`；401.3 秒 |
| **`gate_closed`**（闸门关闭） | 65 | **0** | `new_entry_blocked_pending_closure_review` × **65** | 0 成交 / 0 持仓 |
| **`duplicate`**（同一 `signal_uid` 连提 3 次） | 1 | 1 | 第 1 次成交；第 2 次（同刻）与第 3 次（推进 4 小时后）都是 `duplicate_signal_uid` | **账本计数一字不变** |

**回放账本**（`out/05_核对_replay.json → ledger_counts`）：
`paper_order 113`（48 开 + 48 平 + 17 拒）｜`paper_fill 96`（48 开 + 48 平）｜`paper_position 48`｜`paper_realized 48`｜`paper_blocked 17`｜`paper_processed 48`｜`paper_stop_history 126`｜`paper_funding 239`｜`paper_request_anchor 65`。

**重复输入判定不看返回值自报**（`code/04b_duplicate.py`）：每次提交后立刻快照 8 张表的行数，`second_no_new_rows` / `third_no_new_rows` 都为 True 才算过。

**闸门关闭的重复提交额外验证**：`paper_order` 仍只有 **1** 行（`INSERT OR IGNORE` + `event_uid` UNIQUE），`paper_blocked` 仍只有 **1** 行（插入前查重）⇒ 拒单也是幂等的。

---

## 6. 逐笔订单 / 逐次成交 / 交易汇总（工单第四条）

产物：`out/05_逐笔订单_replay.csv`、`out/05_逐次成交_replay.csv`、`out/05_交易汇总_replay.csv`、`out/05_核对_replay.json`。

### 6.1 关联链 8 环 —— 48/48 全通

`candidate_id → paper_order.event_uid`（开仓单）`→ paper_fill.order_id → paper_fill.position_id → paper_position.signal_uid → paper_realized.position_id`，逐环要求**唯一命中**；断链或多义一律记 `broken`，**不按顺序补**。

`chain_ok_all = True`，`n_chain_broken = []`。

### 6.2 三方对账 —— 48/48 逐字段全对

成交 ↔ 持仓 ↔ 已实现，比 `qty` / `entry_price` / `exit_price` / `entry_fee` / `exit_fee` / `gross_pnl` / `net_pnl` / `notional` / `slippage_cost` / `Σfunding`（相对容差 1e-9）。

`value_ok_all = True`，`n_value_mismatch = []`。

### 6.3 汇总

| 项 | 值 |
|---|---|
| 已平笔数 | 48 |
| 胜 / 负 / 平 | **9 / 39 / 0**（胜率 **18.75%**） |
| `net_pnl` 合计 | **−637.9036706339** USDT |
| `gross_pnl` 合计 | −614.9958413 |
| 手续费合计 | 20.7531642433 |
| 滑点成本合计 | 27.4416353 |
| 资金费合计 | 2.1546650907 |
| 跳空穿线样本 | **0** |
| 持有小时区间 | 0.25 ~ 212.75 h |

### 6.4 出场路径（本单查出的实质问题，只记录不改）

`exit_path_histogram = {15m_check_position_exit_on_bar: 33, 4h_advance_position_trailing: 15}`

`advance_position_trailing`（`paper_engine.py`）在 `low_4h <= cur_stop` 时会**直接平仓**，并调用
`close_at_price(..., bar_open_time=ref_open_ms, bar_close_time=ref_close_ms, exit_ts=eff_ms)` —— 传进去的是**4h bar** 的开收盘。

由此产生两个必须写清的事实：

1. **成交的 `bar_open_time` 口径不一致**：15/48 笔平仓成交的 `bar_open_time` 是 **4h bar** 的起点，可能早于入场时刻（**14 笔如此**）。`exit_ts` 本身是对的（`max(decision_at, 4h_close)`，全部晚于开仓，**`n_exit_not_after_entry = 0`**），所以这不是「时间倒流」，而是**字段语义不统一**。
   - 本单 06 段最初直接用 `bar_open_time` 当 `exit_time`，结果算出**负持有期**（−2.25 h），被导出侧 `holding_hours` 空值暴露出来；改用 `paper_position.entry_ts/exit_ts` 后修正。
2. **潜在前视（需裁决，本单不改）**：4h 路径的参考 bar 在入场前就已开始（14/48），其 `low` 含**入场前**的价格事件。引擎已有守卫 `ref_close_ms <= entry_ts ⇒ 跳过`（AUTO-LOOP-01 加的），但**只挡住「整根都在入场前」**，挡不住「部分重叠」。对多头而言这会让止损更容易被触发，属于**偏保守**方向的偏差，但不是严格因果。**改即改冻结出场规则，故只记录。**

### 6.5 能力边界（不修饰）

- **有逐次成交事件**：`paper_fill` 是真实表（96 条），**不是订单级投影**；`per_order_fill_rows_histogram = {"1": 96}` —— 每张成交订单**只落 1 条**，**不支持部分成交展开**，也不能据此推断撮合深度。
- **无交易所订单号**：模拟盘不下真单，`exchange_order_id` 一律 `None` + 原因，**不用引擎自增 `paper_order.order_id` 冒充**。
- **无 `liquidity` / `mae` / `mfe`**：引擎不记录 ⇒ 导出 CSV 里这几列为空。
- **跳空按 `stop` 价成交**（`check_position_exit_on_bar` 未用更差的 `open`）⇒ 对多头偏乐观。本单跳空样本 0，暂未触发。

---

## 7. 导出入口 + 工作台（工单第五条）

### 7.1 导出入口的事实（先查清再动手）

前端「导出订单/成交/逐笔 CSV」是**浏览器内** `new Blob(...)` + `a.download`（`BacktestWorkbench.tsx:1863-1873`），**不是后端 CSV 端点**；喂给它的数据**全部**来自 `/trades` `/orders` `/fills` `/rejected`。因此「实际导出入口」= 这两条路一起验：

1. **端点级**（`code/07_export.py`）：起独立后端 `PORT=8799`（隔离 env），真 HTTP 下载 60 个 run × 6 个端点，落 `out/07_download/`。
2. **按钮级**（`code/08_ui_export.mjs`）：起第二个 vite 实例 `3001`（`API_PROXY_TARGET` 指向 8799、`VITE_CACHE_DIR` 独立），用 Playwright **真点按钮**拿 `download` 事件，落 `out/08_ui_download/`。

### 7.2 端点级结果

| 项 | 结果 |
|---|---|
| 列表端点命中 | **60 / 60**（`runs_missing_from_list = []`） |
| 列覆盖（3 类 × 60 run = 180 组） | **全列有值 154 组**、无该类行（不适用）26 组、**空列 0 组** |
| 行数校验 | `rows_expected` = trades 笔数 / fills 2×笔数 / orders 2×笔数+拒单 ⇒ **180/180 相符** |
| 与账本对账（订单/成交/逐笔/拒单 四项计数 + 净额 + 费用） | **60 个 run，0 个不符** |
| K 线重开（抽样 3 run × 2 周期） | **6/6 成功**，candles 672 / 576 / 9792，`signals` 与 `trades` 标记都非空 |
| **K 线图内是否真有这笔交易**（`code/11_chart_window_check.py`） | **C3 60/60**（数据窗口包住 run 窗口，只许更宽）｜**C4 47 ok / 0 fail / 13 不适用**（48 笔交易 entry/exit **全部**落在数据窗口内）｜**C5 all_ok=True**（6 次实拉的首末 candle 时刻**都包住**该 run 全部交易） |
| 生产侧 | `prod_runs_unchanged=True`、`prod_ledger_unchanged=True`（账本 SHA `19825112…da9479` 前后相同） |

> **不写 `chart.json`**：让 `GET /chart` 走 `_generate_fallback_chart`，按本 run 自己的 `summary.symbol` + `inputs.data_summary` 窗口从冷层 parquet **现读真实 K 线**。手写一份 chart.json 反而是「自己造图」。
> **K 线的能力边界**：请求 `?timeframe=4h` 时，回退分支实际只产出 **15m** K 线（`display_timeframe` 如实写 `15m`，没有谎称 4h）。页面那行「0 根（1h 原始）」同理 —— **如实降级，不伪造**。
> **`window_matches_run_window=False` 必须解释清楚，不能当成通过也不能当成失败**：回退分支的窗口取自 `inputs.json.data_summary.time_range_1h`（含预热），与 `run.json.window` **本来就不相等**。07 段如实记了 False；11 段进一步证明「不相等但**包住**，且 48 笔交易全在窗口内」——这才是「图对得上交易」的真正判据。**「窗口不相等」不是缺陷，「交易落在图外」才是。**

### 7.3 按钮级结果

| 项 | 结果 |
|---|---|
| 深链 `/?run=<run_id>` | 落地到「回测复盘」并打开该 run |
| 逐笔 CSV | **2 行**（NEARUSDT 2 笔），930 B |
| 订单 CSV | **4 行**（2 笔 × 开平），1306 B |
| 成交 CSV | **4 行**，886 B |
| K 线渲染 | `canvas_count = 11`，`has_kline_canvas = true` |
| 逐笔 chip | 「本 run 共 2 笔」 |
| **订单定位** | 点第一行逐笔 ⇒ 页面定位到 `trd_paper_pos_1`（`focused_trade_id_shown = true`） |
| **页面数字 vs 账本** | 净收益 +0.37% / 成交 2 笔 / 手续费 0.9523 / 资金费 0.3137 —— **4 项全对** |

> `run_id_in_page = false`：页面用「标的 NEAR/USDT + 运行时间 2026-09-07 22:44」呈现身份，**不打印 raw run_id**。所以用「数字对得上」来证明打开的就是这个 run，而不是靠字符串包含。
> 唯一 4xx/5xx：`GET /api/v1/accounts` → **500**。原因：隔离 `LIVE_DIR` 里没有账户，页面「模拟盘权益」显示 `—`。**与回测无关，是隔离副作用。**

### 7.4 工作台注册

`code/06_workbench.py` → `out/06_工作台注册.json`：**60 个 run，0 错误**（47 个有成交 + 13 个纯拒单）。纯拒单也建 run，是为了让 **17 笔真实拒单全部可查**（否则 14 笔在工作台里查不到）。

每个 run 目录含：`trades.jsonl` / `orders.jsonl` / `fills.jsonl` / `positions.jsonl` / `funding.jsonl` / `candidates.jsonl` / `equity_curve.jsonl` / `rejects.jsonl` / `summary.json` / `inputs.json` / `run.json`。

**三个刻意的取舍**（都写进产物里）：
- `trade_id = trd_paper_pos_<position_id>`：`position_id` 是账本真实主键，这里做**显式投影**并同时保留 `paper_position_id`，不冒充引擎 `trade_id`。
- **不用账户级权益曲线**：`paper_equity` 是账户级序列（96 条覆盖全部 48 笔），塞进单笔 run 会变成「一笔交易却画出全账户曲线」。改为按本 run 自身已实现净额累计推导，并在 `summary.equity_curve_source` 写明「**不是**逐 bar 权益」。
- `fills.jsonl` 必须是「事件 + payload」结构（`ledger_links.all_fills()` 只从 `payload` 取字段），平铺会被读成 `None`。

### 7.5 独立复算（`code/12_independent_recount.py` → `out/12_独立复算.json`）

**为什么单独做这一段**：05 段的对账用的是本单自己的引擎调用 + 自己写的投影，**自己验自己天然一致**。这一段故意**不读** `out/05_*.json`、**不 import** 本单任何计算函数，只用 `sqlite3` 只读直连三个隔离账本，从原始表用 SQL 重算。

| 判据 | replay | prod_epoch | gate_closed |
|---|---|---|---|
| `paper_order` / `fill` / `position` / `realized` / `blocked` | 113 / 96 / 48 / 48 / 17 | 65 / 0 / 0 / 0 / 65 | 65 / 0 / 0 / 0 / 65 |
| 订单状态分布 | `filled 96` + `rejected 17` | `rejected 65` | `rejected 65` |
| 拒单原因 | `account_drawdown_locked 17` | `before_paper_epoch_no_backfill 65` | `new_entry_blocked_pending_closure_review 65` |
| **净额恒等式** `net = gross − 费用 − 资金费` 违规数 | **0** | 0 | 0 |
| **时间因果** `exit_ts <= entry_ts` 笔数 | **0**（48 笔已平） | 0（无持仓） | 0（无持仓） |
| **成交↔订单** 价格/数量/费用 不一致 | **0**（96 张成交订单各 1 条 fill） | 不适用 | 不适用 |
| **悬空外键**（fill→position / fill→order / realized→position / filled 单无 fill / `signal_uid` 空 / `event_uid` 空） | **全 0** | 全 0 | 全 0 |
| **资金费两层** `Σ paper_funding.amount` vs `Σ paper_realized.funding_paid` | `2.1546650906699716` vs `2.1546650906699716`，**diff = 0.0** | 不适用 | 不适用 |
| **与 05 报告数字等价** | `n / net / gross / fee / funding` **五项全同**（容差 1e-9） | —— | —— |

> **口径差要说明**：SQL 重算 `Σnet = −637.903670633921`，报告写 `−637.9036706339`（差 2.1e-11，来自浮点求和顺序）。判据用 `1e-9` 容差，**不拿「打印出来的小数位数」当一致**。
>
> **这一段的能力边界**（写进产物 `capability_boundary`）：只校验账本内部一致性 + 与报告的等价性，**不重算撮合价格**（那要重放引擎，等于再实现一遍）。PASS 只说明「账本自洽」，**不构成策略可交付**。

---

## 8. 自然 vs 回放（工单第三条，分开标识）

| | 自然运行 | 隔离回放 |
|---|---|---|
| 账本 | `数据/paper/paper_3a.sqlite3`（生产） | `out/iso_ledger/replay/` |
| 成交 | **0** | 48 |
| 标记 | —— | `origin='replay'` + `run.json.research_period=true` + `not_natural_signal=true` |
| 生产账本实况 | `paper_order 13`（3 filled 全 `voided` + 10 rejected）｜`paper_fill 6`（全 `voided`）｜`paper_position 3`（全 `manual`/`voided`/`synthetic_test_isolation`）｜`paper_realized 3`（全 `voided`）｜`paper_blocked 12` | —— |

**结论：自然成交 = 0，且回放成交一律不得计入自然成交。** 工单要求「自然信号为 0 就如实报告」——这就是 0。

---

## 9. 本单查出的问题清单

| # | 问题 | 性质 | 处置 |
|---|---|---|---|
| 1 | 15/48 平仓走 4h 路径，成交的 `bar_open_time` 是 4h bar 起点，**14 笔早于入场** ⇒ 按 bar_open 算持有期会出现负值 | 引擎字段口径不一致 | **已修投影口径**（改用 `entry_ts/exit_ts`）；引擎侧**只记录不改** |
| 2 | 4h 路径的参考 bar 在入场前已开始，其 `low` 含入场前价格 ⇒ **潜在前视**（守卫只挡「整根在入场前」） | 规则/引擎语义 | **只记录，需裁决**（改即改冻结出场规则） |
| 3 | 只设 `EVENT_TREND_RUNS_DIR` 时 `/batches` 仍读生产批次 | 隔离覆盖不全 | **已补 `EVENT_TREND_BATCH_ROOT`**，并加 `batches_endpoint.isolated` 自证 |
| 4 | `_generate_fallback_chart` 只产出 15m K 线，请求 `?timeframe=4h` 拿不到 4h | 回退分支能力边界 | **如实记录**（`display_timeframe` 已诚实标 15m） |
| 5 | 拒单订单 `qty/price/fee` 为 NULL | 设计如此（前置闸门在定量前拒） | 判定逻辑已按 `status` 分流，不误报「空列」 |
| 6 | 「`candidate_id` 换窗口即变」这一旧说法 | 旧结论有误 | **已用构造原文 + B3 更正** |

**本单自己的判据缺陷（自查出来并已修）**：
- 06 段最初用成交 `bar_open_time` 当 `exit_time` ⇒ 14 笔负持有期。**根因是没做时间因果校验**，已给 05 段加硬判据 `exit_after_entry`（现在 48/48 通过，`n_exit_not_after_entry=[]`）。
- 07 段最初用 `body["candles"]` 判 K 线，实际载荷在 `body["chart"]["candles"]` ⇒ 误报 6/6 失败。
- 07 段最初把「0 行的 run」算成「空列失败」，且 `all()` 把 `None` 当 `False` ⇒ 三态判据写错。已改「通过 / 不通过 / 不适用」三态。
- **07 段记了 `window_matches_run_window=False` 却没说清它意味着什么** ⇒ 这个字段放在那里既不能算通过也不能算失败，等于**没有判据**。已补 11 段把它拆成三条可判定的判据（C3 包含关系 / C4 交易落在窗口内 / C5 实拉 candle 首末时刻包住交易）。**教训：一个只会输出 False 的检查项，不构成验收。**

---

## 10. 复现

```bash
cd evidence/SIGNAL-ORDER-TRACE-04/code
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

$PY 01_identity.py --phase static        # 身份静态取证
$PY 02_candidates.py --equivalence       # 数据源等价性
$PY 02_candidates.py                     # 180 天全池扫描（约 150 s）
$PY 01_identity.py --phase determinism   # B1/B2/B3
$PY 03_iso_market.py                     # 建隔离行情库（约 353 MB）
$PY 04_iso_exec.py --pass prod_epoch     # 严格生产规则 → 0 成交
$PY 04_iso_exec.py --pass replay         # 隔离回放（约 400 s）
$PY 04_iso_exec.py --pass gate_closed    # 闸门关闭
$PY 04b_duplicate.py --case open         # 重复输入（已成交）
$PY 04b_duplicate.py --case gate         # 重复输入（被拒）
$PY 05_verify.py --pass replay           # 逐笔/逐次/汇总 + 关联链 + 三方对账
$PY 06_workbench.py                      # 注册进工作台（60 run）
$PY 07_export.py                         # 起 8799 隔离后端，端点级导出核对
$NODE 08_ui_export.mjs                   # 起 3001 前端，浏览器点按钮导出
$PY 10_report_numbers.py                 # 抽报告数字
$PY 11_chart_window_check.py             # K 线窗口是否真包住交易
$PY 12_independent_recount.py            # 绕过本单产物，sqlite 直连账本独立复算
$PY 09_manifest.py                       # 交付清单（逐文件 SHA-256）
```

端口占用会被拒绝（`_port_free` 检查），**不复用未知进程**；两个脚本都在 `finally` 里终止自己起的子进程。

---

## 11. 交付清单

| 路径 | 内容 |
|---|---|
| `code/00_common.py` | 隔离 env、fail-closed 断言、`FastMemWatcher`、`cross_run_key` |
| `code/01_identity.py` | 四类身份静态取证 + B1/B2/B3 + 生产账本只读实证 |
| `code/02_candidates.py` | 数据源等价性 + 180 天全池候选扫描 |
| `code/03_iso_market.py` | 隔离行情库物化（60 标的，冷+热合并） |
| `code/04_iso_exec.py` | 四个 pass 的隔离执行（全部调用 `paper_engine` 既有函数，无自造撮合） |
| `code/04b_duplicate.py` | 重复输入幂等性（按账本计数判定） |
| `code/05_verify.py` | 关联链 8 环 + 三方对账 + 逐笔/逐次/汇总导出 + 能力边界 |
| `code/06_workbench.py` | 工作台 run 目录与 `run.json` 注册 |
| `code/07_export.py` | 隔离后端 + 端点级导出下载与逐列核对 |
| `code/08_ui_export.mjs` | Playwright 真点按钮导出 + K 线渲染 + 订单定位 |
| `code/09_manifest.py` / `manifest.json` | 逐文件 SHA-256 + 关键指纹 |
| `code/10_report_numbers.py` | 报告数字机械抽取 |
| `code/11_chart_window_check.py` | K 线窗口覆盖核对（数据窗口 ⊇ run 窗口、48 笔交易全在窗口内） |
| `code/12_independent_recount.py` | 绕过本单产物，sqlite 直连账本独立复算（恒等式 / 悬空外键 / 资金费两层 / 与报告等价） |
| `out/01_*` ~ `out/10_*` | 各段原始结果 JSON |
| `out/11_K线窗口覆盖.json` | K 线窗口覆盖核对（C3 / C4 / C5 三态） |
| `out/12_独立复算.json` | 三个隔离账本的独立复算 + 与 05 报告的等价性比对 |
| `out/05_*.csv` | 逐笔订单 / 逐次成交 / 交易汇总 |
| `out/07_download/` | 端点级下载的原始响应（60 run × 6 端点） |
| `out/08_ui_download/` | 浏览器实际下载的 3 份 CSV + 5 张截图 |
| `out/iso_market.sqlite3` | 隔离行情库（本单自建，353.3 MB，`downloaded_anything=false`） |
| `out/iso_ledger/{prod_epoch,replay,gate_closed,duplicate_open,duplicate_gate}/` | 5 个隔离账本（每个 pass 独立，互不串） |
| `out/iso_runs/` | 60 个隔离工作台 run 目录（**不在**生产 `回测日志/` 下） |
| `manifest.json` | 逐文件 SHA-256 + 关键指纹，**1,467 个文件 / 373.6 MB** |

> **顺序说明（避免误读）**：`manifest.json` 在本报告全部内容定稿**之后**生成，因此 README 自身在 manifest 里的 SHA 是新鲜的。但 `manifest.json` **不把自己算进 `files` 列表**（否则自己的哈希永远对不上）。**纪律：改完 README 必须重跑 `09_manifest.py`**，否则 manifest 里 README 的 SHA 当场失效。

**`manifest.json` 里的关键指纹**（结论绑定 `2026-10-25T03:07:35Z` 这一刻；再跑一次就会变，因为 `out/` 会被覆盖）：

| 对象 | SHA-256 / 值 |
|---|---|
| 隔离行情库 `out/iso_market.sqlite3` | `470bb200…ed0c081` |
| 隔离账本 `replay` | `75fafa6d…4a8502c` |
| 隔离账本 `prod_epoch` | `a720010b…146839c` |
| 隔离账本 `gate_closed` | `33a022a3…1953a6a` |
| 隔离账本 `duplicate_open` / `duplicate_gate` | `1756e3e4…7725199` / `e2e5ee7b…95ec017` |
| **生产账本**（只读） | `19825112…6da9479` —— 与 07 段前后核对值**相同** |
| 生产 runs 目录条目数（只读） | 2,291（本单 0 新增） |

> 缩写规则统一为「前 8 位 + `…` + 后 7 位」，全量值见 `manifest.json`。上表由脚本从 `manifest.json` 机械生成，非手抄。

---

## 12. 遗留 / 待裁决

1. **【需人决策】4h 路径的部分重叠 bar 是否算前视**：14/48 笔受影响。改则动冻结出场规则，须走裁决流程。
2. **【需人决策】`candidate_id` 能否正式作为跨运行键**：本单证据支持「能」（构造 + B1 + B3），但 B3 交集只有 1 个样本。若采纳，`cross_run_key` 可降为冗余校验；若不采纳，继续双键并存。
3. **【结构性问题，非缺陷】样本量**：48 笔远不足统计门槛（页面自己都提示「不足 30 笔不能当结论」）。按项目既有结论，攒可统计样本要数月到数年，`加标的`这条路已走完，只剩`加时间`。
4. **【能力缺口】`paper_order` 无 `signal_uid` 列**：订单→候选 只能走 `event_uid`。当前够用，但若将来一个候选要对应多张订单，`event_uid` 会不够。
5. **【未验证】真实挂钟自动成交**：本单全部是回放时钟（`now_ms` = 15m 收盘）。真实挂钟下的自动成交仍未被验证，`delivery_eligible` 依然为 `false`。
