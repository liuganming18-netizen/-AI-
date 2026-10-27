# SYSTEM-RUNTIME-01-FIX · 全池信号引擎（526 标的）与「0 信号」真因

时间：2026-10-23（UTC 10:0x–10:3x）。范围：只读上游 + 本目录新增；未改任何冻结阈值、未改账本、未改撮合、未改闸门。

---

## 1. 用户的两个问题与结论

| 问题 | 结论 | 证据 |
|---|---|---|
| 之前查到的两个阻塞（扫描不限交易所/市场、信号缺 `role` 字段）修好了没有 | **修好了**，并在隔离环境逐项验证 | `verify_result.json` V1–V9 |
| 信号要扩大到 500 多个币 | **已扩大：526 个标的**（观察 528，排除 `ALLUSDT`、`BTCDOMUSDT`） | 页面「监控标的: 526 个」、`bootstrap.universe=526` |

**但是「信号区 0 笔」并不是没修好，而是被一个更深的缺陷挡着 —— 已定位并修掉：**

> 突破瞬间的 **OI 闸门**在「关闭 OI 过滤」（B 组）时**仍然拦单**。
> 全池 30 天历史里价格形态确实走完了 4 次突破，**4 次全部被 `OI_UNAVAILABLE(oi_empty)` 打死**，所以候选恒为 0。

---

## 2. 全池漏斗（修复前 → 修复后，同一份数据）

`diag_funnel_pool.py`，526 标的、各自全量历史（约 30 天）：

| 阶段 | 修复前 | 修复后 |
|---|---|---|
| 箱体 BOX | 3179 | 3182 |
| 扫低 SWEEP | 612 | 614 |
| 回踩 RETEST | 369 | 370 |
| 首次突破穿越 | 4（全部记 `OI_UNAVAILABLE`） | 4 |
| **候选 CANDIDATE** | **0** | **4** |
| 失效 INVALIDATED | 1562 | 1564 |
| 超时 EXPIRED | 1223 | 1232 |

淘汰理由分布（修复后）：
- `建档后7天内未发生扫低` 1204
- `跌破L-0.5A或连续2根收盘破L` 1005
- `跌破Lr-0.1A或连续2根收盘破L` 346 ← 回踩后最集中的淘汰点
- `跌破S-0.1A或连续2根收盘破L` 213
- `扫低后72小时内未发生缩量回踩` 21
- `回踩后7天内未发生突破` 6
- `事件总体观察超时(>14天)` 1

> 注意：`跌破 Lr-0.1A` 这条**不是缺陷**。冻结规则 §D 原文写着「回踩之后 low < Lr-0.1A 或连续两根 1h 收盘 < L，先判失效」，实现是忠实的。它解释了为什么回踩到突破的转化率只有 4/370 —— 这是策略本身的极窄设计，**不得为了出信号去放宽**。

---

## 3. 缺陷定位与修复

### 3.1 缺陷：B 组的 OI 闸门没有随 `enable_oi_filter` 关闭

`策略/WyckoffMaster/event_trend_v0_1/state_machine.py`，突破分支原代码：

```python
if oi_status not in ("ok", OI_STATUS_ASSUMED) or oi_growth is None:
    self._terminate_event(event, EventState.OI_UNAVAILABLE, ...)
    return None
```

这段是**无条件执行**的。而运行时 `RealSignalEngine` 构造状态机时传的是
`enable_oi_filter=False`（B 组），并且 `process_1h_bar` 传入的 OI 是**空数组**
（实时链路没有 OI 采集）⇒ `read_oi` 返回 `oi_empty` ⇒ 每一次突破都被判死。

冻结规则 §3.D 与 §7 的 A/B 定义写得很清楚：
> 「A：上述完整价格事件链 + OI 门槛。B：完全相同价格事件和执行规则，**仅关闭 OI 门槛**。」

