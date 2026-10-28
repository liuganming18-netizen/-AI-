# SIGNAL-NOW-01｜反重力：现实时间入场独立复核

- 项目：`/Users/boos/Desktop/威科夫因子共振趋势`
- 证据目录：`evidence/SIGNAL-NOW-01-ANTIGRAVITY/`（本单独占；未改 Work 代码、未改生产数据、未改统一交接）
- 复核对象：Work 单 `evidence/SIGNAL-NOW-01-WORK/` 落地后的**当前真实运行版本**
- 本单性质：**只读 + 隔离复核**。生产库一律 `mode=ro`；行情库 `mode=ro`；不调用下单接口；不注入信号
- 时间基准：2026-10-27 **18:46:02Z – 20:18:04Z**（北京时间 10-28 02:46 – 04:18）；采样段 A 19:08–19:28Z、段 B 19:28–20:18Z（跨 20:00Z 的 1h + 4h 双边界）

---

## 1. 一句话结论

**Work 的代码改动是真的、也在生产进程里真的跑起来了；但「现实时间入场」这条规则目前只能算「代码正确 + 隔离可证 + 加载已证」，不能算「生产已验证」——因为自然候选仍然是 0。**

支撑「加载已证」的不是哈希也不是 PID，而是**内容级证据**：运行中的 API 返回的 `fill_policy` 带着本次新增的两个键。

**段 B 已经跨过 20:00Z 的 1h + 4h 双边界**，两个水位在同一秒各推进一格（`watermark_1h` 18:00Z→19:00Z、`watermark_4h` 12:00Z→16:00Z），状态机新建档 21 个箱体 —— 系统确实在按 4h 推进，不是跑不动。**但自然候选仍然 0**：这一次收盘里没有一个标的跌破箱体下沿（`SWEEP = 0`），卡点在 **BOX → SWEEP**。

唯一那笔成交（`test_probe_succ`）是既有挂单在**旧公式**下入的场，按修好后的规则应当被拒。

同时，本单独立查出 **4 项 Work 没报的事实**，其中 2 项会影响「这笔成交到底算不算实时成交」的判定（见第 5 节）。另外还更正了一处**我自己差点写错的地方**：跟踪止损用的是 4h **最低价** × (1−d)，不是最高价 —— 按最高价算会得出「应该上移却没上移」的错误结论（见 3.2）。

---

## 2. 五项复核项目 × 三态完成表

判定只有三态：**完成 / 部分完成 / 未完成**（阻塞单列）。「执行方自报」不计入完成。

| # | 工单复核项 | 状态 | 独立证据 | 说明 |
|---|---|---|---|---|
| 1 | 核源代码与运行实例身份（哈希、策略/配置身份、PID 与启动时刻、加载证据、当前扫描状态） | **完成** | `out/01_身份与加载证据.json` | 内容级加载证据成立（不是只看哈希/PID） |
| 2 | 只读按 UID 穿透 289 条研究记录与 `test_probe_succ`，确认身份与 `approved=false`/`available_at=NULL` 仍受保护 | **完成** | `out/02_UID穿透.json` | 289 条逐条核对；四臂账本零污染 |
| 3 | 在独立副本复算时间边界、15 分钟执行、超时/停机不回填、有效期、`approved` 门禁、幂等、既有挂单/持仓恢复 | **完成**（复算本身）/ 部分完成（含 1 项缺陷探针） | `out/03_隔离复算.json` | 17 场景、25/26 断言；唯一失败是**故意设的缺陷探针** |
| 4 | 逐字段核查时间来源与单调关系；核查是否仅作合法执行 bar 价格、是否闭 bar 后才记录；缺证据应为 NULL/unproven | **完成** | `out/04_时间字段与单调关系.json` | 全量行（非抽样）；查出 3 条违规与 1 项 `fill_ts` 语义问题 |
| 5 | 独立观察重载后服务健康、采集器/Worker 未误重启、水位推进与自然候选漏斗；四种证据分开报告 | **完成**（观测本身）/ 自然行为仍**未验证** | `out/05_运行采样_A.json`、`out/05_运行采样_B.json`、`out/08_分段对比.json` | 段 A 不跨边界（标 `not_observed`）；段 B 跨 20:00Z 双边界，**两个水位均实测推进**；自然候选仍为 **0** |

**四种证据必须分开读**（工单明确要求，不得互相替代）：

| 证据类型 | 本单结论 | 依据 |
|---|---|---|
| ① 源码正确 | 改动 5 处均在文件里，语义与工单一致；**但发现 1 处注释与实现不符、1 处用户可见文案仍是旧公式** | `01`、`03` |
| ② 隔离行为 | 16/16 场景断言通过；`D4` 探针暴露「代码层无 `bar_close<=attempt` 校验」 | `03` |
| ③ 实际加载 | **成立**：运行中的 API 返回的 `fill_policy` 带本次新增的两键 | `01`、`05` |
| ④ 自然行为 | **未验证**：自然候选 0，无自然候选→建单→成交闭环 | `05` |

**附：工单禁止事项的自证**（改 Work 代码 / 改生产数据 / 改统一交接）见 **3.6**，6/6 通过 —— 用的是只读连接静态证明 + mtime 时间证明 + `-wal` 大小旁证，不是一句声明。

---

## 3. 逐项复核结果

### 3.1 源码与运行实例身份（复核项 1）

**源码（只读哈希，未改动）**

| 文件 | sha256 | 大小 | mtime (UTC) |
|---|---|---|---|
| `框架/backend/paper_engine.py` | `a99f8af00d4d82ca47acde9ffd494ef547395a29793ee2cd67e0f86f813a52d1` | 166,937 B | 2026-10-27T17:48:50Z |
| `框架/backend/unified_runtime.py` | `e30e213538686466fb06a16e4773a435b8bcfeb4f7c1ddfcfe301f4475995470` | 183,290 B | 2026-10-27T17:39:40Z |
| `框架/backend/live_api.py` | `585c97272b11feecc817c95676f6840b45b37a2622a3c262a8299ead03d7d07a` | 117,295 B | 2026-10-27T15:05:48Z |

与 Work 改动前备份对撞：`paper_engine.py` 备份 `d08586fff89b…`、`unified_runtime.py` 备份 `8e6306124f1b…`，**两者均已改变** ⇒ Work 的改动确实落在文件里（不是只写在报告里）。

**运行实例**

| 角色 | PID | 启动时刻 (UTC) | 命令行 |
|---|---|---|---|
| API | 43819 | 2026-10-27T18:19:01Z | `adapter.py` |
| Runtime（3a 全池） | 43827 | 2026-10-27T18:19:01Z | `unified_runtime.py --arm 3a --symbol all --interval 2.0` |
| Worker（生产） | 68775 | 2026-10-27T13:59:15Z | `workers/backtest_worker.py --db 数据/任务队列/jobs.sqlite3` |
| 采集器 | 42278 | 2026-10-23T09:00:16Z | `realtime_collector.py` |

⇒ Runtime / API 的进程启动时刻（18:19:01Z）**晚于**最晚的源码 mtime（17:48:50Z），采集器与 Worker 全程未被重启。

**内容级加载证据（本单最关键的一条）**

