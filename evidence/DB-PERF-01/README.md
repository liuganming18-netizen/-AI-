# DB-PERF-01 数据库架构与查询性能优化 · 收口报告

> 结论先行：**「链路不通」的根因不是数据库慢，是两条接口在路由表里缺失 + 前端漏了预取。**
> 修掉之后画布从「1 段通过 / 3 段等待 / 3 段未验证」变成 **「5 段通过 / 1 段等待 / 1 段未验证」**。
> 数据库层另做了 2 个索引 + 连接级 PRAGMA 调优 + 缓存策略改造，`/data/feeds` 从 4.5 秒降到 **2 毫秒**。
> **剩下 2 段不是性能问题，也不是我能擅自改的**：1 段是真实等待条件（0 条可执行信号），
> 1 段是冷库内容完整性确实没验过（详见第 5 节）。

- 采集时刻：2026-10-24T02:50Z 前后（每张表都带 `captured_at_utc`）
- 构建标识：`dbperf01-20261024`
- 服务：API pid 41184 / Worker 41194 / Runtime 41198（重启后跑的是本轮代码）
- 证据目录：`evidence/DB-PERF-01/`

---

## 1. 你看到的「还是没有通」，拆开是三件不同的事

截图上是 7 段全非绿。实际拆开是三类**完全不同**的问题，混在一起看就会误以为「都是数据库慢」：

| 段 | 截图状态 | 真实原因 | 性质 |
|---|---|---|---|
| 1 交易所来源 | 等待条件 | `/api/v1/data/pool-scope` 没被预取，`R()` 恒为 null | **代码缺陷** |
| 2 实时采集 | 已验证可用 | 本来就通 | — |
| 3 热库／冷库 | 未验证 | 冷库接口 `/data/cold-archive-status` 在路由表里**缺失 ⇒ 404**；且前端也没预取 | **代码缺陷 + 真实未验证** |
| 4 数据质量 | 未验证 | 依赖的 `pool-scope` 取不到，连带降级 | **代码缺陷** |
| 5 信号引擎 | 等待条件 | 526 标的全量在扫、水位不滞后、**当前 0 条可执行信号** | **真实等待条件，不是故障** |
| 6 模拟账本 | 等待条件 | 依赖链上游取不到 | **代码缺陷** |
| 7 工作台／回测对账 | 未验证 | 对账接口 `/system-flow/paper-reconciliation` 也**缺失 ⇒ 404**；证据文件还缺 3 个必需字段 | **代码缺陷 + 证据不完整** |

**关键发现：** 页面自己的 `CHAIN_PATHS` 预取表只列了 8 条路径，
而第 3 段和第 7 段的判定代码要读第 9、10 条接口。`R(path)` 只从预取结果里取，
所以这两段**永远拿不到响应**，无论后端多快都只会显示「未验证」。
这不是性能问题，靠加索引永远修不好。

---

## 2. 改了什么（5 个文件 + 2 个索引）

### 2.1 后端路由表补齐 2 条接口 —— `框架/backend/api/routes.py`

`metrics_api.handle_cold_archive_status` 和 `handle_paper_reconciliation_evidence`
两个处理函数**早就写好了**，`adapter._route_v1_legacy` 里也接了，但新路由表
（`api/routes.py::build()`）**没注册**。而 `route_v1()` 默认走新表
（`LEGACY_ROUTER=0`），于是这两条接口实际返回 `{"错误":"接口不存在"}` + 404。

补上后与 legacy 长链行为完全一致（`metrics_api` 不可用时回退 `missing(...)`，不抛 500）：

```python
Route("/api/v1/data/cold-archive-status", ..., group="light", cache=5, ...)
Route("/api/v1/system-flow/paper-reconciliation", ..., group="light", cache=5, ...)
```

**注意：这只让接口从 404 变成 200。** 200 不等于通过 —— 第 7 段要的
`status === 'verified_historical_sample'` 还得证据文件说了算（见 2.4）。

### 2.2 前端补预取 —— `UI界面/web/public/system-flow.html`

`CHAIN_PATHS` 从 8 条补到 10 条。这是**修 bug**，不是放宽判据：
页面判定代码本来就声明要读这两条接口，只是漏在了预取表外面。

### 2.3 冷库接口做真实的逐分片存在性核对 —— `框架/backend/metrics_api.py`

