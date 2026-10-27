# PAPER-LIVE-01 模拟实盘持续运行与全池因果闭环验收报告

## 一、工单目标与基线核验

根据工单要求：
1. **统一启动入口、PID 记录与实际进程**：杜绝启动脚本恢复成 BTC 单币或重复启动运行时引发锁冲突；
2. **核对全池新闭合 K 线增量扫描**：526 标的增量推进，缺数据、扫描失败必须详细暴露具体原因；
3. **信号与交易执行贯穿**：新信号必须完整落入统一信号库、路由挂单、撮合成交、跟踪止损维护与退出账本；
4. **严格执行冻结配置**：不改信号阈值，不补开历史候选，不把研究参数矩阵变成自动选参；
5. **自然市场真实记录与隔离测试闭环**：日常无新突破时如实记录为 0；独立沙盒验证交易闭环，生产库 0 写入；
6. **热重启恢复与健康守护**：隔离验证断点恢复，不停止健康采集器进程。

---

## 二、生产环境真实运行状态与 PID 矩阵

| 服务名称 | 进程名 / 模块 | 真实 PID | 监听端口 / 作用域 | 身份校验 (Identity Matched) | 运行状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **API 适配器** | `adapter.py` | `42250` | `8787` (HTTP 200) | `True` | `running` |
| **回测 Worker** | `backtest_worker.py` | `42256` | 任务队列守护 | `True` | `running` |
| **实时行情采集器** | `realtime_collector.py` | `42278` | 行情与 funding 采集 | `True` | `running` (心跳健康) |
| **交易运行时** | `unified_runtime.py` | `50390` | Arm: `3a`, Symbol: `all(526)` | `True` | `running` (1400+ cycles) |

> **关键修复实据**：此前 `service_manager.py` PID 文件记录已失效的 `43008`，导致 status 误报 `identity_matched: false` 且无法受控停止；现已增加从 `paper_runtime_3a_status.json` 及独占锁文件自动发现、双向核实与 PID 同步机制，真实 PID `50390` 现已被完全感知与受控管理。

---

## 三、6 项任务逐项落实与技术整改

### 1. 统一启动入口与参数修复（避免恢复成 BTC 单币与重复启动）
- **`start.sh` 修复**：修改 L26 默认值 `SYMBOL="${SYMBOL:-all}"`（此前为 `BTCUSDT`），杜绝桌面快捷启动将全池 526 币回退为 BTC 单币；
- **`启动系统.command` 修复**：支持并透传 `SYS_SYMBOL="${SYS_SYMBOL:-all}"`，确保桌面启动系统原生运行全池扫描；
- **防重复启动与互斥锁**：`service_manager.py` 在 `start()` 时主动调用 `_find_active_runtime_pid()`，若已存在同 arm 存活进程则自动复用，不再盲目拉起新进程触发排他锁冲突；在 `stop()` 时精准停止核实后的进程，绝不误杀外部进程或采集器。

### 2. 全池 526 币增量扫描与缺数据原因暴露
- **`MultiSymbolSignalEngine` 改造**：
  - 新增 `self.skipped_reasons: Dict[str, str]`，详细记录标的跳过原因（例如 `4h_bars_insufficient(12<50)` 或 `1h_bars_insufficient(60<200)`），绝不静默吞掉；
  - 新增 `self.scan_errors: Dict[str, dict]`，捕获各标的单步推进异常；
- **状态暴露透明化**：
  - 在 `get_full_status()` 中直接对外暴露 `watermark_1h`、`watermark_4h`、`skipped_reasons`、`scan_errors`、`scan_errors_total`；
  - 当前生产池状态：`universe: 526`，`ready: 526`，`skipped: 0`，全量标的均具备完整 4h/1h K 线历史。