工单明确写了「哈希、报告自述或 PID 变化单独不算运行加载证明」。本单用的判据是：`live_api.py:802` 会把 `paper_engine.FILL_POLICY` 原样塞进 `/api/v1/paper/accounts` 的 `quality.fill_policy`。

实测（`05` 采样首末两次）：

```
fill_policy 键 = [defined_by, fill_price, future_fill, known_gap, no_fill_before,
                 on_fill_point_before_process_start, on_missed_moment,
                 ratification_status, ...]
含 on_fill_point_before_process_start = True
含 known_gap = True
no_fill_before = "max(signal.event_time, signal.available_at)；无信号时刻（手工请求）才取真实请求时刻"
```

这两个键只存在于 Work 改后的 `paper_engine.py`，且 `no_fill_before` 已是改正后的文案 ⇒ **运行中的 API 进程确实加载了新代码**。

**`.pyc` 取证（说明为什么不能拿 pyc 当证据）**

磁盘上 `paper_engine.cpython-313.pyc` 内嵌 `source_mtime=2026-10-27T13:19:51Z` / `source_size=160,216`，与现行源码（17:48:50Z / 166,937 B）**不符**。但 Runtime 与 API 的进程环境里 `PYTHONDONTWRITEBYTECODE=1`，且进程启动晚于源码改动 ⇒ 运行时按源码加载，pyc 陈旧**不影响**本次运行。本单不把 pyc 当加载证据，也不把它的陈旧当缺陷。

**水位（`live_run` 三行，只读）**

| 键 | 值 | `updated_at` |
|---|---|---|
| `paper_epoch_ms` | 1791939600000（2026-10-14T01:00:00Z） | 2026-10-23T03:42:02Z |
| `live_start_ms` | 1792748844240（2026-10-23T09:47:24Z） | 2026-10-23T09:47:24Z |
| `state_anchor_ms` | 1782542715686（2026-06-27T06:45:15Z） | 2026-10-25T06:45:20Z |

`effective_floor_ms() = max(live_start, paper_epoch) = 2026-10-23T09:47:24.240Z`。三行 `updated_at` **无一落在 10-27** ⇒ 授权判定沿用 10-23，Work 没有偷偷把生效起点改到 10-27。

**当前扫描状态**：`universe=526`、`cycle_count` 每 2 秒推进、`overall_health=healthy`、`scan_errors_total=0`、`watermark_1h=18:00Z`（`aligned_1h=True`、`lag_1h_bars=0`）、`watermark_4h=12:00Z`（`aligned_4h=True`、`lag_4h_bars=0`）。

---

### 3.2 289 条研究记录与 `test_probe_succ` 穿透（复核项 2）

**289 条研究行（逐条，不是抽样）**

| 字段 | 289 条的取值 | 结论 |
|---|---|---|
| `origin` | 289/289 = `historical_research` | 身份未变 |
| `executable_state` | 289/289 = `historical_not_executable` | 仍不可执行 |
| `payload.role` | 289/289 = `research_only` | 仍只作研究 |
| `payload.approved` | 289/289 = `False` | **仍被批准门禁挡住** |
| `available_at` | 289/289 = `NULL` | 未伪造可得时刻 |
| `payload.timestamps.available_at_status` | 289/289 = `unproven` | 未把 `confirmed_at` 当可得时刻 |
| `payload.arm` | 4a=105 / 4c=97 / 4b=87 | — |
| `payload.position_multiplier` | 1.0×200 / 0.8×89 | 原值保留 |
| `payload.identity_repair` | 289 条均在 | 身份修复留痕仍在 |
| payload 内外矛盾 | **0 条** | 列与 payload 不再自相矛盾 |

**账本污染检查（四臂，全量双向差集）**

`out/06_UID与账本影响表.json` 把 `live_signal` 的 UID 全集与四臂 6 张账本表里出现过的 UID 做了双向差集：

| 项 | 数量 |
|---|---|
| `live_signal` 总行数 | **289** |
| 其中 `origin='historical_research'` | **289** |
| 其中非研究行 | **0** |
| 3a 账本里出现过的不同 UID | 15 |
| 3b / 3c / 3d | 0 / 0 / 0 |
| **研究 UID 出现在账本里的条数** | **0** |
| 账本 UID 不在 `live_signal` 里的条数 | 15（全部是手工/测试单，本来就没有信号行） |

⇒ **289 条研究行没有产生任何持仓、挂单、订单、成交或处理记录。**

**这里有一条必须点明的硬事实**：`test_probe_succ` 在 `live_signal` 里**根本没有对应行**（`in_signal=False`），它只在 3a 账本里留下 6 行（position / pending / order / fill / processed / request_anchor 各 1）。⇒ 它连「已持久化的信号」都不是，是**直接调用开仓接口的人工探针**。

同理，生产 `live_signal` 当前**一条自然信号都没有** —— 整张表只有 289 条研究行。这既解释了「当前区 0 条」，也说明「自然候选 0」不是在漏斗某层被挡住，而是**从信号入库这一步就没有**。

**`test_probe_succ` 全链（只读溯源，未删、未强平、未改写）**

| 时刻 | 值 | 说明 |
|---|---|---|
| 事件/确认 | 2026-10-23T09:47:25.240Z | 信号自身时刻 |
| 可得时刻 | `NULL` | 无证据，未伪填 |
| 现实发现（首次请求） | 2026-10-27T15:46:47.361Z | 与事件相差 **367,162,121 ms ≈ 4.2496 天** |
| 下单登记 | 2026-10-27T15:46:47.363Z | 挂单行落库（首次请求后 2 ms） |
| 计划执行下限 | 2026-10-27T16:01:47.361Z | = 首次请求 + 15 分钟（**旧公式的痕迹**） |
| 执行 bar | 16:15:00.000Z → 16:29:59.999Z | 第一根已闭合 15m bar |
| 实际撮合 | 2026-10-27T16:30:14.728Z | bar 收盘后 **14,729 ms** |
| `entry_ts` | 2026-10-27T16:15:00.000Z | 回填为 bar 开盘，回填量 **914,728 ms** |

成交价取证：`raw_price=80429.2` **逐位等于**该 bar 的 `open=80429.20`；`fill_price=80469.5`（差 40.30 = 5 bps 滑点）；`market_source = binance.um.candles_15m.is_closed.open`。

**这笔必须分成四类看清，不能混说**：

1. **测试成交** —— 是。`signal_uid=test_probe_succ`，是人工探针，**不是策略自然产生**；且它在 `live_signal` 里**没有对应信号行**（见上）。
2. **自然候选** —— 不是。它不对应任何 `funnel` 的 CANDIDATE 计数，`live_signal` 里也没有它的行。
3. **隔离构造** —— 不是。它落在**生产**账本 `paper_3a`，`voided=0`。
4. **真实撮合** —— 是真实挂单被真实进程时钟推进成交（不是回放、不是注入）。但它的入场时刻是**旧公式算出来的**（`earliest_fill` 用了首次请求 + 15 分钟），按修好后的规则这笔应当被拒。

⇒ **不得称「自然策略成交」，也不得称「修好之后的闭环已打通」。** 它是「旧缺陷的一次真实落地」，恰好成了新代码必须拒掉的反例。

**既有 BTC 仓位是否仍开**