原来只报 `status: "indexed"`（catalog 文件能读），前端因此只能说
「分片存在性未验证」。现在把 catalog 声明的 candles 分片**逐个 `os.stat`**：

```
声明 12,551 · 实际存在 12,498 · 缺失 53
缺失标的：ALLUSDT、BTCDOMUSDT（两个都是允许池里被排除的标的）
耗时 32~560 ms（视 OS 目录缓存冷热）
```

**这 53 个缺失是真实存在的**，不是测量误差。它们属于历史遗留声明：
catalog 里写着有，盘上没有，而这两个标的本来就被排除在策略池外。
所以正确处理是**修 catalog**，不是补数据。

结果：第 3 段从「未验证（不知道为什么）」变成
「**已查清**：53 个缺失，都是已排除标的；内容完整性还没验」。
判定仍然是「未验证」，因为 `content_integrity_verified: false` ——
只 stat 了存在性，没读 Parquet 内容、没校验行数。

### 2.4 重跑真实对账，补全证据文件 —— `evidence/CODEX-CLOSEOUT-01/out/ui_paper_reconciliation.json`

原文件 17/17 全过，但**缺三个必需字段**：`generated_at_utc`、`scope_kind`、`source_hashes`。
接口的判据要求「生成时刻非未来且 48 小时内 + 范围是 historical_sample_mechanism +
现存 run 目录 + 17/17 断言 + 全部必需源文件哈希一致」，缺一个就是 `unverified`。

用原始探针 `probes/verify_ui_run.py --run-id bt_20261023T162149Z_59f3c8fa` 重跑：

```
17/17 通过 · 16 个源文件哈希全部 match · status: verified_historical_sample
```

### 2.5 数据库层：2 个索引 + 连接级 PRAGMA + 缓存策略

**（a）连接级 PRAGMA（零风险，不改库文件）**

库 11.2 GB、`oi_history` 6.7M 行、`funding_history` 2.1M 行，
而 SQLite 默认 `cache_size` 只有 **2 MB**（`-2000`），默认 `temp_store` 还是**文件**。
统一在 `_connect_ro()` 里设：

```python
PRAGMA cache_size = -262144   # 256 MB 页缓存
PRAGMA temp_store = MEMORY    # GROUP BY 的临时 B 树不落盘
```

**（b）2 个索引（additive，可 `DROP INDEX` 回滚）**

| 索引 | 为什么必须建 | 实测效果 |
|---|---|---|
| `ix_fund_hist_symbol_time` on `funding_history(symbol, funding_time)` | 主键是 `(exchange, market, symbol, funding_time)`，`GROUP BY symbol` 跨所合并时用不上有序前缀 ⇒ 必须建临时 B 树 | **494 ms → 89 ms（↓82%）**，`USE TEMP B-TREE FOR GROUP BY` **消失** |
| `ix_oi_hist_symbol` on `oi_history(symbol)` | `count(DISTINCT symbol)` 原来要扫 `(exchange,symbol,period,ts)` 四列宽索引 | **356 ms → 184 ms（↓48%）** |

建索引耗时：1.2 秒 + 9.17 秒；库体积 11,238 MB → 11,290 MB（+51.7 MB）。

**（c）`/data/feeds` 改「先给旧值、后台刷新」**

原来：15 秒缓存一过期，下一个真实用户就要等一次完整查询（实测 3.77 秒）。
现在：过期后**立刻返回旧值**，同时后台单飞（single-flight）刷新。
只有进程刚启动、完全没有旧值时才同步等一次 —— 而这一次也已经在
`adapter._prewarm` 里预热掉了。

**（d）`oi_history` 三合一聚合拆成三条**

`count(distinct), count(*), max(ts)` 放在一条 SQL 里时，优化器只能挑一个索引
兼顾三个聚合（实测 433 ms）。拆开后各走各的索引：184 + 20 + 174 = **379 ms**。

---

## 3. 实测数据（全部可机械复算）

### 3.1 数据库查询（中位数，同一连接内每条跑 3 次取中位）

| 查询 | 优化前 | 优化后 | 变化 |
|---|---|---|---|
| `funding_stale_groupby`（找停更标的） | 494.29 ms | 89.42 ms | **↓82%** |
| `funding_agg`（费率三合一） | 120.95 ms | 88.26 ms | ↓27% |
| `oi_agg`（OI 三合一→拆三条） | 433 ms | 379 ms | ↓12% |
| `coverage_by_tf`（15m 覆盖） | 0.12 ms | 0.12 ms | 无变化（本来就快） |

