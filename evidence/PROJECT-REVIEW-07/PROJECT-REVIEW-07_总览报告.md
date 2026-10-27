# PROJECT-REVIEW-07｜信号修复后的全项目梳理（总览报告）

- 出单时间：2026-10-25 13:50（北京时间 GMT+8）｜数据截止：2026-10-25T05:41Z
- 性质：**只读盘点 + 缺陷登记**。未改任何交易经济规则、未改代码、未删文件、未搬目录。
- 证据目录：`evidence/PROJECT-REVIEW-07/`（`out/*.json` 为机器可复算原始结果，`*.py` 为可重跑的盘点脚本）
- 判定纪律：只有「通过／不通过／未验证」三态。**自报 PASS、静态代码、隔离验证、实际运行观察分开列**，任何一项都不单独构成通过。

---

## 0. 一句话结论

系统**在跑**，数据**是通的**，账本**是空的**；但「回测用的规则」和「模拟盘正在跑的规则」**不是同一套**，而且历史回测产物里**有一大半打不开、也说不清自己按什么规则跑的**。所以现在**不能**用任何一份历史回测结论去支持「这套策略有效」，也**不能**把回测收益当成实盘预期。

---

## 1. 策略研究：两套名字，三套实现，一套在跑

### 1.1 名字与实体的对应关系（本单核实）

| 用户口径的名字 | 实际实体 | 规范文档 | 实现代码 | 实验产物 | 当前状态 |
|---|---|---|---|---|---|
| **威科夫.唐奇安因子共振** | `WX_OI_RESONANCE_V1`（族 `F_OI_RES`） | `策略/WyckoffMaster/oi_resonance_v1/README.md`（策略说明，非冻结规则） | `策略/WyckoffMaster/oi_resonance_v1/`（core/factors/execution/config） | `回测日志/oi_resonance_v1/`（已归档到 `legacy_archive`） | **仅研究**。注册表 `runnable=False`、`status=retired`；`registry_entry.formal_run_gate()` 硬编码 `formal_run_allowed=False`，G01–G05 未过 ⇒ 禁止正式运行；无下单路径 |
| **威科夫.唐奇安共振动态策略** | `WX_EVENT_TREND_V0_1`，当前以 **`FT-LOW4H`** 规则身份运行（版本串 `FT-LOW4H-EXP-V2.2`） | ① `evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md`（SHA `1f1167de…9a8ab`，13,315B）<br>② `evidence/SYSTEM-CONTRACT-01/02_FT_LOW4H配置身份规范.json` | `策略/WyckoffMaster/event_trend_v0_1/` + 共享执行核 `trading_core/` | `回测日志/event_trend_v0_1/runs*`（2,284 组）＋ `runs_work_dynamic_bt_01`（45 组 freqtrade 链） | **唯一实际运行**（模拟盘 arm 3a，进程 79149）；同时**仍是研究态**（`run_frozen=False`，M4 门禁未过） |
| （历史）威科夫·唐奇安合体 v3.1 | `WyckoffMaster` v3.1 / `F_WX 3a–3d` / M0–M6 | `策略/WyckoffMaster/README.md`、`EXECUTION.md` | 已封存 `策略/WyckoffMaster/_legacy_archive_v31/`（12 文件逐字节一致） | 归档 | **已退役**，不参与运行 |

> 「威科夫.唐奇安因子共振」在代码里没有同名文件——它对应的是 OI 因子共振那一支（`oi_resonance_v1`，因子含 `f_oi_level_rank` 等 0.50/0.30/0.20 权重）。若你指的是别的对象，以本表「实体」列为准去核对。

### 1.2 规范与实现的三处硬分叉（**最重要**）

| 项 | 冻结规则 md（`1f1167de…`） | 实际运行的 FT-LOW4H | 差在哪 |
|---|---|---|---|
| 跟踪止损启用时机 | 首次 4h 收盘浮盈达 **+2R** 才启用 | **入场即启用**（`trailing_trigger_r=0.0`） | 早启用＝更早被扫 |
| 跟踪线取几根 4h | 最近 **10 根**完整 4h 的最低价 | 最近 **1 根**（`trailing_lookback_4h_bars=1`）× (1−5%) | 线更贴价格 |
| 最长持仓 | **10 个自然日**，到期下一根 15m 开盘退出 | `max_holding_days=None`（不执行） | 一套有到期、一套没有 |
| 初始止损 | `P0 = Lr − 0.1A`（结构位） | `实际成交价 × 0.95` | 完全不同的止损锚 |