`position_id=4`、`symbol=BTCUSDT`、`status=open`、`exit_ts=NULL`、`qty=0.006`、`entry_price=80469.5`、`stop_price=76446.0`、`initial_stop_price=76446.0`、`planned_risk=24.141`、`voided=0`、`stop_history` 1 行（仅初始止损）、`paper_realized` 0 行。

运行管理状态：`execution_daemon.open_positions=1`、`trailing_updates_total=0`、`exits_triggered_total=0`、`last_error=null`。止损 76,446 对当前价（16:15 那根 bar 收盘 80,566.8）仍有约 5% 空间，未被触发。

**跟踪止损为什么 0 次更新 —— 段 B 跨过 4h 边界后仍是 0，已查清，不是缺陷**

`paper_engine.advance_position_trailing` 的 docstring 与实现一致写明：**跟踪候选价 = 最近一根已收盘 4h K 线的 `low` × (1 − `trail_d`)**；生效止损取 `max(初始值, 此前值, 本次候选)`，**只上移、不下移**。

用真实行情（`market_v2`，只读）复算：

| 4h bar（`open_time`） | 该 bar `low` | `low × 0.95` | 当前止损 | 结果 |
|---|---|---|---|---|
| 16:00Z（19:59:59.999Z 收盘，段 B 跨过） | 80,413.30 | **76,392.635** | 76,446.0 | 候选 **低于**当前止损 ⇒ **不上移，正确** |
| 12:00Z（15:59:59.999Z 收盘） | 79,810.10 | 75,819.595 | 76,446.0 | 同上 |

⇒ 段 B 跨过 4h 收盘后 `trailing_updates_total` 仍为 0、`stop_history_total` 仍为 1，是**规则算出来的结果**。当前有效止损仍是初始的 76,446.0（= 80,469.5 × 0.95）。

**这里有个极易读错的地方，必须点明**：如果按「最高价 × (1−d)」算，16:00Z 那根 `high=80,807.2` × 0.95 = **76,766.84 > 76,446**，就会得出「应该上移却没上移 ⇒ 跟踪坏了」的**错误结论**。规则用的是**最低价**。本单第一版正文就差点这么写，已按代码与行情复算更正。

---

### 3.3 独立副本复算（复核项 3）

**做法**：原地导入生产模块 + 全路径环境变量重定向 + **自建受控行情库**（不复制 13 GiB 生产热库）+ **每场景独立账本**；运行前后对 5 个生产库做 sha256 对撞。

**隔离代价（必须如实标注，不得当成生产行为）**

- 全部场景用 `origin="replay"`。这是唯一能让 `now_ms` / `requested_at` 生效的入口（`paper_engine.py:1092-1095` 用 `is_replay` 短路）；生产日常路径 `origin="signal"` 会**丢弃**外部时钟、改用真实挂钟，任何历史时刻场景都构造不出来。
- **代价**：`is_replay=True` 会同时**跳过** `paper_engine.py:1158` 的 `earliest_fill_before_first_request_clock` 校验 ⇒ **本脚本不能用来证明「成交不早于首次请求挂钟」**。该条由 3.4 的时间字段对照另行核查。
- 合约规格用本单自建 CSV（同表头、同 `contract_limits` 读取代码路径），不碰生产规格文件。
- 水位键按生产实测值写入**隔离库**，用来验证 `open_position` 的 epoch 默认口径。

**结果：17 场景，16/16 场景断言通过，专项断言 9/10，合计 25/26**

| 场景 | 验证内容 | 结果 |
|---|---|---|
| T1 | 正例：正常确认 ⇒ 成交，执行 bar = 确认后 15 分钟那根 | PASS |
| **T2** | **4 天前确认 + 今天首次请求 ⇒ 必须拒（不得续期）** | PASS（`missed_fill_moment_no_backfill`，`order_request` 仍取 10-23T09:47:25Z） |
| T2b | 同上但进程在 bar 收盘后才启动 ⇒ 停机判据也应拦 | PASS（`fill_point_closed_before_process_start`） |
| **T3** | **epoch 默认值口径**：10-20 落在「9 天 8 小时边界洞」内 | PASS（拒单，默认 epoch = 10-23T09:47:24.240Z，**不是** 10-14T01:00） |
| T4 | 幂等：同 uid 第二次请求 | PASS（`duplicate_signal_uid`、不刷新 `first_request`、不产生第二笔持仓） |
| D1 | 15 分钟落点 + 成交价来源 | PASS（落点 = 00:15:00；`raw_price` == 该 bar `open`；`is_closed=1`） |
| D2 | 应成交 bar 尚不存在 ⇒ 挂单，不提前成交 | PASS（`pending_future_fill`，`paper_pending.status=pending`，成交 0） |
| D3 | `available_at` 晚 10 分钟 ⇒ 落点顺延 | PASS（落点顺延到 00:30:00） |
| **D4** | **缺陷探针：`is_closed=1` 但尝试时刻早于该 bar 收盘** | **FAIL（这是预期结果）** |
| D5 | 前视探针：`bar_open` 晚于尝试时刻 | PASS（`bar_after_attempt_clock_no_lookahead`） |
| S1 | 滞后超过 `MAX_FILL_LAG_MS`(1h) ⇒ 拒 | PASS |
| S2 | 停机：应成交 bar 在进程启动前已收盘 ⇒ 拒 | PASS（本单新增判据生效） |
| S3 | 反例（防新判据过严）：进程在收盘前已运行 ⇒ 正常成交 | PASS |
| S4 | 闭 bar 后记账可分辨：`entry_ts==bar_open`、`fill_ts==尝试时刻`、`fill_ts>bar_close` | PASS |
| M0/M1/M2 | 倍数门禁：无倍数基准 / 未批准 0.5x / 已批准 0.5x | PASS（M1/M0 = 1.0；M2/M0 = 0.4999） |

**D4 探针结论（唯一 FAIL，且是故意设的）**

```
尝试 = 2026-10-26T00:20:00Z
bar  = 00:15:00 → 00:29:59.999Z（is_closed=1）
结果 = filled，fill_ts = 2026-10-26T00:20:00Z
fill_ts 早于 bar_close 599,999 ms
```

⇒ `open_position` **只信 `is_closed` 标记**，代码层没有 `bar_close <= attempt_ms` 的第二道校验。真实数据里这个前置条件不出现（见 3.4：同一 `open_time` 全池 528 个标的，`available_at < close_time` 的条数为 **0**），所以它是**潜在缺口**而不是正在发生的越界。但这正是「实际成交后延迟记账须有原始执行证据」这条交付标准要防的东西 —— 一旦采集器把 `is_closed` 提前置位，引擎会直接照用。

**生产零写入**：`live_signals` / `paper_3a` / `paper_3b` / `paper_3c` / `paper_3d` 五个库 sha256 前后**逐位未变**；行情库指向的是本单自建的临时库（`market_db_is_production=False`）。

---

### 3.4 时间字段对照与单调关系（复核项 4）

**七个时间口径，谁写的、什么意思**（完整表见 `out/04_时间字段与单调关系.json` 的 `field_semantics`）