复算：`python evidence/DB-PERF-01/probes/20_db_perf.py baseline`
（基线 JSON：`out/perf_baseline.json`；加索引后：`out/perf_after_index.json`）

### 3.2 `/api/v1/data/feeds`

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 进程启动后**第一个**用户请求 | **4,535 ms** | **2 ms**（启动预热） |
| 15 秒缓存过期后的请求 | **3,772 ms** | **2 ms**（先给旧值 + 后台刷新） |
| 连续请求 | 0.6 ms | 0.5 ms |

### 3.3 画布 13 个依赖接口（热态，一次真实 HTTP）

```
  200      3.5       1271  /api/v1/data/pool-scope
  200     19.9       3649  /api/v1/pool/status
  200      1.5      12321  /api/v1/data/import-plan
  200      0.7     932593  /api/v1/data/status
  200      0.4       2035  /api/v1/data/cold-archive-status      ← 本轮从 404 修好
  200      6.3       1972  /api/v1/paper/status
  200    351.0      29606  /api/v1/paper/accounts
  200      0.6       1609  /api/v1/signals?scope=current
  200    119.4      10787  /api/v1/backtests?limit=1
  200     49.8       3368  /api/v1/system-flow/paper-reconciliation ← 本轮从 404 修好
  200      0.5      12972  /api/v1/data/feeds                     ← 本轮优化
  200     80.1    7684331  /api/v1/data/coverage                   ← 体积仍是最大负担
  200      4.4    6033809  /api/v1/markets

合计 638.1 ms · 最大 351.0 ms · 总字节 14,730,323
```

优化前同口径首次合计 **3,790.9 ms**。

复算：`python evidence/DB-PERF-01/probes/30_api_perf.py`
（落盘：`out/api_perf_after.json`）

---

## 4. 画布七段：逐段结论（浏览器实测，不是接口拼凑）

复算：`node evidence/DB-PERF-01/probes/10_canvas_state.mjs`
（落盘：`out/canvas_state.json` + `out/canvas_full.png`）

| 段 | 结论 | 依据 |
|---|---|---|
| 1 交易所来源 | **已验证可用** | 池分母接口 200，口径一致 |
| 2 实时采集／历史归档 | **已验证可用** | 各周期上游写入水位可得 |
| 3 热库／冷库 | **未验证** | 存在性已逐个 stat：12,551 声明 / 12,498 存在 / **53 缺失**（ALLUSDT、BTCDOMUSDT，均为已排除标的）。**内容完整性未校验**（不读 Parquet、不验行数）⇒ 按纪律不判绿 |
| 4 数据质量与已闭合水位 | **已验证可用** | 上游写入 = 热库最后闭合 = 引擎实际读取，滞后在阈值内 |
| 5 信号引擎 | **等待条件** | 526 标的全量在扫、周期新鲜、水位不滞后，**当前 0 条可执行信号**（策略极窄）。这是等待，不是故障，也不计入通过 |
| 6 模拟账本 | **已验证可用** | 账户/持仓可读，权益落账 |
| 7 工作台／回测对账 | **已验证可用** | 17/17 逐笔对账 + 16 个源文件哈希全 match，`verified_historical_sample` |

**页面结论栏：**「全链无故障；有 1 段在等待条件」+ 1 段未验证
（`已验证 5 · 等待 1 · 故障 0 · 未验证 1`）。

> 第 7 段虽然判绿，但它自己的 blocker 写得很清楚：
> 「证据范围仅为同一历史样本机制闭环；**当前自然行情成交与策略有效性仍未验证**」。
> 这句话不是客套 —— 它确实是当前系统的真实边界。

---

## 5. 我没做的事，以及为什么

### 5.1 没有把第 3 段「改成绿的」

冷库确实有 53 个声明分片不在盘上，内容完整性确实没验过。
如果为了让截图好看而把 `state` 从 `unv` 改成 `pass`，那是在撒谎。
**正确做法是修 catalog（删掉 53 个已排除标的的遗留声明），然后才谈判绿。**
要不要修，等你点头。

### 5.2 没有改 `/data/coverage` 的 7.68 MB 响应

