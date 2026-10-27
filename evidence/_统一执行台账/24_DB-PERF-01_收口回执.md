# 24 · DB-PERF-01 数据库架构与查询性能优化 · 收口回执

- 触发：用户「优化数据库架构和查询性能，还是没有通」+ 链路画布截图（7 段全非绿）
- 收口时刻：2026-10-24T02:50Z
- 构建标识：`dbperf01-20261024`
- 服务：API pid 41184 / Worker 41194 / Runtime 41198
- 交付：`evidence/DB-PERF-01/`（README + probes/10_canvas_state.mjs、20_db_perf.py、30_api_perf.py + out/）

## 一句话

「链路不通」的根因**不是数据库慢**，是两条接口在新路由表里缺失（404）+ 前端预取表漏了这两条路径。
修掉后画布 **1 通过/3 等待/3 未验证 → 5 通过/1 等待/1 未验证**。
数据库层另做 2 个索引 + 连接级 PRAGMA + 缓存策略改造，`/data/feeds` 4535ms → 2ms。

## 改了什么

| 文件 | 改动 | 性质 |
|---|---|---|
| `框架/backend/api/routes.py` | 补 `/data/cold-archive-status`、`/system-flow/paper-reconciliation` 两条 Route | 修缺陷（原 404） |
| `UI界面/web/public/system-flow.html` | `CHAIN_PATHS` 8 条 → 10 条；第 3 段显示分片存在性实检结果；BUILD_ID 更新 | 修缺陷 + 信息精确化 |
| `框架/backend/metrics_api.py` | `handle_cold_archive_status` 新增 `shard_existence`（逐个 stat，TTL 300s） | 新增证据，不放宽判定 |
| `框架/backend/datacenter_service.py` | `_connect_ro()` 统一 PRAGMA；feeds 改 stale-while-revalidate；oi 聚合拆三条 | 性能 |
| `框架/backend/adapter.py` | `_prewarm` 增加 feeds 预热 | 性能 |
| `evidence/CODEX-CLOSEOUT-01/out/ui_paper_reconciliation.json` | 用原始探针重跑，补 `generated_at_utc`/`scope_kind`/`source_hashes` | 证据补全 |
| DB `market_v2.sqlite3` | 新增 `ix_fund_hist_symbol_time`、`ix_oi_hist_symbol` | additive，可 DROP 回滚 |

## 实测（全部可复算）

- `funding_stale_groupby` 494.29 → 89.42 ms（↓82%，`USE TEMP B-TREE FOR GROUP BY` 消失）
- `funding_agg` 120.95 → 88.26 ms（↓27%）
- `oi_agg` 433 → 379 ms（三合一拆三条 + 窄索引）
- `/data/feeds` 启动首次 4535 → 2 ms；缓存过期 3772 → 2 ms
- 画布 13 个依赖接口热态合计 3790.9 → 638.1 ms
- 建索引耗时 1.2s + 9.17s；库 11,238 → 11,290 MB（+51.7 MB）

复算：`probes/20_db_perf.py baseline`、`probes/30_api_perf.py`、`probes/10_canvas_state.mjs`

## 画布七段

1 已验证可用 · 2 已验证可用 · 3 **未验证** · 4 已验证可用 · 5 **等待条件** · 6 已验证可用 · 7 已验证可用

- 第 3 段未验证：存在性已逐个 stat（12,551 声明 / 12,498 存在 / **53 缺失**，全属 `ALLUSDT`、`BTCDOMUSDT` 两个已排除标的）；**内容完整性未校验**（不读 Parquet、不验行数）。
- 第 5 段等待：526 标的全量在扫、水位不滞后、**当前 0 条可执行信号**。等待 ≠ 通过，也不是故障。

## 新发现的真实故障（未掩盖）

`EPICUSDT` 资金费率停更 **90.6 小时**（最后结算 2026-10-20 08:00Z），但**仍在正常交易**（盘口 2 分钟前更新，价 0.183709）。未加入「已下架白名单」—— 加进去等于把真故障说成正常。

## 测试

服务停止态 `pytest tests/ -q`：**15 failed / 364 passed / 79 skipped**。
15F = 2 已知留红（`frame_fit_01::T07`、`frame_fit_02::T06`，断言前提已不存在）+ 13 既有环境问题（`service_manager.py:118` 隔离路径校验、`worker_already_running`）。
**无关证明**：临时摘掉 `adapter._prewarm` 的 feeds 预热后单跑 4 个相关文件 → 仍 13F/10P；已恢复预热。

## 顺手清理

5 个残留进程占生产 `jobs.sqlite3` 致正式 Worker 起不来：pid 33724 / 76245 / 77544 / 55012（`adapter.py` 监听 49262/55691/56447/8799）+ 40679（`backtest_worker --owner service_manager_62782`）。只停命令行含本项目路径者。

## 待用户拍板

1. 冷库 53 个缺失分片要不要修 catalog（修了第 3 段才可能判绿）
2. `/data/coverage` 7.68 MB 要不要加 `?agg=1` 只返聚合（改接口契约 + 改前端）
3. `frame_fit_01::T07` / `frame_fit_02::T06` 两条断言怎么办（改断言 = 改验收契约）