| 字段 | 写入点 | 语义 | 缺证据时 |
|---|---|---|---|
| `event_time` | `open_position(...)` 原样透传 | 信号**事件/确认时刻**（bar 收盘那刻） | `NULL` |
| `available_at` | 同上 | 该信号**当时确实可得**的时刻 | `NULL` + `unproven` |
| `first_request_ms` | `paper_request_anchor` 的 `INSERT OR IGNORE` | **现实发现时刻** = 第一次真正请求的服务端挂钟 | 不会为空 |
| `paper_pending.created_at` | `_register_pending(... L.now_ms())` | **下单登记时刻** | 不会为空 |
| `earliest_fill_ms` | `order_request_ms + 15 分钟` | **计划执行下限**（不是成交时刻） | 不会为空 |
| `bar_open_time` / `bar_close_time` | `bar_at_or_after(earliest_fill_ms)` | **计划执行 bar**；`entry_ts` 回填为 `bar_open_time` | 不会为空 |
| `fill_ts` | `attempt_ms` | **实际撮合/处理时刻**（挂钟），审计用；≠ `entry_ts` | 不会为空 |

**全量行（4 笔持仓 / 7 条成交 / 15 条订单 / 4 条挂单 / 9 条首次请求锚点，不是抽样）**

单调关系逐条验：`event_time ≤ available_at ≤ first_request ≤ created_at ≤ earliest_fill ≤ bar_open < bar_close < fill_ts`，外加 `entry_ts==bar_open`、`fill_ts == paper_order.ts == paper_pending.resolved_at == paper_processed.ts`、`raw_price == bar.open`、`is_closed=1`、`fill_ts-bar_open ≤ 1h`。

**结果：3 条违规，全部落在 3 笔已作废的旧手工测试行上。**

| chain | 违规 | 数值 |
|---|---|---|
| `3a/pos1/sig_closeloop_btc_1792668586` | `bar_close < fill_ts` 不成立 | `bar_close=11:14:59.999Z`、`fill_ts=11:01:00Z`（**早 839,999 ms**） |
| `3a/pos2/sig_closeloop_btc_1792668605` | 同上 | `bar_close=11:29:59.999Z`、`fill_ts=11:16:00Z`（早 839,999 ms） |
| `3a/pos3/sig_ui_test_1792668684487` | 同上 | `bar_close=11:29:59.999Z`、`fill_ts=11:16:00Z`（早 839,999 ms） |

这三行都是 `voided=1 / void_reason=synthetic_test_isolation`、`origin=manual`、`model_version=paper_exec_v1_...`。用**当前**行情数据无法复原「当时拿的是哪一根 bar」，`available_at` 也在 `fill_ts` 之后约 14 分钟。⇒ **不能作为实时成交证据；做任何成绩统计必须按 `voided` 排除。** 同时它们正面印证了 D4：只要 bar 带 `is_closed=1` 出现在库里，引擎就会用它成交。

**唯一非作废成交（`test_probe_succ`）的时间链全部单调成立**，包括 `fill_ts > bar_close`（14,729 ms）。

**成交 bar 的「当时可得」证据**

| 项 | `test_probe_succ` 用的那根 bar |
|---|---|
| bar | BTCUSDT 15m `open_time=16:15:00.000Z` |
| `close_time` | 16:29:59.999Z |
| `is_closed` | 1 |
| `available_at` | 16:30:16.461Z（收盘后 **+16,462 ms**） |
| `fill_ts` | 16:30:14.728Z（收盘后 **+14,729 ms**） |
| 同一 `open_time` 全池标的数 | 528 |
| 其中 `is_closed=1` 且 `available_at < close_time` | **0** |
| 其中 `available_at` 为空 | **0** |

⇒ 「bar 未收盘就被标成已闭合」在真实数据里**不存在**（0/528），成交价确实取自已收盘 bar 的 `open`。**但 `fill_ts` 比该 bar 的 `available_at` 早 1,733 ms** —— 这条差值本单**不推断原因**，见第 5 节第 2 项。

**289 条研究行的时间字段**：`available_at` 289/289 = `NULL`；`available_at_status` 289/289 = `unproven`；`confirmed_at` 289/289 非空；`available_at < confirmed_at` 的违规 **0 条**。⇒ 缺证据就是 `NULL/unproven`，没有被 `confirmed_at` 顶替。

---

### 3.5 重载后独立运行采样与自然漏斗（复核项 5）

> 数据来自本单自己的采样脚本（`scripts/05_runtime_observe.py`），**未复用** Work 的采样文件。分两段跑：段 A 40 个样本 / 约 20 分钟（不跨 4h 边界），段 B 100 个样本 / 约 50 分钟（跨 20:00Z 的 1h + 4h 双边界）。两段合计 **140 个样本、约 69 分钟**。产物 `out/05_运行采样_A.json`、`out/05_运行采样_B.json`，两段对比由 `scripts/08_segment_compare.py` 生成 `out/08_分段对比.json`（数字不手抄）。

**段 A 实测（19:08:28Z → 19:27:59Z，40 样本 × 30 s，跨度 1,171.2 s）**

| 观测项 | 首样本 | 末样本 | 结论 |
|---|---|---|---|
| `cycle_count` | 358 | **520** | 持续推进（约 8.3 次/分钟）⇒ 运行实例真的在扫 |
| `latest_15m_close` | 18:59:59.999Z | 19:14:59.999Z | 15m 行情在前进 |
| `watermark_1h` | 18:00Z | 18:00Z | **未动，正确**：19:00 那根 1h 要到 20:00Z 才收盘 |
| `watermark_4h` | 12:00Z | 12:00Z | **未动，正确**：16:00 那根要到 19:59:59.999Z 才收盘 |
| `latest_4h_close` | 15:59:59.999Z | 15:59:59.999Z | 与 `watermark_4h` 一致，`lag_4h_bars=0`（水位没落后于库） |
| `candidates_since_start` | 0 | 0 | 自然候选 **0** |
| `state_counter` | IDLE 217 / BOX 274 / RETEST 27 / SWEEP 8 | **完全相同** | 20 分钟内状态机零迁移（状态机按 4h 推进，本就该如此） |
| `funnel_since_start` | BOX 0 / SWEEP 0 / RETEST 1 / CANDIDATE 0 | 完全相同 | — |
| `live_signal_total` | 289 | 289 | 仍只有研究行，自然信号 0 |
| `pending` / `position` / `fill_count` | filled 1 / rejected 3；closed 3 / open 1；7 | 完全相同 | 账本零变化 |
| `orders_routed_total` | 0 | 0 | 没有自然信号 ⇒ 没有路由 |
| 进程是否重启 | — | **4 个进程 lstart 前后一致** | 采集器 / Worker / Runtime / API 都没被误重启 |
| 总体健康 | — | `healthy` | — |

**段 A 读出来的三件事**

1. **服务是活的、在干活**：`cycle_count` 涨了 162，15m 行情在前进，健康 `healthy`，四个进程一个都没重启 ⇒ 「重载后服务健康」这一条**成立**。
2. **水位不动是对的，不是缺陷**：段 A 窗口（19:08–19:28）根本不跨 1h 或 4h 收盘，所以 `watermark_1h`/`watermark_4h` 不动是**预期行为**。已写进产物的 `not_observed`，不允许读成缺陷。
3. **自然候选 0 是「没信息」而不是「坏消息」**：状态机 20 分钟零迁移，`since_start` 的 `RETEST 1` 还是重载那一刻的残留。要判断策略能不能出候选，必须等 4h 收盘 ⇒ 所以才有段 B。