它是首屏最大的单笔传输（7.68 MB，占 13 个接口总字节的 52%），
但画布实际只用聚合值（`cells` 计数和 `byTf[tf].rows`），不需要逐标的明细。
可以加 `?agg=1` 只返回聚合（预计 7.68 MB → 几 KB），
但那是**改接口契约**，要动 `v1_coverage` 签名 + 路由 + 前端取值。
按项目纪律，改契约要你拍板，我不擅自做。

### 5.3 没有改回测列表的 3 秒缓存

`/backtests?limit=1` 热态 119 ms、冷态首次约 2.2 秒（扫 2,269 个 run 目录）。
它已有 3 秒 TTL 缓存，且**「存在非终态 run 时不写缓存」**是 BT-UI-01 的明确设计
（状态变化要立刻可见）。把 TTL 拉长会让新建 run 迟迟不出现，所以不动。

### 5.4 没有擅自把 `EPICUSDT` 加进「已下架白名单」

优化过程中 `/data/feeds` 报出一条真实告警：
**`EPICUSDT` 资金费率停更 90.6 小时**（最后结算 2026-10-20 08:00Z），
而它**仍在正常交易**（盘口 2 分钟前更新，价格 0.183709）。

白名单里现有 3 个标的（`1000000BOBUSDT`、`PROMPTUSDT`、`PUMPBTCUSDT`）
停更 450 小时。把 `EPICUSDT` 加进去就等于把真故障说成「正常」，
所以保持红色告警，如实报出来。

**这条需要你决定**：是去修采集，还是确认它真的该停更。

---

## 6. 测试回归

`pytest tests/ -q`（服务停止状态下跑，避免 `worker_already_running` 干扰）：

```
15 failed, 364 passed, 79 skipped
```

15 个失败**全部不是本轮引入**，分两类：

**（a）2 个已知留红，需要你裁决**（上一轮 CODEX-CLOSEOUT-01 就留着）
- `test_frame_fit_01::T07_RealEntrypoint::test_create_run_rejects_pinned_window_mismatch`
- `test_frame_fit_02::T06_IllegalStillRejected::test_pinned_window_mismatch_rejected`

这两条断言「执行链写死 2024H1 窗口 ⇒ 必须 409」。但新基线**已经删掉了写死窗口**，
同请求现在合法返回 202。改代码造假 = 撒谎；改断言 = 改验收契约。两条都要你点头。

**（b）13 个既有环境/配置问题**
`test_system_isolation_03/04/05` + `test_system_startup_01`，
失败点在 `service_manager.py:118` 的**启动前路径校验**
（`隔离模式缺少必要路径配置：['market_archive', 'jobs_db']`）
和 `worker_already_running`，都是测试 fixture 与生产环境共存的老问题。

**证明方式（做过，不是推断）：** 把本轮唯一可能影响启动的改动
（`adapter._prewarm` 里的 feeds 预热）**临时摘掉**，单独跑这 4 个测试文件：

```
13 failed, 10 passed   ← 与带着预热跑的结果完全一致
```

所以这 13 个与本轮改动无关。验证完已恢复预热（`adapter.py` 已确认含预热代码）。

**本轮改动的 4 个后端文件对应的测试全部通过**：
`test_metrics_contract.py`、`test_data_path_01.py`、`test_bt_ui_01/02`、`test_business.py`。

---

## 7. 顺手清掉的残留进程

跑测试过程中发现 5 个残留进程占着生产 `jobs.sqlite3`，导致正式 Worker 起不来
（`worker_already_running`）。全部确认是测试/历史工单遗留的临时实例后停掉：

| pid | 身份 | 处理 |
|---|---|---|
| 33724 | `adapter.py` 监听 49262（10-24 09:48 测试残留） | SIGTERM |
| 76245 | `adapter.py` 监听 55691（10-23 22:16） | SIGTERM |
| 77544 | `adapter.py` 监听 56447（10-23 22:19） | SIGTERM |
| 55012 | `adapter.py` 监听 8799（10-23 19:16，昨日游离） | SIGTERM |
| 40679 | `backtest_worker.py --owner service_manager_62782`（测试残留） | SIGTERM |

只停了命令行含本项目路径的进程，其它 python 一律跳过。

---

## 8. 复现步骤

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势

# 1. 画布七段（真浏览器，需要前端 3000 与后端 8787 都在跑）
node evidence/DB-PERF-01/probes/10_canvas_state.mjs

# 2. 数据库查询基线（只读，不改库）
python evidence/DB-PERF-01/probes/20_db_perf.py baseline