另：`EPICUSDT` 是去修采集，还是确认它该停更？


---

# 24-补记 · 用户「修复问题」后：三件拍板 + 一个待确认事实，全部落地

- 补记时刻：2026-10-24T04:00Z
- 构建标识：`dbperf01c-20261024`（上一版 `dbperf01-20261024`）
- 服务：API pid 48536 / Worker 48539 / Runtime 48545
- 新证据：`evidence/DB-PERF-01/out/fix_verify.json`（由 `probes/40_fix_verify.py` 生成）

## 四件事的结果

| 事项 | 处理 | 结果 |
|---|---|---|
| 冷库 53 缺失分片 | 删 catalog 里的**过期声明**（`ALLUSDT` 14 + `BTCDOMUSDT` 39，均指数类合约，本就被排除在 526 外）；备份 `.bak-20261024T0333Z`（11,597,348 B） | 缺失 **0** |
| 冷库内容完整性 | 新增三层实检：全量 `os.stat` + 全量读 Parquet footer 行数 + 5% 均匀抽样 sha256 | 存在 12,498/12,498 · 行数 12,498/12,498（108,208,916 行）· 哈希 624/624（258 MB）· `content_integrity_verified=true` |
| `/data/coverage` 体积 | 加 `?agg=1`（默认不传参行为不变，additive） | 7,684,331 B → **2,335 B**（3,290.9 倍） |
| 两条断言 | 改写为守护新契约（同窗口 202 / 异窗口 409 且含「与登记身份不一致」）；清掉死代码 `execution_scope_mismatch()` | 两条**都通过** |
| `EPICUSDT` 资金费率 | 直查交易所确认 `status=SETTLING`、`nextFundingTime` 冻结 ⇒ 正常终止；带依据入 `KNOWN_SETTLING_SYMBOLS`（原变量名 `KNOWN_DELISTED_SYMBOLS` 改名，它描述的是「停止结算」不是「下架」） | 17 路数据源全 `normal` |

## 本轮额外发现并修掉的 5 个缺陷（会让画布说假话）

1. **`system-flow.html` 语法错误**（两个字符串字面量之间漏 `+`）⇒ 整页 JS 不执行，画布永远「正在读取」。
   **重要**：上一版报告第 4 节的「5 通过/1 等待/1 未验证」是在这次文案改动**之前**抓的；改完页面其实是坏的。本轮修好并重抓，旧数字不得再被引用为验收结论。
2. **探针假超时**：`waitForFunction(fn, {timeout})` 把 options 当 arg 传，实际一直用默认 30s ⇒ 改为 `(fn, null, {timeout})`。
3. **「刚启动还没跑第一轮」被判故障**：`last_cycle_ms` 为空时一律 `fail`。改为按运行时长分档（阈值内 ⇒ 等待，超过 ⇒ 故障）。
4. **状态文件属于已退出进程却说成「运行时 stopped」**：重启后新实例要先 import 重模块（约 20 秒）才写状态，窗口内读到上一实例的临终记录 ⇒ 每次重启假红。新增 `record_pid_alive` / `record_written_ms` / `record_age_ms`，死 pid 时 `status=stale_record`（原值留 `record_status_raw`）。
5. **`?agg=1` 读错字段**：累加 `cell["rows"]`（该字段不存在）⇒ `rows` 恒 0。改用 `expected` 与 `missing_within_observed_range`，并区分「键不存在」与「值为 NULL」。

## 最终画布（2026-10-24T03:57Z，真浏览器）

**6 段已验证可用 / 1 段等待条件 / 0 故障 / 0 未验证。**
结论栏原文：「全链无故障；有 1 段在等待条件 —— 5. 信号引擎 —— 等待不等于通过。」

逐笔对账 `verified_historical_sample` · 17/17 · 源文件哈希 16/16 全 match · run `bt_20261023T162149Z_59f3c8fa`。

**对账证据因源文件改动重算过 2 次**（本轮改了 `experiment_registry.py`、`unified_runtime.py`，都在那 16 个哈希文件里）。每次重算都写入 `reverify_note` 说明「改了哪个文件、为什么改、为何不影响对账结论」，接口与页面都会显示。**不写这句就等于用「哈希全 match」把漂移盖住。**

## 测试

服务停止态：**13 failed / 366 passed / 79 skipped**（上一版 15F/364P/79S）。
减少的 2 个 = 本轮改写的两条断言转绿。剩 13 个全是既有环境问题（`worker_already_running`、隔离路径校验缺项）。

## 仍然不通过（不要因为画布好看就当成通过）

1. 两套止损规则未裁决：模拟盘「成交价×0.95」vs 回测「Lr−0.1A」⇒ 仓位差约 3.8 倍、出场全分歧。
2. 自然行情 0 笔成交 ⇒ 真实挂钟自动成交未验证（等待，不是故障也不是通过）。
3. 正式实验能启动但 `delivery_eligible=false` ⇒ 结果不可交付。
4. 冷库哈希仍是 5% 抽样；catalog 声明范围 ≠ 全市场覆盖。
5. 第 5 段只代表「扫描在跑」，不代表「策略有效」。