**一处如实登记的字段缺失**：状态文件里 `last_cycle_ms` 取出来是 `None`（`last_cycle_utc` 全程 `None`）。本单**不推断**原因，改用 `cycle_count` 作为「扫描在推进」的证据 —— 两者不冲突，只是这个字段当前不可用。

**段 A 产物版本说明**：`out/05_运行采样_A.json`（353,506 B，sha256 `c6e149d2…6fd1cb`）由脚本 **r1** 产出，**不含** `script_sha256` 与跟踪止损字段；段 B 用 **r2** 产出并补上这两项。两段不混用、不改写原件。

---

**段 B 实测（19:28:32Z → 20:18:04Z，100 样本 × 30 s，跨度 2,972.5 s，跨 20:00Z 的 1h + 4h 双边界）**

| 观测项 | 首样本 | 末样本 | 结论 |
|---|---|---|---|
| `cycle_count` | 525 | **995** | 持续推进 |
| `latest_15m_close` | 19:14:59.999Z | 20:14:59.999Z | 15m 行情在前进 |
| `watermark_1h` | 18:00Z | **19:00Z** | **推进了**（发生在 20:00:33Z 那个样本，收盘后 33 秒） |
| `watermark_4h` | 12:00Z | **16:00Z** | **推进了**（同一个样本，两个水位同时推进） |
| `latest_4h_close` | 15:59:59.999Z | **19:59:59.999Z** | 与 `watermark_4h` 一致，`lag_4h_bars=0` |
| `state_counter` | IDLE 217 / BOX 274 / RETEST 27 / SWEEP 8 | IDLE **203** / BOX **288** / RETEST 27 / SWEEP 8 | **状态机真的按 4h 推进了**：14 个标的从 IDLE 进 BOX |
| `funnel_since_start` | BOX 0 / SWEEP 0 / RETEST 1 / CANDIDATE 0 / EXPIRED 0 | BOX **21** / SWEEP **0** / RETEST 1 / CANDIDATE **0** / EXPIRED **7** | 见下方分因 |
| `candidates_since_start` | 0 | **0** | 自然候选仍 **0** |
| `live_signal_total` | 289 | 289 | 仍只有研究行 |
| `fill_count` / `position_max_id` | 7 / 4 | 7 / 4 | 没有新成交、没有新持仓 |
| `trailing_updates_total` / `stop_history_total` | 0 / 1 | 0 / 1 | 见 3.2 的复算，**是规则算出来的结果** |
| `scan_errors_total` | 0 | 0 | 扫描无报错 |
| 进程是否重启 | — | **4 个进程 lstart 前后一致** | 采集器 / Worker / Runtime / API 都没被误重启 |
| 总体健康 | — | `healthy` | — |

**段 B 读出来的三件事**

1. **水位推进这一条从「未观测」变成「已观测且成立」**：20:00:33Z 一个样本里 `watermark_1h` 与 `watermark_4h` 同时前进一格，与「1h/4h 都在 20:00Z 收盘、整点后约 1 分钟推进」的预期完全一致。⇒ 段 A 的「水位不动」确实是边界未到，不是卡住。
2. **状态机确实按 4h 在推进**：`state_counter.BOX` 274→288，`funnel.since_start.BOX` 0→21 ⇒ 这一根 4h 收盘带来 21 次箱体建档、7 次观察过期。**这不是一个「跑不动」的系统。**
3. **但自然候选仍然是 0，而且这次是有信息量的 0**：`SWEEP = 0` —— 21 次新建档里**没有一个标的跌破箱体下沿**，所以根本走不到回踩，更走不到候选。卡点在 **BOX → SWEEP**。

**这里必须把两个不同时间尺度的卡点分开，不能混说**：

| 观测尺度 | 卡在哪一层 | 证据 |
|---|---|---|
| 本单跨边界窗口（1 根 4h bar） | **BOX → SWEEP**（21 次建档，0 次跌破下沿） | 段 B 的 `funnel.since_start` |
| 48 天回放累计 | **RETEST → CANDIDATE**（1,255 次回踩，0 候选） | `funnel.history` |

两个结论**不矛盾也不互相替代**：短窗口里连跌破都没发生，长窗口里跌破了、回踩了，但没变成候选。合起来看，FT-LOW4H 这条链在两个尺度上都有卡点，只是层次不同。

**关于「跨了边界仍然 0」的纪律**：段 B 跨过 4h 边界、水位推进、状态机推进，**只说明这次观测「有机会」看到候选，不说明「策略一定能出候选」，更不说明「策略坏了」**。工单要求「自然候选为 0 就记 0」——记 0。同时**不得**拿隔离正例（T 系列、D 系列场景）来替代这一项，因为那是隔离构造，不是自然行为。

**自然扫描分因（取段 B 末样本 20:18:04Z 的运行时状态，`out/05_运行采样_B.json`）**

状态机当前分布（526 标的）：

| 状态 | 数量 | 含义 |
|---|---|---|
| `BOX` | **288** | 箱体已建档，等跌破下沿（跨边界后 +14） |
| `SWEEP` | 8 | 已跌破下沿（第一关过了） |
| `RETEST` | 27 | 回踩中（第二关过了） |
| `IDLE` | **203** | 无事件（其中 `IDLE_READY_BOX` **13** / `IDLE_WAIT_BOX` 78 / `IDLE_COOLING` 112） |

`IDLE_READY_BOX` 从段 A 时的 19 降到 13，正好对应 `BOX` 从 274 涨到 288 —— **20:00Z 那根 4h 收盘把一部分「等建档」的标的真的建成了箱体**。这是状态机在动的直接痕迹。

漏斗两套计数（**互不替代**，工单要求的「分因」就在这里）：

| 层 | 本次进程启动后（`since_start`，20:18:04Z） | 48 天预热回放（`history`，全程不变） |
|---|---|---|
| BOX | **21** | 8,170 |
| SWEEP | **0** | 2,050 |
| RETEST | 1 | 1,255 |
| **CANDIDATE** | **0** | **0** |
| `VOLUME_REJECTED` | 0 | 7 |
| `OI_UNAVAILABLE` | 0 | 51 |
| `OI_REJECTED` | 0 | 0 |
| `INVALIDATED` | 0 | 4,274 |
| `EXPIRED` | **7** | 3,496 |
| `MISSED_WITHOUT_RETEST` | 0 | 33 |

**`history` 全程一个数都没变**（段 A、段 B、跨边界前后逐位相同）⇒ 它是 48 天预热的固定累计，**不是**实时累加器。这一点很重要：拿它当「本次运行成绩」会读错。

**怎么读这张表（关键，别读错）**：

- `history` 是 48 天回放的累计，**不是**本次运行的成绩。它把卡点钉死在 **RETEST → CANDIDATE 这一层**：1,255 次回踩成功，0 次变成候选。
- `since_start` 只统计 18:19 重载之后的迁移，全部接近 0 是**正常的**（状态机按 4h 推进，重载后只走了 2–3 根 4h 时间片的零头）。
- `OI_UNAVAILABLE=51` 只说明「走到 OI 判定的那 51 次全被挡」，**不能**推出「关掉 OI 就会有候选」——另外 1,204 次在到 OI 之前就已经 `INVALIDATED`/`EXPIRED`。
- 数据坏死标的 6 个（`zero_volume_flat_run`：`1000000BOBUSDT`、`EPICUSDT`、`MITOUSDT`、`PROMPTUSDT` 等）**禁止决策**，只跳过决策、不删行、不改价。