# 3. 接口耗时与体积
python evidence/DB-PERF-01/probes/30_api_perf.py

# 4. 重跑逐笔对账证据
python evidence/CODEX-CLOSEOUT-01/probes/verify_ui_run.py \
       --run-id bt_20261023T162149Z_59f3c8fa

# 5. 回滚本轮索引（如需）
python evidence/DB-PERF-01/probes/20_db_perf.py drop-index
```

---

## 9. ~~需要你拍板的 3 件事~~ → 已按「修复问题」全部落地

上面三件加一个待确认事实，都已在本轮直接办完，不再需要拍板。下面逐条写「原来怎样、改了什么、现在什么结果、怎么复算」。

### 9.1 冷库 53 个缺失分片 → 删的是过期声明，不是数据

- **备份**：`数据/市场数据/parquet/_catalog.json.bak-20261024T0333Z`（11,597,348 B）。
- **删了什么**：53 条声明 —— `ALLUSDT` 14 条 + `BTCDOMUSDT` 39 条。两者在交易所的状态是 `TRADING`，但它们是**指数类合约**（全部币种综合指数 / BTC 主导率），本就被排除在策略池 526 之外，本来就不该有 K 线归档。所以是「catalog 多写了」，不是「数据丢了」。
- **改后**：分片声明 32,973 → 32,920；`sha256` 由 `9f0d2c6a…4424a0` 变为 `c5a9b3d0…efd02f`；文件里新增 `pruned` 字段记录时间/原因/样例，可追溯。
- **实检结果**：声明 12,498 / 存在 12,498 / **缺失 0**。

### 9.2 冷库做真正的三层实检，而不是「看到 indexed 就放行」

以前第 3 段只有一句「catalog 可读」，这跟「分片真的在盘上、内容真的对得上」是两件事。现在 `metrics_api._verify_cold_shards()` 做三层：

| 层 | 方法 | 范围 | 结果 |
|---|---|---|---|
| 存在性 | 逐个 `os.stat` | **全量** 12,498 | 12,498/12,498 存在，缺失 0，字节数差 0 |
| 行数 | 读 Parquet footer `num_rows` | **全量** 12,498 | 12,498/12,498 一致，合计 108,208,916 行，读取失败 0 |
| 内容哈希 | sha256，**固定步长均匀抽样 5%** | 抽样 624 | 624/624 一致，读 258,479,928 B |

耗时拆分：`stat` 32 ms + `rows` 3,892 ms + `hash_sample` 2,471 ms。接口 TTL 提到 600 秒，并加了启动预热，所以请求路径上通常只花 1 ms 读缓存。

**这里必须说清楚「已验证」到哪一步**：`content_integrity_verified=true` 的含义是「存在性全量通过 + 行数全量通过 + 哈希抽样无异常」。它**不**等于「5.4 GB 逐字节全量校验过」，也**不**等于「catalog 声明的范围 = 全市场覆盖」。这两条已写进接口的 `limitations` 和页面的判定说明里。

### 9.3 `/data/coverage` 加 `?agg=1`，默认行为不变

| | 响应体积 | 说明 |
|---|---|---|
| 完整（默认，不传参） | 7,684,331 B | 13,155 条逐单元格明细，契约**未改** |
| `?agg=1` | **2,335 B** | 只返计数与分布，**缩小 3,290.9 倍** |

聚合里给的是能对得上的真实字段，不是拍脑袋的计数：

- `totals`：13,155 个单元格 · `bars_expected` 115,369,360 根 · `bars_missing` 3,585 根 · `gaps_unknown` 0。
- `by_timeframe` / `by_field` / `by_exchange`：按 5m…1d 各周期、按 klines/oi/funding、按 binance/okx 分桶。
- `bars_missing` 只统计**已逐段定位过**的缺根；`gaps_unknown` 是「缺根数未知」的单元格数（键不存在=该字段不适用，不计入）。两者**不混算**。

### 9.4 两条断言 → 改写为守护新契约，双双通过

- 旧断言要求「请求窗口与登记不一致 ⇒ 409」，它守护的是一个**已被删除的缺陷**（执行链曾把窗口写死成 `2024-01-01 ~ 2024-06-30`）。继续要求 409，等于要求把缺陷留着。
- 同时清掉死代码：`experiment_registry.execution_scope_mismatch()` 显式恒返回 `None`（原检查对象已不存在），并保留字段与常量供 UI/证据读取。窗口不一致现在由**设计身份比对**拦截，实测比旧机制更严 —— 连「请求窗口是登记窗口的子集」都会被拒。
- 改写后的断言：同窗口 ⇒ 断言 `202`；换窗口 ⇒ 断言 `409` 且错误里含「与登记身份不一致」。**两条现在都通过。**

### 9.5 `EPICUSDT` 资金费率停更 → 确认是正常终止，不是采集故障

- 直查交易所：`fapi/v1/exchangeInfo` 返回 `EPICUSDT status=SETTLING`（清算中）；`premiumIndex` 的 `lastFundingRate=0`、`nextFundingTime` 冻结；最后结算 10-20 08:00Z。
- 所以「停更 90.6 小时」是**该标的已经停止结算**，属正常终止。
- 处理方式：加入 `KNOWN_SETTLING_SYMBOLS`，**带判定依据与核对时刻**（不是无依据静音）。原变量名 `KNOWN_DELISTED_SYMBOLS` 改名为 `KNOWN_SETTLING_SYMBOLS` —— 它描述的是「已停止结算」，不是「已下架」，名字得对。
- **踩过的坑**：本地 `symbol_pool.status` 里 `EPICUSDT` 仍写 `TRADING`，因为那份快照是 10-05 的。**陈旧快照不能用来判断当前状态**，必须直查交易所。
- 现在 17 路数据源全部 `normal`，异常 0 路。

---

## 10. 本轮额外发现并修掉的 5 个缺陷（不修就会一直假红/假绿）

这 5 个不是「顺手优化」，是**会让画布说假话**的东西，逐个列清楚：

1. **页面脚本语法错误 ⇒ 整页 JS 根本不执行。**
   `system-flow.html` 第 3 段的说明文案里漏了两个 `+` 号，两个字符串字面量挨在一起 ⇒ `SyntaxError: Unexpected string`。
   后果：画布永远停在「正在读取数据链路…」，chips 和节点都是 0 个。
   **必须说明**：上一轮报告第 4 节那个「5 通过 / 1 等待 / 1 未验证」是在这次文案改动**之前**抓的；改完之后页面其实是坏的，本轮才修好并重新抓。这条如果不写出来，等于用一个失效的数字充当验收结论。

2. **抓画布的探针假超时。**
   `page.waitForFunction(fn, {timeout:120000})` —— 第二个位置参数是「传给页面函数的 arg」，不是 options，所以实际一直用默认 30 秒。页面稍慢就报超时，看起来像「页面坏了」。改成 `waitForFunction(fn, null, {timeout:120000})`。

3. **「刚启动还没跑第一轮」被当成「跑过但停了」⇒ 故障。**
   引擎刚起时 `last_cycle_ms` 必然为空，旧逻辑一律判 `fail`。现在按运行时长分档：仍在停滞阈值（600 秒）内 ⇒ **等待**，措辞写明「引擎刚启动 X 秒，还没跑完第一轮扫描」；超过阈值仍无周期 ⇒ 才判故障。

4. **状态文件属于已退出进程，却被说成「运行时 stopped」。**
   进程重启后，新实例要先 import 一堆重模块（实测约 20 秒）才写出第一份状态；这段窗口里读到的是**上一个实例的临终记录**（它 `finally` 里写过 `stopped`）。旧逻辑据此判「运行时停了 ⇒ 故障」，每次重启都会假红一次。
   修法：`get_unified_runtime_status()` 新增 `record_pid_alive` / `record_written_ms` / `record_age_ms`；当记录里的 pid 已退出时，`status` 标为 **`stale_record`**（原值保留在 `record_status_raw` 里），页面据此判「未验证（等待交接）」而不是「故障」；只有记录已经很旧、确实没人接手，才判故障。

5. **`?agg=1` 里读错了字段名。**
   聚合原本累加 `cell["rows"]`，但逐单元格根本没有 `rows` 字段（真实字段是 `expected`），于是 `rows` 恒为 0 —— 看着有数，其实全是 0。改用 `expected` 与 `missing_within_observed_range`，并区分「键不存在」与「值为 NULL」。

---

## 11. 本轮最终实测（真浏览器 + 真接口，2026-10-24T03:57Z）

**画布七段：6 段已验证可用 / 1 段等待条件 / 0 故障 / 0 未验证。**
页面结论栏原文：「全链无故障；有 1 段在等待条件 —— 5. 信号引擎 —— 等待不等于通过。」

| 段 | 状态 | 结论要点 |
|---|---|---|
| 1 交易所来源 | 已验证可用 | — |
| 2 实时采集／历史归档 | 已验证可用 | — |
| 3 热库／冷库 | 已验证可用 | 三层实检通过；未验的只有「哈希非全量」与「catalog 范围≠全市场」 |
| 4 数据质量与已闭合水位 | 已验证可用 | — |
| 5 信号引擎 | **等待条件** | 526 标的全量在扫、周期新鲜、水位不滞后，当前 **0 条可执行信号**（策略极窄，不是故障） |
| 6 模拟账本 | 已验证可用 | — |
| 7 工作台／回测对账 | 已验证可用 | 历史样本机制 17/17；不代表自然行情成交与策略有效性 |

**逐笔对账**：`verified_historical_sample` · 17/17 · 源文件哈希 16/16 全 `match` · run `bt_20261023T162149Z_59f3c8fa` · 成交 `trd_edfeb086…`。

> 关于「复算说明」：本轮因为改了 `unified_runtime.py`（第 10 条第 4 项），它在对账证据的 16 个哈希文件里，所以证据当场失效、第 7 段翻红 —— 这是机制在正常工作，不是 bug。按原脚本重算了 17 条断言（真读接口 + 真读隔离账本 + 重算字段），17/17 通过，并在证据里写入 `reverify_note` 说明「改了哪个文件、为什么改、为何不影响对账结论」，接口与页面都会把它显示出来。**不写这句，就等于用「哈希全 match」把漂移盖住。**

**测试回归**（服务停止态）：**13 失败 / 366 通过 / 79 跳过**，较上一轮 **15 失败** 减少 2 个 —— 正是本轮改写的那两条断言转绿。剩下 13 个全是既有环境问题（`worker_already_running`、隔离路径校验缺项），与本轮改动无关。

**复算方式**：`python evidence/DB-PERF-01/probes/40_fix_verify.py` → `out/fix_verify.json`。上面每个数字都能从该文件的原始字段读出来。

---

## 12. 仍然不通过的（不要因为画布好看就当成通过）

1. **两套止损规则仍未裁决**：模拟盘「成交价×0.95」vs 回测「Lr−0.1A」⇒ 同一信号仓位差约 3.8 倍、出场全分歧。
2. **自然行情 0 笔成交** ⇒ 「真实挂钟自动成交」仍未验证。这是**等待**，不是故障，也不是通过。
3. **正式实验能启动但 `delivery_eligible=false`** ⇒ 结果不可交付。
4. **冷库哈希仍是 5% 抽样**，不是逐字节全量；**catalog 声明范围 ≠ 全市场覆盖**。
5. **第 5 段永远只代表「扫描在跑」，不代表「策略有效」** —— 0 信号既可能是策略窄，也可能是市场没机会，这两者本机现在分不开。

---

## 13. 复现步骤

```bash
# 0. 前置：后端 8787、前端 3000 都在跑
#    python 框架/backend/service_manager.py restart --enable-paper --arm 3a --symbol all

# 1. 画布七段（真浏览器，不是接口拼凑）
node evidence/DB-PERF-01/probes/10_canvas_state.mjs
#    → out/canvas_state.json + out/canvas_full.png

# 2. 四项修复的机械复算（只读）
python evidence/DB-PERF-01/probes/40_fix_verify.py
#    → out/fix_verify.json

# 3. 数据库查询基线 / 接口耗时
python evidence/DB-PERF-01/probes/20_db_perf.py baseline
python evidence/DB-PERF-01/probes/30_api_perf.py

# 4. 重跑逐笔对账证据（改了那 16 个文件之一就必须重跑）
python evidence/CODEX-CLOSEOUT-01/probes/verify_ui_run.py \
  --run-id bt_20261023T162149Z_59f3c8fa --note "<改了什么、为什么改>"

# 5. 回归测试（必须先停服务，否则 worker 抢 jobs.sqlite3）
python 框架/backend/service_manager.py stop --enable-paper
cd 框架/backend && python -m pytest tests/ -q

# 6. 回滚本轮索引（如需）
python evidence/DB-PERF-01/probes/20_db_perf.py drop-index
```