**结论**：`config.py` 文件头写着「严格对应冻结规则 md，SHA 1f1167de…」，但 `make_ft_low4h_config()` 用 6 个覆盖项把规则换成了另一套。**冻结 md 没有随之更新，也没有第二份 FT-LOW4H 冻结规则文件**。这意味着：任何人拿 `1f1167de…` 这份规范去核对当前模拟盘，都会得出「实现不符合规范」的结论——而实际上是规范过期了。

### 1.3 实验产物按「规则身份」分布（机械复算，`out/09_回测规则身份分布.json`）

| 批次 | 组数 | 规则身份 |
|---|---:|---|
| `回测日志/event_trend_v0_1/runs` | 2,284 | **1,197 组 = 旧 V0_1 规则（2R/10根/10天）**；22 组 = FT-LOW4H；**1,065 组无参数记录** |
| `runs_sep_all_latest` | 528 | 全部旧 V0_1 规则 |
| `runs_aug100` / `runs_2025aug100` / `runs_sep50*` | 354 | 全部旧 V0_1 规则 |
| `runs_ft_low4h_grid_01` / `runs_ft_stop_grid_01/02` / `runs_ft_stop_20_01/02` / `runs_ft_exit_01` / `runs_ft_entry_09` | 279 | **无参数记录**（无法判定规则） |
| `runs_work_dynamic_bt_01`（freqtrade 链） | 45 | 无参数记录；`trades.jsonl` schema 与官方通道不同 |

**直白说**：目前 1,197 组「官方通道」回测跑的是**旧的 2R/10根/10天规则**，而模拟盘跑的是**入场即跟踪/1根/无到期**。这两套结果不能互相背书。旧规则回测里确实出现了 **318 笔 `max_holding_expired`**（10 天强平）与 71 笔 `timeout_240h`——如果谁认为「持仓 10 天已取消」，这些成交记录就是反证。

### 1.4 同一套规则，两套风控预算（**本单新发现，P0**）

即使规则身份对上了，**回测和模拟盘用的钱和仓位上限也不是一套**：

| 参数 | 回测链 | 模拟盘链 | 差 |
|---|---|---|---|
| 初始本金 | **2,000** | **5,000** | 2.5 倍 |
| 最大同时持仓 | **3** | **10** | 3.3 倍 |
| 单名名义上限 | **30%** 权益 | **10%** 权益 | 3 倍 |
| 总名义上限 | **90%** | **80%** | — |
| 入场参与率上限 | **0.1%** | **1%** | **10 倍** |
| 组合初始止损风险 | 1.5% | 5% | 3.3 倍 |
| 滑点 | 3 bps | 5 bps | — |

- 回测侧来源：`config.py::make_ft_low4h_config()`；`backtest_runner.py:790-797` 只覆盖了 6 项（本金/止损/跟踪/风险/费率/滑点/参与率），**仓位数与名义上限没被覆盖，固定取代码默认 3 / 1.5% / 30% / 90%**。
- 模拟盘侧来源：`10_研究参数生效视图_v2_2_user5000_shared.json`（整份读）；执行点在 `paper_engine.py:529`（仓位数）、`:534`（单名上限）、`:1040`（参与率）。

**最要紧的是参与率差 10 倍**——它直接决定「这一笔能不能成交」。回测里被判「成交量不足、不下单」的候选，在模拟盘里会成交 ⇒ **回测的成交率系统性低于模拟盘，回测收益不能线性外推，两者也不能放进同一张表比较**。详见缺陷表 D-02。

---

## 2. 数据处理：币池、冷热、实时、周期、时间口径

### 2.1 币池来源与排除规则（`/api/v1/data/pool-scope`，`out/01_API实时抓取.json`）