### 3. 信号、路由、挂单、撮合、止损与退出全链路贯穿
- **契约贯穿**：信号由 `CandidateSnapshot` 生成不可变快照，落入 `live_signal` 表（`executable_state='enterable'`）；
- **路由挂单**：`SignalPersistenceAndRouter` 按交易所/市场/周期/事件角色四重匹配，登记入 `paper_pending` 挂单表；
- **撮合成交**：`paper_engine.advance_pending` 遵循因果防线，根据 `earliest_fill_ms` 之后首根闭合 15m bar 开盘价模拟撮合成交，开仓落入 `paper_position` 表；
- **跟踪止损**：`UnifiedExecutionDaemon` 依据 FT-LOW4H 规则（`low_4h * (1 - trail_d)`）单调抬高止损，轨迹持久化至 `paper_stop_history` 表；
- **退出平仓**：明细 15m 价格击穿止损线后立即触发市价平仓，落入 `paper_realized` 归档并清空活动持仓。

### 4. 严格执行冻结配置与历史候选丢弃
- 严格遵循 `FT-LOW4H-EXP-V2.2` 冻结参数（ATR 周期、15m 执行延迟、0.0005 滑点手续费等）；
- 启动时预热产生的历史候选订单严格执行 `history_candidates_discarded` 统计并丢弃（生产环境丢弃 4 笔历史候选），绝不向后补开历史事件。

### 5. 自然市场真实记录与隔离测试闭环（严格区分）
- **自然市场**：当前自然市场 526 标的未产生符合全部因果过滤的新突破，`signals_generated_total: 0`，`orders_routed_total: 0`，如实反映客观市场真实状态，严禁无信号时凭空造单；
- **开仓闸门状态**：服务端开仓总闸已开启（`allow_new_entries: true`）；
- **隔离测试验证**：在独立沙盒内注入合成 K 线完整跑通“突破 -> 挂单 -> 成交 -> 止损抬高 -> 止损退出”全闭环（详见 `isolation_trade_closure_evidence.json`）；
- **生产环境 0 污染断言**：测试期间日常生产库 `market_v2.sqlite3`、`live_signals.sqlite3`、`数据/paper/` 修改时间与记录数保持 0 变化。

### 6. 热重启接管与持续健康
- **热重启恢复**：在账本存在活跃持仓时重启 UnifiedRuntime，能无缝接管已有持仓，不重复建仓，保持止损监控；
- **持续运行保障**：实时采集器 PID `42278` 持续存活并保持心跳探针正常，数据链路不断流。

---

## 四、自动化测试套件验收结果

运行专项验收测试 `框架/backend/tests/test_paper_live_01.py`：
```text
test_01_universe_and_watermark_exposure (__main__.TestPaperLive01.test_01_universe_and_watermark_exposure)
测试全池标的启动、增量扫描与缺数据原因详细暴露。 ... ok
test_02_lock_concurrency_and_pid_auto_sync (__main__.TestPaperLive01.test_02_lock_concurrency_and_pid_auto_sync)
测试同 arm 互斥独占锁及 ServiceManager 从 status.json 自动同步存活 PID。 ... ok
test_03_isolated_end_to_end_trade_closure (__main__.TestPaperLive01.test_03_isolated_end_to_end_trade_closure)
在独立沙盒内验证完整的交易闭环：突破 -> 信号 -> 挂单 -> 撮合 -> 止损跟踪 -> 平仓。 ... ok
test_04_restart_and_recovery (__main__.TestPaperLive01.test_04_restart_and_recovery)
测试持仓中接管与热重启恢复：不漏单、不重复开仓。 ... ok
test_05_production_read_only_status (__main__.TestPaperLive01.test_05_production_read_only_status)
检查日常生产环境 526 币扫描持续运行状态与指标。 ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.608s

OK
```

---

## 五、交付证据文件索引

- **验收测试日志**：`evidence/PAPER-LIVE-01/test_execution.log`
- **实盘服务状态快照**：`evidence/PAPER-LIVE-01/service_status_live.json`
- **隔离闭环交易数据结构**：`evidence/PAPER-LIVE-01/isolation_trade_closure_evidence.json`
- **源码 SHA256 哈希清单**：`evidence/PAPER-LIVE-01/source_hashes.txt`
