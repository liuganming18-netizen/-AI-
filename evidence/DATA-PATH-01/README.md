# DATA-PATH-01 数据读取与账本性能优化验收报告

## 一、工单目标与问题根因剖析

### 1. 账本 23 秒延迟的根因诊断
在未整改前，账本接口（`GET /api/v1/paper/accounts` 及 `/api/v1/paper/positions`）在有活跃持仓时耗时高达 **23 秒以上**，排查发现存在以下系统性根因：
1. **函数重复定义与无效覆盖**：`paper_engine.py` 内部存在两个同名函数 `last_closed_15m()`。后方（约 2193 行）的定义直接覆盖了前方（约 360 行）定义；
2. **复合索引前缀列遗漏引发 845 万行全表扫描**：后方 `last_closed_15m()` 与 `last_closed_4h()` 直接对 `candle_versions` 表执行 `WHERE symbol=? AND timeframe=? ORDER BY close_time DESC LIMIT 1`。
   - `candle_versions` 包含 **8,459,047** 行数据；
   - 其物理主键复合索引为 `(exchange, market, symbol, timeframe, open_time, source_id)`；
   - 因**遗漏了 `exchange` 和 `market`**，SQLite 无法使用主键前缀列，被迫退化为 **`SCAN candle_versions`（全表扫描 845 万行）+ `USE TEMP B-TREE FOR ORDER BY`**！单次查询耗时高达 **8.62 秒**；
3. **持仓估值多次放大全表扫描**：在生成四臂账本快照时，`equity()` 遍历持仓估值触发一次查询，`snapshot()` 又再次遍历持仓估值触发第二次查询，若包含 1~3 笔持仓，累计延迟即达到 `8.6s * 3 ≈ 25.8s`；
4. **统一运行时守护循环阻塞**：`unified_runtime.py`（L775）与 `paper_loop.py`（L196）中检查 15m 退出条件的 SQL 同样遗漏 `exchange` 和 `market`，导致后台持仓巡检循环同样被全表扫描卡死；
5. **Row 对象缺少属性访问**：`equity()` 计算未实现风险时误调用 `sqlite3.Row.get("stop_price")` 抛出 AttributeError，导致非空持仓估值异常中断。

---

## 二、整改方案与执行计划对比

### 1. 优先复用现有查询与索引（零新增索引，零锁等待）
- **复用 `candles` 视图与主键复合索引**：
  统一查询显式限定 `exchange='binance' AND market='um' AND symbol=? AND timeframe=? AND is_closed=1` 并按 `open_time DESC LIMIT 1`（若指定 `upto_ms` 则追加 `close_time<=?`）。
- **执行计划对比实据**：

| 查询阶段 | 执行计划（EXPLAIN QUERY PLAN） | 单次耗时 | 扫描行数 | 性能提升 |
| :--- | :--- | :--- | :--- | :--- |
| **整改前** | `SCAN candle_versions` + `USE TEMP B-TREE FOR ORDER BY` | **8,626.25 ms** | 8,459,047 行全扫 | 基线（瓶颈） |
| **整改后** | `SEARCH v USING INDEX sqlite_autoindex_candle_versions_1 (exchange=? AND market=? AND symbol=? AND timeframe=?)`<br>`SEARCH h USING PRIMARY KEY (...)` | **0.106 ms** | 1~2 行索引点查 | **快 81,379 倍** |

- **索引与锁等待评估**：
  本次优化**无需创建任何新的 SQLite 索引**（`new_indexes_created: 0`），无需对生产行情库加写锁（`lock_wait_seconds: 0.0`），数据表结构保持 0 改变，无需回退。

### 2. 代码涉及修改清单
- [`框架/backend/paper_engine.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/paper_engine.py)：
  - 统一定义并优化 `last_closed_15m` 与 `last_closed_4h`，限定 `exchange='binance' AND market='um'`，优先查 `candles` 视图（带 fallback）；
  - 修复 `equity()` 中 `p.get("stop_price")` 的 Row 兼容性；
- [`框架/backend/unified_runtime.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/unified_runtime.py)：
  - L775 优化 15m 退出检查查询，限定交易所与市场，避免守护进程全表扫描；
- [`框架/backend/paper_loop.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/paper_loop.py)：
  - L196 优化 15m 退出检查查询；
- [`框架/backend/live_api.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live_api.py)：
  - L1129 优化外部 4h K 线核验查询，限定交易所与市场；