- **来源**：交易所官方 `exchangeInfo` / `instruments` 快照里的分类字段（binance `underlyingType`，okx `instCategory`），不是人工名单。
- **分母**：`symbol_pool` 共 **814**（binance 528 + okx 286）；官方分类为加密且在活动范围的 binance 合约 **526**。
- **排除规则（逐条有原因串）**：
  - 非加密类：`EQUITY` 175、`HK_EQUITY` 15、`KR_EQUITY` 8、`COMMODITY` 8、`PREMARKET` 4、`CN_EQUITY` 2、`FX` 1（合计 213，均为 TradFi 合约）
  - 加密但不在活动范围 178（含 `SETTLING` 130、非 USDT 计价、季度合约等）
  - 加密指数 3：`BTCDOMUSDT`、`ALLUSDT`、`DEFIUSDT`（「另列指数，不冒充币」）
  - `PENDING_TRADING` 1
- **口径**：**观察 528 / 允许 526**（528 里含 `ALLUSDT`、`BTCDOMUSDT` 两个指数标的，有数据但不参与决策）。运行身份写的是 `all(526)`。

### 2.2 已存储 / 已接入 / 实际参与决策（三者必须分开看）

| 层次 | 数量 | 证据 |
|---|---:|---|
| **已接入（池内登记）** | 814 | `symbol_pool`（binance 528 + okx 286） |
| **已存储（有 K 线）** | 551 个 (标的,交易所) × 8 周期 = 4,385 组合；**115,662,520 根** | 热库 `coverage_cache` 聚合（`out/` 内嵌于报告，见 §2.3） |
| **实际参与决策** | **526**（仅 binance UM） | 运行状态 `signal_engine.universe=526 / universe_declared=526` |
| 已接入但**没有** K 线 | okx 263 个（286−23） | `coverage_cache` 只有 okx 23 个标的 |
| 有 K 线但**不参与**决策 | binance 2 个（`ALLUSDT`、`BTCDOMUSDT`）＋ okx 23 个 | 池口径排除 / 运行身份只取 binance 526 |

### 2.3 各周期覆盖（热库实测，截至 2026-10-25 05:29Z）

| 周期 | 标的数 | 行数 | 最早 | 最晚 | 记缺根数 |
|---|---:|---:|---|---|---:|
| 5m | 551 | 27,721,313 | **2025-10-04** | 2026-10-25 05:34 | 0 |
| 15m | 551 | 46,848,657 | 2019-12-31 | 2026-10-25 05:29 | 2,887 |
| 30m | 551 | 23,566,877 | 2019-12-31 | 2026-10-25 05:29 | 434 |
| 1h | 551 | 11,717,949 | 2019-12-31 | 2026-10-25 04:59 | 174 |
| 4h | 551 | 2,896,575 | 2019-12-31 | 2026-10-25 03:59 | 49 |
| 8h | **528** | 1,445,400 | 2019-12-31 | 2026-10-24 23:59 | 24 |
| 12h | 551 | 968,302 | 2019-12-31 | 2026-10-24 23:59 | 12 |
| 1d | 551 | 497,447 | 2019-12-31 | 2026-10-24 23:59 | 5 |

- **5m 只有 1 年**（2025-10-04 起），其余 7 个周期从 2019-12-31 起。凡用 5m 做长历史研究的结论都要打折。
- **8h 缺 okx**（okx 无 8h 原生周期，不合成）。
- 缺失根数是 `coverage_cache` 自报的期望差，不是逐根校验结果。

### 2.4 冷热历史

- **热**：`数据/市场数据/db/market_v2.sqlite3`，11 GB，26 表 + 1 视图（`candles` = `candle_versions` ⋈ `candle_heads`），115.7 M 根。
- **冷**：`数据/市场数据/parquet`，19 GB，catalog 声明 **32,920 分片 / 108,208,916 行 / 5.37 GB 声明字节**。catalog 的 `coverage_semantics` 自己写明：「声明不等于逐文件存在性、内容完整性或全市场覆盖验收」。
- **信号引擎的取数方式**：`data_source = merged_cold_hot`，4h 扫描冷 538,104 行 + 热 142,944 行，重叠 121,904 行、**重叠值不一致 0 行**，最终 materialize 559,144 行；526 标的中 505 个满窗、21 个上市偏晚、缺根 0。

### 2.5 实时采集

- 进程 `realtime_collector.py` pid **42278**（10-23 17:00 启动，至今未重启）；`collector_state` 心跳正常，磁盘剩余 26.5 GB（阈值 1.0 GB）。
- `kline_stream_state`：binance 528 标的 / okx 23 标的 × 8 周期全部 `symbols_seen == symbols_planned`，`last_msg_at` 在 1 秒内。
- **未收盘 bar 走独立临时层**（`realtime_klines` / `forming_bar`），不进已闭合历史——这是对的，但意味着「页面最后一根」与「库里最后一根」不是同一根。