---

### 3.6 未动生产 / 未动 Work 的自证（`out/09_未动自证.json`，6/6 通过）

工单禁止「改 Work 代码、生产数据或统一交接」。本单用**三条互相独立**的证据证明没做，不靠一句声明。

**① 静态证明：所有生产库连接都是只读**

全单 10 处 `sqlite3.connect` 调用，7 处带 `mode=ro`；另外 3 处可写连接**全部**在步骤 3 的隔离脚本里，且目标顺着变量链落在 `iso/` 下：

```
iso_root = ROOT / "iso"          ← 本单证据目录
tmp      = mkdtemp(dir=iso_root) ← 每次运行新建临时目录
market_db = tmp / "market.sqlite3"
```

三条变量链断言（`iso_root` 定义 / 临时目录位置 / `market_db` 位置）全部成立。

**② 时间证明：Work 目录与交接文件的 mtime 早于本单开始**

写文件必然刷新 mtime，所以「最大 mtime 早于本单开始」等价于「本单没写过」。

| 对象 | 本单开始时刻 | 该对象最大 mtime | 差值 |
|---|---|---|---|
| Work 证据目录（**201** 个文件） | 18:46:02Z | 18:43:49Z | 早 **2 分 13 秒** |
| `docs/用户约定/当前交接.md`（63,608 B） | 18:46:02Z | 17:33:43Z | 早 **1 小时 12 分** |

**③ 三个生产源码哈希与本单开始时逐位一致**：`paper_engine.py` `a99f8af0…`、`unified_runtime.py` `e30e2135…`、`live_api.py` `585c9727…`。

**④ 生产账本「零写入」的文件级证据（这条比哈希强得多）**

| 库 | 主库 mtime | `-wal` 大小 | 含义 |
|---|---|---|---|
| `live_signals.sqlite3` | 15:10:25Z | **0 B** | 15:10 之后没有任何提交写入，`live_signal` 停在 289 行 |
| `paper_3a.sqlite3` | **16:30:18Z** | 无 `-wal` 文件 | 16:30:18（`test_probe_succ` 成交后 3.3 秒）之后零写入 |
| `paper_3b` / `paper_3c` / `paper_3d` | 10-23T03:13:44Z | 无 | 四天没动 |

⇒ 从 16:30:18Z 到**段 B 采样结束（20:18:04Z）共 3 小时 47 分 46 秒**，生产账本一次提交都没有；同期 `cycle_count` 从 358 涨到 995（扫描一直在跑），状态机在 20:00Z 后还新建档了 21 个箱体。**这就是「运行时一直在扫、状态机在动、但不出自然信号、不下单」最直接的文件级证据。**

这条在段 A 结束、段 B 结束后各复验一次，两次结论相同（`out/09_未动自证.json` 为最后一次）。

**方法学提醒（这条本身是个坑，必须记住）**：`-wal` 非 0 时，主库哈希不变**什么都不能证明** —— 写入可能还压在 WAL 里没落主库。所以「哈希不变」只有在 `-wal` 为 0、或该库根本不是 WAL 模式时，才能读成「无写入」。本单两处判断都核过 `-wal` 大小，**没有单靠哈希下结论**。

---

## 4. 与 Work 自报的差异（工单要求逐项标出）

| 项目 | Work 自报 | 本单独立实证 | 差异性质 |
|---|---|---|---|
| 两次重载的 PID 与时刻 | 14879/14857 → 41453/41446 → 43827/43819（18:19:01Z） | 43827/43819，`psutil.create_time` = 18:19:01Z | 一致 |
| 三水位未变 | 逐位未变 | 只读 `live_run`，三行值与 `updated_at` 均未落在 10-27 | 一致 |
| 采集器/Worker 未动 | 未重启 | 采集器 42278（10-23T09:00:16Z）、Worker 68775（13:59:15Z） | 一致 |
| 隔离断言 | 18/18 | 本单自建 17 场景，16/16 + 专项 9/10 | 一致（不同断言集，不互相替代） |
| 48 天回放漏斗 | BOX 8170 / SWEEP 2050 / RETEST 1255 / CANDIDATE 0 / OI_UNAVAILABLE 51 | 从**运行时状态文件**独立读取，逐项相同 | 一致 |
| `test_probe_succ` 是「既有挂单自然成交」 | 是 | 是（真实进程时钟推进），但入场时刻由**旧公式**算出，按新规则应拒 | **需要分层**：它是真实撮合，不是自然候选 |
| `available_at` 晚于 `fill_ts` | 报为 PIT 疑点（1,733 ms） | 事实确认，但本单补上 `fill_ts` 语义解释（见 5.2） | 本单补充 |
| `fill_ts` 的语义 | 未提 | **`fill_ts` = 本轮循环起始时刻，不是撮合完成时刻** | **本单新增** |
| 挂单推进的 `is_replay` | 未提 | **挂单推进时 `is_replay` 恒为 True ⇒ 严格因果校验在该路径上从不生效** | **本单新增** |
| 3 笔作废行的 14 分钟前视 | 未提 | 存在（已作废，但证明 D4 会真实发生） | **本单新增** |
| 环境继承 | 未提 | 重载后的 Runtime/API 继承了 197 个环境变量（含代理与沙箱标记） | **本单新增** |
| 孤儿 Worker | 未提 | PID 88794，10-24T07:28:10Z 起运行，指向 `/var/folders/.../isolation_test_04_*/jobs_isolated.sqlite3` | **本单新增** |

---

## 5. 本单独立新发现（Work 未报）

### 5.1 挂单推进路径上，严格因果校验从不生效（代码事实）

```python
# paper_engine.py:1093-1095
is_replay = (origin in ("replay", "test") or pending_id is not None)
attempt_ms = int(now_ms) if (is_replay and now_ms is not None) else real_wall_ms
req_ms     = int(requested_at) if (is_replay and requested_at is not None) else attempt_ms
```

`advance_pending()` 调用 `open_position(pending_id=<id>, ...)` ⇒ `pending_id is not None` ⇒ **`is_replay=True`**，与 `origin` 无关。

后果有三条：

1. `attempt_ms` 取**调用方传入的 `T`**，不取真实挂钟；
2. `paper_engine.py:1158` 的 `if not is_replay and earliest_fill_ms < first_req: reject(...)` 在挂单推进路径上**永远不执行**；
3. 这与 `paper_engine.py:1088` 注释里写的「日常模拟下单必须以服务端接收时刻为准，外部传入的 requested_at、now_ms 不得改变成交时间下限」**直接矛盾**。

**实际危害有限**：挂单登记时 `earliest_fill ≥ first_req` 已成立，且 `bar_at_or_after(earliest_fill)` 保证 `bar_open ≥ earliest_fill`，所以该防线被跳过通常无害。但它是「文档说有一道门、代码里那道门开着」——属于**声明与实现不一致**，须由主代理裁决是补上还是改注释。

### 5.2 `fill_ts` 不是撮合完成时刻，而是循环起始时刻

