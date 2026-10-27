# SYSTEM-RUNTIME-01-FIX｜信号扫描限定、事件角色落库、暂停后重试

时间：2026-10-23 17:2x　｜　只改 2 个生产文件，其余全部是 `evidence/SYSTEM-RUNTIME-01-FIX/` 内新增。

## 一、两个阻塞的真实位置

### 阻塞 1：信号扫描没有限定交易所和市场

| 位置 | 原写法 | 后果 |
|---|---|---|
| `unified_runtime.py` `route_pending_orders` | `SELECT * FROM live_signal WHERE strategy_key=? AND executable_state='enterable'` | 只按臂名扫**全库**：其它交易所（okx/swap）、其它市场（spot）、以及结构证据提示行都会被当成可开仓信号去路由；臂名还误写在 `strategy_key` 列上（该列语义是策略键） |
| `unified_runtime.py` `MarketDataWatcher.sync_dataset_bars_upto` | `SELECT * FROM candle_dataset WHERE close_time<=? AND is_activated=0` | 数据集里只要有多个标的，任意一个推进时钟就把**别的标的**的 K 线一起激活（串数据 + 全表扫） |

### 阻塞 2：生成的信号缺少界面要求的事件角色字段

真实机制比「没写 role」更隐蔽，是**两条**：

1. **冲突时整行丢弃**：`persist_candidates` 用 `INSERT OR IGNORE`。同一 `signal_uid` 若此前由旧路径写入过（`payload` 无 `role`），新写入被静默忽略 ⇒ 旧行永远补不上角色 ⇒ 界面 `isCurrentSignal`（`role === 'enterable_arm_entry'`）判否 ⇒ **信号在库里有、页面上没有**。
2. **接口整体失败被谎报**：`live_api.live_signals` 在 `scope=current` 会 ATTACH 四臂账本并按 `sig_x.paper_order` 排除终态拒单。账本目录存在但库是空壳（隔离新建 / 迁移中断 / 只读挂载）时 ATTACH 成功、表不存在 ⇒ 整条 SELECT 抛 `no such table` ⇒ 被 `except sqlite3.OperationalError` 当成「信号库尚未初始化」⇒ **信号区整体空白**，且错误信息指向错误原因。

第 2 条是本窗口实测复现的（不是读代码推断）：修复前同一份数据返回
`total=null, missing_reason="实时信号库尚未初始化（请先运行 live worker）"`。

## 二、改了什么

### `框架/backend/unified_runtime.py`（`a4972a2a…fbf4`，45,738B）

1. 新增身份常量 `EXCHANGE / MARKET / TIMEFRAME / STRATEGY_KEY / ARM_ENTRY_ROLE / TERMINAL_REJECT_REASONS`，读写两侧共用，不再各处硬编码。
2. 新增 `_scan_actionable()`：扫描限定 **交易所 + 市场 + 周期 + `payload.role=enterable_arm_entry` + `payload.arm` + 时间字段齐备**，可选按 `origin` 限定（日常 `live` / 回放 `replay` 不互串）。
3. `persist_candidates`：`INSERT OR IGNORE` → `ON CONFLICT(signal_uid) DO UPDATE`，冲突时补写 `payload_json`（角色就此补齐），**不动** `first_seen_at`、事件时间与价格事实；返回 `{new, updated, total_written}`。
4. `route_pending_orders`：
   - 闸门暂停时**不写任何状态**（不写 `paper_processed` / `paper_order` / 不登记挂单），只如实返回 `waiting_gate` 条数；账本不可读时给 `None`，不伪造 0。
   - 拒单**只有终态**（`missed_fill_moment_no_backfill` / `before_paper_epoch_no_backfill`）才写 `paper_processed`；其余（仓位已满 / 参数缺失 / 行情库不可用 / 闸门暂停）保持**可重试**——原实现把所有拒单都写 `paper_processed`，等于把可重试信号静默判死。
5. `sync_dataset_bars_upto`：限定交易所 / 市场 / 标的。
6. `get_full_status().order_processing` 新增 `orders_waiting_gate` 与 `new_entry_gate`；暂停时状态显示 `paused_new_entry_gate`，不再表现为「ready / 本轮 0 条」。

### `框架/backend/live_api.py`（`f6846cc6…d4be`，109,606B）

ATTACH 后校验 `sig_x.sqlite_master` 里 `paper_order` 是否存在，缺表则 DETACH 且该臂不参与终态排除（**如实降级**，与既有注释声明的降级路径一致），避免「账本空壳 ⇒ 信号区整体空白 + 错误原因错报」。

## 三、验证（隔离环境，生产库零写入）

脚本：`probe_fix_verify.py`（V1–V9）、`probe_api_degraded.py`（降级）。结果：`verify_result.json`。
全部使用临时 `LIVE_DIR / PAPER_LEDGER_DIR / MARKET_DB / PAPER_GATE_FILE`。