### 2.6 时间口径

- 全部 UTC 半开区间；4h/1h/15m 收盘是 `:59:59.999`（**不是**整秒），任何整秒直比都会差 999 ms。
- 入场：候选确认后「下一根严格更晚的 15m 开盘」；`available_at` 与 `execution_earliest_ms` 都是「1h 收盘 + 15m」。
- OI 可用性：`桶起点 + 5m ≤ 决策时刻`；无发布时点证据 ⇒ 拒或 `qualified=False`。

### 2.7 异常合约处理

- 当前 4h 全库**无零价行**（`high/low/close=0` 计数 0）——FIX-01 记录的 3 个「数据坏死」标的（`1000000BOBUSDT`/`PROMPTUSDT`/`PUMPBTCUSDT`）已不在当前窗口。
- 退市/SETTLING 合约保留在池里但标 `in_active_range=0`，不删样本（符合冻结规则「禁止静默删去亏损样本」）。
- 上市偏晚 21 个标的在 bootstrap 里单列（`listed_late`），不当作满窗。

---

## 3. 框架与运行：谁在跑、从哪启动、坏了怎么恢复

### 3.1 当前实际进程（2026-10-25 13:38 实测）

| 角色 | 入口 | PID | 启动时刻 | 端口 | 状态 |
|---|---|---:|---|---|---|
| 后端 API + 回测 API | `框架/backend/adapter.py` | 79140 | 10-25 12:05:01 | 8787 | running，`/api/health` 200 |
| 回测 worker | `框架/backend/workers/backtest_worker.py` | 79143 | 10-25 12:05:02 | — | running |
| 信号+模拟盘运行时 | `框架/backend/unified_runtime.py --arm 3a` | 79149 | 10-25 12:05:05 | — | running，cycle 956→960，arm 3a，526 标的 |
| 实时采集 | `框架/backend/realtime_collector.py` | 42278 | **10-23 17:00:16** | — | running，未随重启 |
| 前端 | `UI界面/web`（vite） | 42341 | 10-23 17:00:26 | 3000 | running |

**根目录**：`/Users/boos/Desktop/威科夫因子共振趋势`（所有配置路径都以它为准，见下）。

### 3.2 配置来源（三层，缺一层就跑偏）

1. **环境变量**（启动脚本注入，进程实际持有的值，已从 `/proc` 等价物读出）：
   `MARKET_DB=…/数据/市场数据/db/market_v2.sqlite3`、`MARKET_ARCHIVE=…/数据/市场数据/parquet`、`PAPER_LEDGER_DIR=…/数据/paper`、`PAPER_LOCK_DIR=…/框架/backend/locks`、`ENABLE_PAPER_RUNTIME=1`、`PORT=8787`、`PYTHONPATH=框架:框架/backend:项目根`。
2. **服务管理器**：`框架/backend/service_manager.py`（`start.sh` 只是它的薄壳）。PID 文件、`service_meta_8787.json`、锁目录、`paper_runtime_3a_status.json` 全由它管。
3. **策略/执行配置**：`策略/WyckoffMaster/event_trend_v0_1/config.py` 的 `make_ft_low4h_config()` 为生效值；模拟盘另有 `paper_engine.approved_params()`（`fee_each_side=0.0005`、`slippage_each_side=0.0005`、`max_holding_hours=240`、`rule=FT-LOW4H`、指纹 `80943cb3bb88`）。

### 3.3 加载版本（身份）