代码链：`step_once()` 入口取一次 `now`（`unified_runtime.py:2831`）→ 中间跑完 526 标的扫描（`:2852`）→ 再传到 `execute_cycle(now)`（`:2893`）→ `advance_pending(T=now)`（`:2347`）→ `attempt_ms = T` → `paper_fill.fill_ts = attempt_ms`。

实测对撞：`fill_ts = 16:30:14.728Z`，而该 bar 的 `available_at = 16:30:16.461Z`、`sources.imported_at = 16:30:16.718Z` ⇒ 该行**落库比 `fill_ts` 晚约 2 秒**。同一 `open_time` 全池 528 个标的里「`is_closed=1` 且 `available_at < close_time`」= **0**，排除了「bar 未收盘就被标闭合」的解释 ⇒ 唯一自洽的解释是 **`fill_ts` 早于真正读到该 bar 的时刻**。

**强度：强推断**（代码路径逐行可查 + 时序对撞），**不是受控实验**（未做「人为拖慢单轮耗时再观测 `fill_ts`」的对照）。

影响：
- `fill_ts` 只能当「本轮开始处理」的下界，**不能**当「当时确实拿得到该 bar」的证明；
- `fill_ts - bar_close` 会**低估**真实成交延迟；
- `MAX_FILL_LAG_MS` 用同一个 `attempt_ms` ⇒ 长轮次下「滞后」会被低估；
- `bar_open > attempt_ms` 的前视判据用同一个 `attempt_ms` ⇒ 偏严不偏松。

**本单不据此宣布「成交用了未来数据」** —— bar 确实已收盘，成交价取自已收盘 bar 的 `open`。本条只否定「`fill_ts` 可作可得性证明」这一用法。

### 5.3 环境继承：重载把工具链环境带进了生产进程

原始输出见 `out/07_进程环境与网络.json`（`scripts/07_process_env_and_net.py`，用 `psutil` 取环境与连接，只读）。

| 进程 | 环境变量数 | 有代理变量 | 有 `CODEBUDDY_*` 沙箱标记 | TCP ESTABLISHED | TCP LISTEN |
|---|---|---|---|---|---|
| Runtime 43827（已重载） | **197** | 是 | 是（3 项） | **0** | 0 |
| API 43819（已重载） | **197** | 是 | 是（3 项） | **0** | 2 |
| Worker 68775（未重启） | 61 | 否 | 否 | 0 | 0 |
| 采集器 42278（未重启） | 57 | 否 | 否 | **14** | 0 |

具体继承到的：`HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:53528`（含小写同名）、`CODEBUDDY_SESSION_ID=c553f37c-8a77-456d-a4a3-5820747e3657`、`CODEBUDDY_SAFE_DELETE_SANDBOX=1`、`CODEBUDDY_TOYBOX_SANDBOX_PROFILE=…/toybox.sb`。

`CODEBUDDY_SESSION_ID` 就是**本次会话**的 ID ⇒ 重载是从这个会话的 shell 里发起的，环境是那样被带进去的。这是**重载方式**的差异，不是代码差异。

当前影响：Runtime/API 的 ESTABLISHED = **0**，代理变量此刻不产生实际影响；采集器有 14 条 ESTABLISHED 到 `198.18.0.21:443`（它在直连行情源）。

**本单不据此断言「代理配置无害」**，只说「此刻未观察到影响」。风险在于：下次重载若继承到会拦截外网的代理配置，采集/下单链路可能被静默打断。建议重载时显式清理 `HTTP(S)_PROXY` 等变量。属运维动作，本单不动。

### 5.4 孤儿 Worker

PID 88794，2026-10-24T07:28:10Z 启动，指向 `/var/folders/.../isolation_test_04_ipqsy2ry/jobs/jobs_isolated.sqlite3`（一个临时隔离库），已连续运行 3 天多。不碰生产库，但属于未回收的测试进程。**本单不杀进程**（工单禁止重启/停止服务），只登记。

---

## 6. 交付标准逐条补查

| 交付标准 | 结论 | 依据 |
|---|---|---|
| 原始确认时刻与现实发现时刻不可互相覆盖 | **成立**。`event_time=10-23T09:47:25.240Z` 与 `first_request=10-27T15:46:47.361Z` 各自独立成列，未互相覆盖；`paper_request_anchor` 只落首次请求 | `02`、`04` |
| 历史同一事件重复扫描不得续期重单 | **成立**。T4：第二次请求 `duplicate_signal_uid`、`first_request` 未被刷新、持仓数仍为 1；T2：4 天前确认按信号自身时刻算 `order_request`，滞后 224,105,000 ms ⇒ 拒单 | `03`、`04` |
| 实际成交后延迟记账须有原始执行证据 | **部分成立**。`fill_ts` / `bar_open_time` / `bar_close_time` / `raw_price` / `market_source` 都在账本里可逐笔复算；**但 `fill_ts` 是循环起始时刻（5.2）**，`entry_ts` 回填量 914,728 ms 必须与 `fill_ts` 并列读，单看 `entry_ts` 会把 15 分钟前误当成交时刻 | `02`、`04` |
| 不能把「事后选择过去 bar 开盘价」称为实时成交 | **成立但需附条件**。这笔的 `fill_ts` 晚于 `bar_close` 14,729 ms，是闭 bar 后才记的账；`fill_ts` 与 `entry_ts` 的 914,728 ms 差值在账本里可见。**不能只说「成交在 16:15」**，必须说「成交在 16:30:14.728，价格取 16:15 那根 bar 的 open」 | `02`、`04` |
| 既有规则若只允许固定执行点，不得未经批准改成顺延入场 | **成立**。D3 验证 `available_at` 晚 10 分钟时落点顺延到 `available_at+15m`（这是已批准规则本身，不是新改动）；D5 验证 `bar_open > attempt` 直接拒，**不顺延到下一根**；S2 验证停机后过期执行点直接拒，**不顺延** | `03` |
| 缺证据应为 NULL/unproven | **成立**。289 条研究行 `available_at` 全 `NULL`、`status` 全 `unproven`；`test_probe_succ` 的 `available_at` 也是 `NULL` | `02`、`04` |
| 哈希/报告自述/PID 变化单独不算加载证明 | **本单未用这三项作加载证明**，用的是 API 返回的 `fill_policy` 内容级证据 | `01`、`05` |

---

## 7. 未完成 / 阻塞 / 待裁决

**未完成（本单范围内，需要更长时间或更多证据）**

1. **自然候选→建单→成交闭环**：仍未观测到。段 A（不跨边界）与段 B（跨 20:00Z 的 1h + 4h 双边界，两个水位**实测各推进一格**）合计 140 个样本、约 69 分钟，自然候选**始终 0**。段 B 已把「候选为 0」从「无信息」变成**有信息**：跨边界后状态机新建档 21 个箱体、7 次观察过期，但**没有一个标的跌破下沿**（`SWEEP = 0`）⇒ 短窗口的卡点在 **BOX → SWEEP**。这**不能**读成「策略有问题」，也**不能**读成「通过」，只能读成「这一次 4h 收盘里没有标的跌破箱体下沿」。
2. **真实挂钟自动成交**：仍只有 1 笔既有挂单成交（旧公式入的场），**没有**一笔按新规则自然产生的成交。
3. **`fill_ts` 与 `available_at` 的 1,733 ms 差值**：本单给出强推断（5.2），但未做受控实验。