即 OI 的「缺失即拒」与「增幅不足即拒」**都属于 OI 门槛本身**，B 组必须整段跳过。
原实现让 B 组继承了 A 组的 fail-closed，**A/B 两组退化成同一组**，与冻结规则自相矛盾。

### 3.2 修复（只改这一个语义，不动任何阈值）

把两道 OI 闸门整体收进 `if self.enable_oi_filter:`：

- **B 组**（`enable_oi_filter=False`）：跳过 OI 闸门，正常出候选；
  候选里 `pit_proven=False`、`pit_assumption_note="OI 不可用：oi_empty"`，
  状态迁移理由如实写「放量突破通过(B组未启用OI门槛，OI不可用：oi_empty)」，
  **不冒充「OI 确认通过」**。
- **A 组**（`enable_oi_filter=True`）：语义**完全不变**，缺 OI 仍然 fail-closed。

### 3.3 验证

| 验证 | 结果 |
|---|---|
| 合成序列（形态全中 + 量能达标）· B 组 | **出候选**，理由如实标注 B 组未启用 OI |
| 合成序列 · A 组 | **仍 `OI_UNAVAILABLE`**（fail-closed 未被破坏） |
| `pytest test_frame_pit_01.py + test_event_trend_closure.py` | **39 passed** |
| 全池 526 历史回放 | 候选 **0 → 4** |

---

## 4. 行情数据：补掉全池内部断档

采集器每轮补缺预算只覆盖 10 个合约，实测全池出现**中间断档**（不是尾巴落后）：

| 周期 | 修复前 | 补洞后 |
|---|---|---|
| 1h | 415 个标的停在 00:00、只有 113 个跟到 09:00，中间断 8 根 | 内部断档 **0**，写 3192 根 |
| 4h | 432 个标的停在 10-22 20:00，96 个跟到 04:00 | 内部断档 **0**，写 824 根 |

脚本 `backfill_pool_klines.py`：按 30 天窗口**逐桶对齐找洞**，取最早缺失桶为起点回补，
写库复用采集器 `_store_closed_bars`（已存在的 head 自动跳过，不覆盖既有行），
不造数、不补空桶、不写未收盘 bar。报告 `backfill_pool_report.json`。

> 断档为什么致命：`RealSignalEngine.process_market` 的 1h 游标是
> `start_1h = max(processed_1h_opens) + 1H`。洞若在游标**之后**才补上，
> 这些 bar 永远不会被处理 ⇒ 状态机在错误的 bar 上推进。
> 因此补洞后**必须重启运行时重新 bootstrap**（本轮已重启，PID 50390）。

---

## 5. 增量路径验证（关键：实盘等新 bar 会不会出信号）

历史回放走 `bootstrap()`，实盘走 `step()`，是**两条不同代码路径**，必须分开验。

`probe_step_advance.py`：取真实候选 BBUSDT（2026-10-11 07:00Z 决策，突破 bar 开盘 06:00Z），
把该标的 K 线截断到突破前一根导入**临时库**，然后：

1. `bootstrap()` 到突破前一刻 ⇒ 状态 = **RETEST**；
2. 追加**真实的突破那根 1h bar**；
3. 调 `step()` ⇒ **产出 1 个候选**（收盘 0.010308，量能 5.27×V20，与历史回放完全一致）。

结论：**新 bar 到达 → 增量推进 → 出信号，这条链路是通的。**

---

## 6. 修复后全池能出的 4 个候选（历史，按纪律不补开）

`list_candidates_pool.py` → `candidates_pool.json`：

| 决策时点 | 标的 | 突破收盘 | 量能/V20 | 箱宽 | ER |
|---|---|---|---|---|---|
| 2026-10-02 15:00Z | VELVETUSDT | 0.06737 | 6.21× | 3.78A | 0.064 |
| 2026-10-06 06:00Z | SUSDT | 0.04433 | 5.63× | 5.48A | 0.224 |
| 2026-10-10 11:00Z | WALUSDT | 0.04253 | 64.54× | 5.40A | 0.082 |
| 2026-10-11 07:00Z | BBUSDT | 0.010308 | 5.27× | 5.59A | 0.096 |