- 运行身份：`strategy_version=FT-LOW4H-EXP-V2.2`、`rule_version=FT-LOW4H`、`param_hash=82dec362c182`（`view_sha256` 全值 `82dec362c18230e1…`）。
- 代码身份：每个 run 的 `inputs.json` 记 `fingerprints/code/combined_sha256` 与逐文件 SHA；当前 `config.py` = `29a42d98…`（注意：**不是**冻结规则 md 的 `1f1167de…`）。
- **`locks/unified_runtime_status_3a.json` 是固定桩**（mtime 10-23 11:18，`cycle_count=42`），**不能当就绪判据**。真正的活状态是 `locks/paper_runtime_3a_status.json`（24 KB，秒级刷新）。
- **登记身份校验当前是「恒 false」**（`/api/v1/strategies/registered` → `identity_ok=false`）：生效视图 SHA `82dec362c182…` 与代码里写死的旧基线 `6d18d222…` 不同；`registered_sha256` 为 `null`（视图里没有 `registered_configs` 键）；`effective_sha256` 实为 `sha256("[]")` ⇒ **18 条预登记配置一条都没读到**。原因是那三个固定接受值属于**已退役的 F_WX 3a–3d（5000 共享模型）**，而视图文件里现在装的是 FT-LOW4H 参数。详见缺陷表 D-06——它不影响交易（`run_frozen=False` 已挡住），但意味着「身份一致」这条放行条件对当前策略**结构上永不成立**。

### 3.4 恢复方式（最短路径）

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
sh 框架/backend/start.sh status --enable-paper   # 看四进程身份是否 matched
sh 框架/backend/start.sh restart --enable-paper  # 受控重启（采集器按防误停策略不动）
```

服务管理器对 runtime 有三级探测：PID 文件 → `paper_runtime_3a_status.json` → `.lock`，任一能证明存活就不重启。`stop` 只停本实例管理的进程。

### 3.5 重复入口与失效状态文件（**只记录，未删**）

| 类型 | 对象 | 事实 | 影响 |
|---|---|---|---|
| 重复入口 | 前端 `vite --port=3100`（pid 55420，10-23 19:19） | 与 3000 的正式前端并存，非启动脚本所起 | 白占内存；改代码时可能看错实例 |
| 重复入口 | `backtest_worker` pid 88794（10-24 15:28） | 指向临时隔离库 `/var/folders/…/isolation_test…`，**孤儿进程** | 占用 worker 名额；队列统计可能被它干扰 |
| 重复入口 | 根目录 10 个 `工具_*.py` + `tools/` + 7 个 `run_batch_*.py`（含 `.bak`） | 多个批次脚本各自复制过 chart 生成逻辑（历史事故根源） | 再跑会写出第二种 chart 格式 |
| 失效 PID | `框架/backend/backend-8799.pid`=66616、`backend-8899.pid`=3436 | 两进程均不存在 | 误导排障 |
| 失效 PID | `框架/backend/logs/runtime-3a-8787.pid`=86233 | 进程不存在（真实是 79149） | 同上 |
| 失效状态 | `locks/unified_runtime_status_3a.json` | 固定桩，cycle 42 | **会被误当就绪** |
| 失效配置 | `框架/ops/config/ops_config.json` | 5 处路径已不存在（`data/market_v2.sqlite3`、`backups/hot`、`backups/manifests`、`框架/模拟实盘生产接通_2026-10-14/`） | 只影响 `框架/ops/ops_common.py` 这套运维工具，不影响服务 |
| 失效目录 | `框架/config/` | 空目录 | 与「配置来源」混淆 |
| 失效锁 | `数据/市场数据/parquet/_export.pid` = 27654 | 进程已不存在；`_export.lock` 同值 | 热转冷导出可能被死锁挡住 |

---

## 4. 回测与执行：三条链，别混着看

### 4.1 三条链的实际走向

| 链 | 入口 | 决策核心 | 成交价来源 | 落盘 | 现状 |
|---|---|---|---|---|---|
| **历史信号/回测（官方通道）** | `backtest_worker` → `backtest_runner._run_event_trend()` | `event_trend_v0_1/runner.py`（4h 箱体 → 1h 事件 → 候选）+ `trading_core/engine.py`（A→B→C→D→E 五相位） | 15m bar：入场用本根 open，出场用 open 或止损价，**均加不利滑点后按 tick 保守取整** | `回测日志/<family>/<batch>/run_*/`：`candidates/chart/equity_curve/inputs/manifest/orders/run/summary/trades` 九件套 | 2,284 组；**1,197 组跑旧规则** |
| **隔离模拟（回放时钟）** | `evidence/SIGNAL-ORDER-TRACE-04` 那类隔离目录 | 同一份 `trading_core` + 隔离 `EVENT_TREND_BATCH_ROOT`/`PAPER_LEDGER_DIR` | 同上 | 隔离 runs + 隔离账本 | 已验证 48 开 + 48 平；**必须显式设 `EVENT_TREND_BATCH_ROOT`**，否则 `/batches` 会回落到生产 |
| **自然模拟（挂钟）** | `unified_runtime.py`（pid 79149）→ `paper_engine` | `MultiSymbolSignalEngine`（526 标的、box_gate=`atr_warmup`）+ FT-LOW4H | 15m bar open + 5 bps 滑点 + tick 取整（入场）；出场见缺陷表 | `数据/paper/paper_3a.sqlite3`：`paper_order/fill/position/equity/funding/reject` | **在跑但零自然成交**：3 笔全人工/UI 测试，10 笔拒单 |

### 4.2 候选 → 订单 → 成交 → 费用/资金费 → 复盘 的关联方式

```
候选 candidate_id ──(signal_id)──> paper_order.event_uid ──> paper_fill.order_id
       │                                    │                      │
       │                                    └──> paper_position.signal_uid
       │                                                           │
       └──(回测侧) candidates.jsonl ──> orders.jsonl ──> trades.jsonl ──> chart.json(trades/stop_trace)
                                                     └──> equity_curve.jsonl