| 项 | 结论 | 关键读数 |
|---|---|---|
| V1 落库与可见 | PASS | 写入 1 条；接口 `total=1`；`role=enterable_arm_entry`、`arm=3a`、`strategy_key=wyckoff_v0_1` |
| V2 旧行补角色 | PASS | 旧行 payload 无 role 时界面只看到 1 条；重新落库后 `updated=1`、`payload.role` 补上、界面变 2 条 |
| V3 扫描限定 | PASS | 混入 okx/swap 与 binance/spot 各一条可执行行，扫描结果**不含**它们（`cross_venue_excluded=true`） |
| V4 暂停期 | PASS | `waiting_gate=2`；账本 `pending/processed/orders/blocked` **全 0**（无副作用） |
| V5 恢复后 | PASS | 同一批 2 条全部登记为挂单；`processed=0` |
| V6 重复轮 | PASS | 不重复登记（`processed=0`，挂单仍 2） |
| V7 可重试拒单 | PASS | 行情库不可用 → `rejected(market_db_unavailable)`，**`paper_processed` 无行** |
| V8 恢复后重试成功 | PASS | V7 被拒的 ETH 信号在下一轮成功登记；挂单 `earliest_fill_ms` 在**未来**（不补旧价） |
| V9 终态拒单 | PASS | 注入 `missed_fill_moment_no_backfill` → `terminal=1`，`paper_processed.action=rejected_terminal` |
| 降级（空壳账本） | PASS | `total=1, missing_reason=null, quality=ok`（修复前为 `total=null` + 谎报未初始化） |

## 四、边界与未验证

- **未启动后端、未开浏览器**：以上全部为进程内隔离调用，不是页面端到端。
- **未接真实行情回放**：`open_position` 一律返回 `pending_future_fill`（临时行情库无 15m 序列），成交与退出链路本轮**未验证**。
- **`route_pending_orders` 的 `direction="long"` 仍是硬编码**（v3.1 只多，未改）；若后续开放空头需同步改。
- **`sync_dataset_bars_upto` 只用于测试数据集注入**，日常实例不依赖它。
- 生产 `live/live_signals.sqlite3` 当前 0 行；本轮**未向其写入**，也**未清理**任何历史行。
- 修复只保证「角色与扫描口径一致、暂停不拉黑」；**不构成**系统整体通过，也不改变 `WX-GATE-v2.0` / `WX_EVENT_TREND_V0_1` 的既有判定。

---

## 五、端到端复查（17:47–17:55，真机）

### 5.1 运行时此前根本没在跑

`locks/paper_runtime_3a_status.json` 停在 17:07（`status=stopped`，PID 43008 已死），
`live_signal` 生产库 **0 行**。启动脚本默认 `ENABLE_PAPER=0`（普通 UI/API 安全模式），
所以「页面空」的第一层原因是**交易链没启动**，不是接口或页面问题。

已用 `spawn_detached.py` 按 service_manager 同款参数拉起：PID **46706**，
`runtime_identity.status=running`、`cycle_count` 持续增长、`active_state=BOX`。

### 5.2 页面「守护 PID: — / 推进周期: 0 次」是前端读错字段

后端 `/api/v1/paper/execution_daemon/status` 一直正确返回
`item.runtime_identity.pid / .cycle_count`；前端 `liveTrading.ts` 只读
`item.pid` 与 `item.execution_daemon.pid` ⇒ 恒为 null/0。已补 `runtime_identity.*` 回退。
复查截图：右栏显示 **守护 PID 46706 / 推进周期 128 次 / running**。

### 5.3 行情缺口（真实数据补齐）

BTCUSDT 的 1h 停在 `10-23 00:59Z`、4h 停在 `10-22 23:59Z`（落后 8–9 小时），
采集器补缺预算每轮只覆盖 10 个合约 ⇒ 单标的长期落后。用 `backfill_btc_klines.py`
走币安公开 REST 补齐：1h 写入 8 根、4h 写入 2 根（复用 `_store_closed_bars`，先查 head 已存在即跳过）。
补齐后 1h 最新 `08:00–09:00Z`、4h 最新 `04:00–08:00Z`。

### 5.4 信号为 0 是策略特性，不是链路故障（全池实测）

`diag_pool_scan.py` 用真实行情把 **528 个标的**各自 16 天历史喂给状态机：

| 指标 | 实测 |
|---|---|
| 扫描标的 | 528（16.4 秒） |
| 历史产出候选 | **0** |
| 当前状态分布 | BOX 379 / IDLE 134 / RETEST 12 / SWEEP 3 |

即：引擎在真实数据上确实在推进（有 12 个回踩、3 个扫低），但**没有一次走到放量突破**，
所以 `CandidateSnapshot` 一个都没有。BTCUSDT 单标的同理（`final_state=BOX`）。
页面「暂无实时信号」是**真实空态**，不是被过滤掉。

### 5.5 本轮新增风险（未处理）

- **1h/4h 数据会再次滞后**：采集器预算优先补缺口最大的合约，BTCUSDT 会被反复挤到后面；
  运行时读到不新的 bar 就只会空转。需要定时跑 `backfill_btc_klines.py` 或改采集优先级。
- **运行时只盯 BTCUSDT 一个标的**：全池 528 个里 379 个正在箱体内，但当前架构只驱动一个。
- `/api/v1/signals?scope=current` 空库时 `as_of` 返回 `1970-01-01T00:00:00Z`（`max([])` 兜底 0），
  属展示瑕疵，不影响判定。