这 4 个只作为「引擎确实会出信号」的能力证明，**不写进当前信号区**（bootstrap 主动丢弃历史候选）。
页面上的「历史回放候选: 4 个（历史不补开）」即此数。

---

## 7. 页面实测（playwright，1600×1050）

`shot_daemon.mjs` → `page_fixed.png`：

```
执行守护 (Execution Daemon)   running
守护 PID: 50390        推进周期: 30 次
监控标的: 526 个 · 箱体中 359 / 扫低 3 / 回踩 13
历史回放候选: 4 个（历史不补开）
阻断原因: 新开仓已暂停
```

后端 `GET /api/v1/paper/execution_daemon/status?arm=3a`：
`runtime_identity={pid:50390, status:running, symbol:"all(526)"}`，
`signal_engine={universe:526, universe_declared:526, state_counter:{BOX:359,IDLE:151,SWEEP:3,RETEST:13}}`，
`bootstrap={universe:526, ready:526, skipped:0, history_candidates_discarded:4, elapsed_sec:17.0}`。

---

## 8. 结论（三态口径）

| 事项 | 判定 |
|---|---|
| 三个旧阻塞（扫描限定 / `role` 落库 / 暂停期零副作用） | **通过**（隔离验证 V1–V9） |
| 全池 526 标的接入 | **通过**（ready 526 / skipped 0 / 页面可见） |
| 行情内部断档 | **通过**（1h、4h 内部断档均 0） |
| OI 闸门阻断 B 组突破 | **已修复并验证**（A 组 fail-closed 未变，39 项测试通过） |
| 增量推进（新 bar → 候选） | **通过**（真实数据端到端） |
| **「现在就有实时信号」** | **不成立** —— 当前没有新的突破，信号区如实为 0 笔 |
| 本策略的信号频率 | 全池约 **4 个/30 天**，这是冻结参数下的真实能力，**不是** bug |

> 系统整体仍**不通过**（历史结论不变）；本文件只声明上述 5 项在本轮证据范围内成立。

---

## 9. 本轮改动文件与指纹（SHA-256 前 16 位）

| 文件 | SHA-256 | 字节 |
|---|---|---|
| `框架/backend/unified_runtime.py` | `34ec7b264dbb099d` | 58702 |
| `框架/backend/service_manager.py` | `64166903d379dc55` | 50945 |
| `框架/backend/live_api.py` | `f6846cc605892061` | 109606 |
| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | `40ae81e2cdb0ed10` | 31658 |
| `UI界面/web/src/api/liveTrading.ts` | `c084dfb9cd3399c7` | 22785 |
| `UI界面/web/src/components/PositionPanel.tsx` | `defe3856e80c838d` | 60005 |
| `evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` | `1f1167de35412cb7` | 13315（**未改**） |

`state_machine.py` 改动前 `af50e4ec6aafafb2` / 30215B → 改动后 `40ae81e2cdb0ed10` / 31658B。
**冻结规则 md 的 SHA 与字节数未变**（`1f1167de…` / 13315B），冻结阈值一个没动。

---

## 10. 新增未处理风险

1. **采集器补缺预算仍是每轮 10 个合约**：本轮是一次性补洞，长期仍会再断。
   要么调大预算，要么把本脚本挂成定时任务（建议后者，成本更低且不动采集器）。
2. **实时链路没有 OI 采集**：B 组候选的 OI 字段恒为空，`pit_proven=False`。
   若将来要跑 A 组，必须先接 OI 采集并证明发布时点。
3. **「最近推进」显示 `—`**：后端未在状态文件里写 `last_step_utc`，属展示瑕疵，未修。
4. **`/api/v1/signals?scope=current` 空库时 `as_of` 回落到 `1970-01-01T00:00:00Z`**，未修。