费用：paper_fill.fee（单边 5bps，按成交名义额）｜回测 trades[].entry_fee/exit_fee/fees
资金费：paper_funding（逐结算事件，窗 `(entry_ts, exit_ts]`）｜回测 trades[].funding_fee（`close_bt_order()` 真实结转，Σ逐单 == 交易层 21/21）
复盘：/api/v1/backtests/{id}/{chart,trades,orders,fills,metrics,equity,quality} + 前端「回测」页
```

**两条已确认的关联硬事实**（复核后仍成立）：
1. **交易所订单号不存在**——`paper_order.order_id` 是本机自增键，不得当交易所订单号使用。
2. **`candidate_id` 是窗口无关的跨运行键**；但 `paper_order`/`paper_blocked` 只有 `event_uid`、没有 `signal_uid`，所以「候选→订单」只能按 `event_uid` 字符串匹配，不是外键。

### 4.3 自然模拟为什么是 0 笔

- 当前 `idle_breakdown`：**IDLE_COOLING 140 + IDLE_WAIT_BOX 27 = 167**；`atr_caliber`：526 引擎、**已预热 525**、建档闸门开 525；`history_candidates_discarded=18`（bootstrap 期间的历史候选，按规则不入账）。
- 新开仓闸门 **是开着的**（`allow_new_entries=true`，`reason=user_ui_resumed`），`orders_routed_total=0`、`open_positions=0`。
- 也就是说：**不是被闸门拦着，是 10-23 09:47（`live_start`）之后这 526 个标的真的没走出新的合规事件链**。冷却 140 个是最近 24h 内刚结束事件的标的。

---

## 5. 测试验收：四类证据分开列

| 证据类别 | 本单取到什么 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| **自报 PASS** | 台账 `33_SIGNAL-ZERO-CAUSE-05` 自报 17/17、98/98、44/44 | 只能证明「当时那份回执声称如此」 | 不能代替本轮复算 |
| **静态代码** | 本单读了 `policy.py`/`engine.py`/`engine_adapter.py`/`paper_engine.py`/`backtest_api.py`/`useMarketData.ts` 等 | 能定位实现逻辑与分支 | 不能证明运行时走的就是这条分支 |
| **隔离验证** | 合成用例复现 4h 跟踪通道「借用入场前 4h」（§6.1）；`/chart` 400 堆栈复现（§6.3） | 能证明缺陷**可复现** | 不能证明生产已发生 |
| **实际运行观察** | 进程/端口/心跳、API 19 个端点抓取、44 个 run 抽样重开、账本 13 单 6 成交逐条读 | 能证明**此刻**的真实状态 | 不能证明历史时刻的状态 |
| **测试套件** | `pytest 策略/WyckoffMaster -q` → **150 passed**（含 `test_ft_low4h_parity_closure.py`、`test_fill_precision.py` 共 36 passed） | 能证明已覆盖的断言成立 | 覆盖不到的缺陷照样存在（本单 12 条缺陷全在测试盲区（见 `缺陷表.md`）） |

---

## 6. 五项遗留问题复核结论（详见 `四项重点验收表.md`）

| # | 问题 | 结论 | 一句话 |
|---|---|---|---|
| 1 | 4h 止损是否使用入场前价格 | **实际未发生，代码有漏洞** | 有跟踪上移的成交共 **60 笔（全目录）/ 57 笔（官方 runs 子集）**，首条跟踪线**全部**晚于「入场后第一根 4h 收盘」⇒ 60/60 合规；但通道闸门按「同币全部候选」判，合成用例能造出「用入场前 4h 低点抬止损」（缺陷 D-03） |
| 2 | 跳空成交价格是否符合规则 | **实现符合规则，两端口径不一致** | 规则＝开盘价再加不利滑点；代码 gap 退出只用 open 再乘 (1−滑点)，方向正确；但回测 3 bps、模拟盘 5 bps；且模拟盘历史平仓价未按 tick 取整 |
| 3 | 图表请求周期 vs 返回周期 | **单标的成立，组合与异常格式不成立** | binance 8 周期 8/8 一致；前端不校验响应 tf（D-07）；组合 run 默认给的是 AVAXUSDT/1h 且把 `exec_timeframe` 改写成 1h（真实执行 15m） |
| 4 | 回测K线/身份/订单/成交能否重开追溯 | **部分能，且能看的也未必对** | 3,989 个 run 目录，九件套齐 1,717；**375 个打开直接 400（D-04）**；**2,677 个 `inputs.json` 无参数身份 ⇒ 不可复算（D-05）** |
| 5 | 报告互相矛盾/过时/夸大 | **确认 6 处** | 见 `缺陷表.md` 第三节 R-01~R-06（其中 R-06 是台账 `25_SIGNAL-LINK-01` 的「主因=供给稀缺」已被 `33_SIGNAL-ZERO-CAUSE-05` 推翻，旧台账仍可被引用） |

---

## 7. 本单没做的事（边界声明）

- 未修改任何代码、规则、参数、配置；未重启任何进程；未删除/移动任何文件（含失效 PID、死锁、旧证据）。
- 未运行新的全池回测；未触发真实下单。
- 未对历史 run 做修复性回填（chart 格式、缺失身份）——按「先记录、后裁决」处理。
- 所有数字均可由 `evidence/PROJECT-REVIEW-07/*.py` 重跑复算；`out/*.json` 为原始结果。

---

## 附：本单证据文件索引

**四件交付物**

| 文件 | 内容 |
|---|---|
| `PROJECT-REVIEW-07_总览报告.md` | 本文件：策略研究 / 数据处理 / 框架运行 / 回测执行 / 测试验收 |
| `运行关系图.md` | 真实数据流与运行关系图（4 张图：运行关系、三条链、一笔交易的串链、三处同名不同物） |
| `四项重点验收表.md` | 四项重点逐项验收（自报 PASS / 静态代码 / 隔离验证 / 实际运行观察 分开列） |
| `缺陷表.md` | 按严重程度排序的 12 条缺陷（证据、影响、负责人、最小下一步）＋ 6 处报告矛盾清单 |
| `操作说明.md` | 启动 / 看状态 / 查信号原因 / 打开复盘 的准确命令与页面路径 |

**机器可复算原始结果**

| 文件 | 内容 |
|---|---|
| `out/01_API实时抓取.json` | 19 个 API 端点原始响应（含抓取时刻） |
| `out/02_图表周期一致性.json` | binance/okx × 8 周期的请求 vs 返回间隔实测 |
| `out/03_回测可追溯性.json` | 3,989 个 run 目录的九件套/chart 契约/身份盘点（逐 run） |
| `out/04_回测重开实测.json` | 三类 run 的 `/chart` 重开结果 |
| `out/05_回测重开抽样.json` | 44 个 run 分层抽样重开（HTTP/K线/成交/周期） |
| `out/06_图表格式冲突.json` | 对象格式 1,399 / 数组且非空 375 / 数组但空 2,215 |
| `out/07_4h止损入场前价格复核.json` | 37,404 笔成交的跟踪线时点判据统计（60 笔有上移、60/60 合规） |
| `out/08_同币多笔暴露面.json` | 859 组同币多笔、3,346 笔第 2 笔及以后、其中 8 笔带跟踪上移 |
| `out/09_回测规则身份分布.json` | 4,982 个 inputs.json 的规则身份聚合 |
| `03_回测可追溯性盘点.py` / `07_4h止损入场前价格复核.py` / `09_回测规则身份盘点.py` | 可重跑脚本 |