- [`框架/backend/tests/test_data_path_01.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/框架/backend/tests/test_data_path_01.py)：
  - 新增专项验收测试套件。

---

## 三、基准性能测定结果（连续 10 次请求测定，目标 P95 ≤ 2.0s）

### 1. 独立沙盒有持仓态快照与 API 基准（注入 BTCUSDT, ETHUSDT, SOLUSDT 3 笔活跃仓位）
使用生产环境真实 845 万行行情库（只读模式）：

| 请求序号 | 四臂快照 `snapshot_all()` 耗时 | 账本接口 `live.paper_accounts()` 耗时 | 独立 HTTP 端口端到端耗时 |
| :---: | :---: | :---: | :---: |
| Req #01 | 21.094 ms | 26.004 ms | 20.663 ms |
| Req #02 | 25.216 ms | 42.126 ms | 28.451 ms |
| Req #03 | 28.543 ms | 20.806 ms | 38.897 ms |
| Req #04 | 16.153 ms | 24.699 ms | 23.642 ms |
| Req #05 | 14.033 ms | 21.667 ms | 23.762 ms |
| Req #06 | 17.032 ms | 40.231 ms | 27.970 ms |
| Req #07 | 21.113 ms | 25.601 ms | 38.759 ms |
| Req #08 | 33.428 ms | 28.336 ms | 26.236 ms |
| Req #09 | 17.847 ms | 22.920 ms | 33.872 ms |
| Req #10 | 17.506 ms | 31.974 ms | 27.478 ms |
| **Min** | **14.033 ms** | **20.806 ms** | **20.663 ms** |
| **Max** | **33.428 ms** | **42.126 ms** | **38.897 ms** |
| **Avg** | **21.197 ms** | **28.436 ms** | **28.973 ms** |
| **P50** | **21.094 ms** | **25.802 ms** | **27.724 ms** |
| **P95** | **33.428 ms** | **42.126 ms** | **38.897 ms** |
| **验收指标** | **≤ 2000.0 ms** (通过) | **≤ 2000.0 ms** (通过) | **≤ 2000.0 ms** (通过) |

> **实测结论**：在真实 845 万行行情库与 3 笔活跃持仓下，端到端 HTTP P95 为 **38.897 ms**，较目标（2,000 ms）快了 **51 倍**，彻底消除 23 秒卡死问题。

### 2. 生产现网接口实测（端口 8787，`/api/v1/paper/accounts`）
- 10 次请求耗时：`44.0ms, 53.6ms, 49.2ms, 39.1ms, 68.1ms, 44.2ms, 66.0ms, 52.1ms, 55.9ms, 50.9ms`
- **现网 HTTP P95**：**68.099 ms**（响应状态码恒为 200，完整返回 30,891 字节）。

---

## 四、数据链状态所需事实与可用性核验

通过复用既有 `/api/v1/health`、`/api/v1/data/status`、`/api/v1/paper/execution_daemon/status` 接口，当前数据链状态事实如下：

1. **最新可用时间与数据年龄**：
   - 行情库 15m 闭合时间：`1792763099999`（入库时间 `1792763100472`）；
   - 行情库 4h 闭合时间：`1792756799999`；
   - 行情库 1h 闭合时间：`1792760399999`；
   - 采集器实时报价最新事件：`0s` 前（心跳 2s 前，`fresh=True, valid=True`）。
2. **数据缺口与覆盖范围**：
   - 全池 526 标的全部通过预热（`universe: 526, ready: 526, skipped: 0`）；
   - 无任何标的因 K 线不足而被跳过，无缺口阻塞。
3. **读取来源分层**：
   - 官方归档层：经 `batch_download.py` 严格校验 CHECKSUM 载入；
   - 历史落库层：`market_v2.sqlite3` 视图 `candles`；
   - 实时增量层：`realtime_collector.py` WebSocket 实时流入库。
4. **错误统计与阻塞原因**：
   - `skipped_reasons: {}`（0 项）；
   - `scan_errors_total: 0`；
   - 开仓总闸 `allow_new_entries: true`；
   - 此前账本读取阻塞的唯一根因是 SQL 缺少前缀列导致全表扫描，现已彻底修复。

---

## 五、持续运行守护与生产零污染说明

1. **健康采集器持续运行（零中断）**：
   - 实时行情采集器 PID `42278` 持续存活，累计运行平稳，探针心跳 `healthy`；
2. **生产数据零污染**：
   - 测试均在独立临时沙盒进行，测试完成后断言生产库 `live_signals.sqlite3` 与 `数据/paper/` 记录数未受任何污染。
3. **回归验证全绿**：
   - `test_data_path_01.py`：3 passed
   - `test_paper_live_01.py`：5 passed
   - `test_system_isolation_05.py`：6 passed

---

## 六、证据文件索引

- **测试执行完整日志**：[`evidence/DATA-PATH-01/test_execution.log`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/DATA-PATH-01/test_execution.log)
- **基准测试数据与执行计划对比**：[`evidence/DATA-PATH-01/benchmark_results.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/DATA-PATH-01/benchmark_results.json)
- **修改源码 SHA256 哈希清单**：[`evidence/DATA-PATH-01/source_hashes.txt`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/DATA-PATH-01/source_hashes.txt)