**阻塞（需要用户/主代理拍板）**

4. **`fill_ts` 是否要改成「撮合完成时刻」**：现状是循环起始时刻，会让延迟被低估。改则需动 `execute_cycle` 的时钟传递（`paper_engine` 与 `unified_runtime` 各一处），**属于改代码，本单无权动**。
5. **D4 缺口是否要补 `bar_close <= attempt_ms` 校验**：补了更安全（多一道防线），但会让「采集器正常但时钟微偏」的场景多一种拒单原因。**属改代码，本单只出探针不动手。**
6. **挂单推进路径上 `earliest_fill_before_first_request_clock` 失效**：补上还是改注释，需裁决。
7. **同一份 API 响应内部，成交时钟存在两套说法，而用户看到的是旧的那套**。`/api/v1/paper/accounts` 一次返回里：

   | 字段 | 原文 | 与实现是否一致 |
   |---|---|---|
   | `quality.fill_policy.no_fill_before` | 「max(signal.event_time, signal.available_at)；无信号时刻（手工请求）才取真实请求时刻」 | **一致**（实现见 `paper_engine.py:1154`） |
   | `quality.note`（`live_api.py:806-809`） | 「成交时钟：**第一根 `open_time >= max(信号确认时刻,信号可用时刻,真实请求时刻)+15m` 的已闭合 15m bar 的 open**」 | **不一致**（旧公式，把「真实请求时刻」当成总在参与计算） |

   ⇒ 前端用户读到的是 `quality.note`，也就是**旧公式**。属改代码，本单不动。

   **顺带三条同响应内的硬事实**：① `fill_policy.ratification_status = "pending_human_ratification"` —— 成交政策**本身尚未获人工批准**；② `defined_by = "work_2026-10-13"`；③ `fill_policy.known_gap` 自己就写着「执行动作**从未在 bar 开盘时刻真实发生**，成交一律是闭 bar 后回读该 bar 的 open，`entry_ts` 恒早于实际处理 15~30 分钟」⇒ 与本单第 6 节的判断一致，且这是**系统自述**，不是本单推断。

8. **`paper_engine.py:989-990` 的 `open_position` docstring 仍写旧公式**：

   > 「成交价 = 第一根 `open_time >= max(信号 event_time, 信号 available_at, 真实首次请求墙钟) + 15m` 的已闭合 15m bar 的 open」

   与 `:1154` 的实现不符。只影响读代码的人，不影响运行。

   **顺带一条正面证据**：同一处 `quality.note` 已写明「既有持仓为**旧版/未验收**，已排除正式策略表现」⇒ 系统自己就把 `test_probe_succ` 排除在成绩之外，与本单判定一致。

**与本次复核无关但顺带登记**

9. `paper_order` 的 `order_id` 有 8 个空号（9、13–16、19–21），现有账本无法自证是被删除还是 SQLite 自增计数被回滚消耗。**两种解释都不影响任何数值结论**，只影响「账本是否完整可重建」这一条。

---

## 8. 产物清单与复现命令

```
evidence/SIGNAL-NOW-01-ANTIGRAVITY/
├── README.md                              本报告
├── scripts/
│   ├── 01_identity_and_load.py            源码哈希 / pyc 取证 / 进程身份 / 内容级加载证据 / 水位
│   ├── 02_uid_penetration.py              289 条逐条 + test_probe_succ 全链 + 四臂污染 + 行情取证
│   ├── 03_isolation_recompute.py          17 场景隔离复算（自建行情库 + 每场景独立账本）
│   ├── 04_time_chain_audit.py             全量行时间链 + 单调关系 + bar 可得性 + fill_ts 语义
│   ├── 05_runtime_observe.py              重载后独立运行采样（只读；AG_TAG/AG_SAMPLES/AG_INTERVAL 分段）
│   ├── 06_uid_ledger_impact.py            UID ↔ 四臂账本双向差集（全量）
│   ├── 07_process_env_and_net.py          进程环境继承与网络暴露面（只读）
│   ├── 08_segment_compare.py              段 A / 段 B 采样对比（读两份产物算差，数字不手抄）
│   └── 09_untouched_proof.py              未动生产 / 未动 Work / 未动交接自证（6 项）
├── out/
│   ├── 01_身份与加载证据.json
│   ├── 02_UID穿透.json
│   ├── 03_隔离复算.json
│   ├── 04_时间字段与单调关系.json
│   ├── 05_运行采样_A.json / 05_运行采样_B.json
│   ├── 06_UID与账本影响表.json
│   ├── 07_进程环境与网络.json
│   ├── 08_分段对比.json
│   └── 09_未动自证.json
├── logs/                                  采样与穿透脚本的原始 stdout（其余脚本的原始输出即 out/*.json）
└── iso/                                   隔离复算临时目录（自建行情库 + 各场景账本）
```

> **为什么 `logs/` 只有 3 个文件**：`02`、`05_A`、`05_B` 是「过程性」脚本，stdout 本身就是证据（采样逐帧、穿透逐条），所以另存原件；`01`/`03`/`04`/`06`/`07`/`08`/`09` 的原始输出**就是** `out/*.json`（脚本把全部结果写进产物并原样打印），不另存重复副本。**这 7 个脚本刻意没有重跑**：`01` 的 `at_utc` 被 `09` 当作「本单开始时刻」使用，重跑会让时间证明失效。

复现（全部只读，可重复跑）：

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PY=/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3
VPY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python   # 仅 07 需要 psutil
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/01_identity_and_load.py
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/02_uid_penetration.py
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/03_isolation_recompute.py   # 约 5 秒
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/04_time_chain_audit.py
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/06_uid_ledger_impact.py
$VPY evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/07_process_env_and_net.py
# 采样：段 A 约 20 分钟（不跨 4h 边界），段 B 约 50 分钟（跨 20:00Z 的 1h+4h 双边界）
AG_TAG=A AG_SAMPLES=40  AG_INTERVAL=30 $PY evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/05_runtime_observe.py
AG_TAG=B AG_SAMPLES=100 AG_INTERVAL=30 $PY evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/05_runtime_observe.py
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/08_segment_compare.py       # 需 A、B 都在
$PY  evidence/SIGNAL-NOW-01-ANTIGRAVITY/scripts/09_untouched_proof.py
```

> 采样脚本必须**真跑**（不能造样本）：它读的是当时的状态文件与只读 SQL，重跑会得到当时的事实，不能复现历史时刻。段 A 与段 B 的原始 stdout 分别在 `logs/05_observe_A.log`、`logs/05_observe_B.log`。

---

## 9. 本单没有做的事（防止误读）

- 没有改 Work 的任何代码、任何产物、任何结论；
- 没有改生产数据（5 个库 sha256 前后逐位一致）、没有改统一交接；
- 没有调用任何下单接口、没有注入任何信号、没有强平、没有删账；
- 没有重启采集器、没有重复启服务、没有停止任何进程；
- 没有改策略阈值 / OI 门槛 / 风险参数 / 评分权重 / 仓位倍数；
- 没有把 `test_probe_succ` 说成自然成交，也没有把隔离正例当成生产行为；
- **没有自行宣布闭环通过** —— 最终验收由主代理做。
