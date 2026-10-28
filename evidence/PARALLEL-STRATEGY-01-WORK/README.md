# PARALLEL-STRATEGY-01-WORK｜现实时间模拟撮合

> 生成时间：2026-10-28（北京时间）｜执行方：Work（本单独占调用段修改与本目录）
> 反重力只读复核在 `evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/`，各写各自目录，未交叉。
> **本目录只记录本单做了什么、证据在哪、哪些还没证明。不做验收结论**——验收由主代理独立进行。

---

## 0. 一句话说清改了什么

改动前：策略说「12:00 确认 → 最早 12:15 执行」，但代码必须等 12:15 那根 K 线**收盘（12:30）**
才取得到它的开盘价，然后把这笔成交记成「12:15 成交」。也就是说**执行动作从来没在 12:15 发生过**，
账本上的时间比真实处理时间早了 15~30 分钟。

改动后：12:15 那根 K 线**刚开盘**就从采集器实时写入的行情表里读它的开盘价并成交，
账本记 `reality_fill=1`，同时记下这个价格是什么时候真正拿到的（`data_available_at`）。
如果 12:15 已经过去、那根 K 线已被下一根覆盖 ⇒ **拒单**，既不用收盘价冒充当时成交，
也不顺延到 12:30 那根。

**价格口径一个字都没改**：成交价仍然是「执行点那根 15 分钟 K 线的开盘价」（已批准的
`market_at_eligible_detail_open`）。改的只是**什么时候能拿到这个价**。

---

## 1. 四层分别报告

工单要求把四件事分开说，不能混成一句「通了」。

| 层 | 结论 | 证据 |
|---|---|---|
| **① 源码修复** | 已改，三份最小 diff 全部有可核对基线 | 本文 §2；`out/07_changeset.json`、`out/07_*.diff` |
| **② 隔离行为** | 10/10 通过；边界实测**执行点后 6.5 秒成交**（非 15 分钟）；零回归 | 本文 §4、§7；`out/02_isolation.json`、`out/03_boundary_fill.json`、`out/04_regression_parity.json` |
| **③ 运行实例已加载** | 已重载，内容级证据成立（不是靠 pid/哈希自报） | 本文 §5；`out/05_reload.json` |
| **④ 自然扫描/候选/订单/成交** | **全部为 0，如实记零** | 本文 §6；`out/06_natural_observe.json` |

④ 是零不等于 ③ 没做对。这两件事必须分开看：新代码确实在跑（③），但市场这段时间没给出
符合策略的候选（④），所以没有自然成交。**任何隔离注入、测试探针都不算自然成交。**

---

## 2. 改了什么（变更清单）

### 2.1 三个文件、两种改动性质

| 文件 | 改动前 sha256 | 改动后 sha256 | 行数变化 | diff |
|---|---|---|---|---|
| `框架/backend/paper_engine.py` | `a99f8af0…a52d1` | `bae9d934…17a5e` | 2882 → 3130 行<br>166,937 → 183,116 B | +259 / −11<br>`out/07_paper_engine.diff` |
| `框架/backend/live_store.py` | `02eaf41b…f1ca6` | `ba85a66d…120d6` | +16 / −0 | `out/07_live_store.diff` |
| `框架/backend/live_api.py` | `585c9727…d07a` | `94302c96…7fa2` | +10 / −5 | `out/07_live_api.diff` |

**没有改的（已逐个核对哈希未变）**：

- `框架/backend/unified_runtime.py` — `e30e2135…95470`（本单只在 `WRITE_TARGETS` 里登记，实际一个字没动）
- `策略/WyckoffMaster/event_trend_v0_1/config.py` — `122683f2…0eedeb46`（**策略参数/阈值/确认条件/OI 门槛/风险参数全部未动**）
- 生效视图 `10_研究参数生效视图_v2_2_user5000_shared.json` — `82dec362c182…`
- 闸门 `paper_new_entry_gate.json` — `f4691db2c7f66c81…`

### 2.2 基线可信度（这一节是为了防止「拿别人的改动当自己的成绩」）

三个 diff 的基线不是随手取的：

- **`live_store.py`**：git HEAD 的 blob 哈希 **等于** 本单 `00_freeze_before` 记录的 `02eaf41b…`
  ⇒ `git diff HEAD` 就是本单改动，无夹带。
- **`live_api.py`**：这个文件在**我动手之前就已经带着其他工单的未提交改动**
  （git HEAD 是 `7175dae6…`，不等于冻结记录 `585c9727…`）⇒ `git diff HEAD` 会把别人的改动混进来，
  **不能用**。做法：把工作区文件里我自己加的那一处逐字回退，重建基线；
  重建结果的 sha256 **正好等于** `585c9727…` ⇒ 证明重建基线就是改动前的真实内容，
  那 +10/−5 只含我这一处。
- **`paper_engine.py`**：基线用反重力快照 `evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/snapshot/paper_engine.py`，
  哈希 `a99f8af0…` **等于** 冻结记录。

三份基线全部 `baseline_verified = true`（`out/07_changeset.json`）。

### 2.3 改动前后冻结快照对照（`out/00_freeze_before.json` vs `out/00_freeze_after_reload.json`）

| 分组 | 项数 | 有变化 |
|---|---|---|
| `write_targets`（拟改文件） | 3 | **2**（`paper_engine.py`、`live_store.py`） |
| `read_dependencies`（只读依赖） | 5 | **1**（`live_api.py`，本单的文案 + 策略字典透出） |
| `configs`（生效视图 + 闸门） | 2 | **0** |
| `frozen_rule_candidates`（冻结规则 `config.py`） | 1 | **0** |

差异明细在 `out/00_freeze_compare.json`。也就是说：**除了这 3 个文件，整个工程一个字节都没动。**

### 2.4 `paper_engine.py` 改了什么（结构）

1. **新增取数层**（不碰策略）：
   - `REALTIME_SOURCE` / `REALTIME_FILL_ENV` / `REALTIME_FILL_MODES` / `M15`
   - `realtime_fill_mode()` — 读环境变量 `WX_REALITY_FILL_MODE`，非法值回落默认，不静默放行回填
   - `effective_fill_mode(origin)` — 按**调用来源**分派（见 2.4）
   - `align_15m_up(ts)` — 与既有 `bar_at_or_after` 的 `open_time >= ts` 语义逐位一致
   - `realtime_15m_bar(con, symbol)` — 读 `realtime_klines` 里该标的**当前那一根**
   - `resolve_fill_bar(...)` — 决定「用哪根 bar + 从哪条路取到」，返回 `(bar, info)`
2. **`open_position` 里换了「bar 从哪来」**：原来 `bar_at_or_after(...)`（强制 `is_closed=1`），
   现在先判 `earliest_fill_ms > attempt_ms`（时钟没到执行点 ⇒ 挂单），再走 `resolve_fill_bar`。
   `bar is None` 时按原因分流：`pending_future_fill` ⇒ 挂单；其余 ⇒ 拒单并写清原因。
3. **来源标签带可核对的标记**：`market_source` 写成
   `binance.um.realtime_klines_15m.open|reality=1|fill_source=realtime_15m_open|data_avail_ms=…`，
   零 schema 侵入就能从账本本身看出「这笔是不是当场成交」。
4. **三列落库**：`paper_position` / `paper_order` / `paper_fill` 各补 `reality_fill`、
   `data_available_at`、`fill_source`。实现方式是对刚插入的新行 `UPDATE`，
   **不改原来 9 条 INSERT 分支**（改动局部化，审查面小，不触碰任何既有列与历史行）。
5. **返回 dict 加 `timeline`**：信号确认时刻 / 信号可用时刻 / 下单请求时刻 / 首次请求时刻 /
   计划执行时刻 / 撮合时刻 / 执行 bar 开盘 / 执行 bar 收盘 / 数据可得时刻 / 撮合比计划晚多少毫秒。
6. **`FILL_POLICY` 文案**：`fill_price` 改成新口径描述；原 `known_gap`（记录旧缺陷的原文）
   **不删键名**（既有观测脚本会读），改为「已修复」表述，修复前原文完整保留在 `known_gap_history`。

### 2.5 为什么要按「调用来源」分派（这条容易做错）

`effective_fill_mode(origin)` 的优先级：

1. 环境变量 `WX_REALITY_FILL_MODE` 显式设定 —— 隔离取证与回滚用；
2. `origin in ('replay','test')` ⇒ `legacy`（历史回放按旧口径，`reality_fill=0`）；
3. 其余（生产 `live` / `recovered` / `manual`）⇒ 默认 `realtime_only`。

**为什么必须分**：回放和隔离测试用的全是**历史**行情，实时表里根本不会有那根 bar。
若对它们也强制 `realtime_only`，历史上「挂单→撮合」的回放与既有隔离测试会全部变成「拒单」——
那是把「现实撮合」误当成「不许回放」。用户口径「按现实时间入场」约束的是**生产实时实例**。

这不是事后补的借口，是**踩出来的**：第一版没分来源，直接把既有测试
`test_paper_live_01::test_03` 弄坏了（`pending_matched 0 != 1`）。

生产代码实际传的是 `origin="live"`（`live_worker.py:364`）与 `origin="recovered"`
（`unified_runtime.py:2566`），两者都落在默认 `realtime_only` 一侧。

### 2.6 `live_store.py` / `live_api.py` 为什么算「最小必要」

- `live_store.py`：只在 `PAPER_COLUMNS` 末尾**加性**追加 9 项（3 表 × 3 列），历史行保持 NULL。
  不加这三列，账本就无法自证「这笔是不是当场成交」，只能靠报告补记——那正是本单要消灭的东西。
- `live_api.py`：两处，都是**透出**而非**改变行为**：
  ① `quality.note` 的成交时钟文案改成新口径（原来那段文案描述的是旧公式，与代码不符）；
  ② `quality.fill_policy` 改为 `{**PE.FILL_POLICY, "effective_mode_runtime": PE.realtime_fill_mode()}`，
     让 API 直接透出**运行时实际生效的撮合模式**。
  ② 的唯一目的是给「运行实例已加载」提供**内容级**证据（见 §5），避免只凭 pid / 哈希自报。

---

## 3. 行情事实与时序（改之前先量过）

没有先证明「执行点当场确实拿得到那个开盘价」，这次改动就是空中楼阁。

### 3.1 实时表覆盖（`out/01_probe.json`，00:38:53Z）

| 交易所/市场 | 实时 15m 行数 | 同一根 bar 的标的数 |
|---|---|---|
| binance um | 522 | 522（`open_time` 全为 `00:30:00Z`，`is_closed` 全 0） |
| okx swap | 286 | —（该表 okx 侧长期未刷新，最新 `received_at` 停更） |

池是 528 个标的，binance um 有 522 个有实时行 ⇒ **6 个标的没有实时 15m 行**，
这 6 个走「无实时行情 ⇒ 拒单」路径，不伪造（隔离用例 P6 就测这条）。

### 3.2 关键性质：实时表永远只有「正在形成的那一根」

`realtime_klines` 主键是 `(exchange, market, symbol, timeframe)` ⇒ 每标的每周期**只有一行**。
bar 一闭合，这一行就被下一根**覆盖**。于是：

- 执行点当场读得到它的 `open`；
- 一旦执行点过去，这一行已经变成更晚的一根 ⇒ **事后拿不到**
  ⇒「错过执行点」在数据层直接表现为「查不到」，不需要靠时间差推断。

这条性质是整套设计的地基，实测确认：闭合后立刻切到下一根（`out/01_probe_forward.json`
的 `realtime_row_after_close`，5 个标的全部变为 `00:45:00Z`、`is_closed=0`、open 已换新价）。

> **但这句「覆盖」要修正**：实现上不是原地 UPDATE，而是「先 DELETE 旧行、等下一个 tick 再 INSERT」。
> 因此边界后有一段**约 3.4 秒完全没有行**的窗口。详见 **§3.4**（实测 + 代码位置）。
> 这一段不影响设计成立（引擎会挂单等一个周期），但**必须知道**，否则会把它当成 bug 去查。

### 3.3 前向一致性：实时读到的 open 与收盘后的 open 逐位相同

这是「换了取数时刻、没换价格口径」的硬证据（`out/01_probe_forward.json`）：

- 采样 00:43:42Z，bar 区间 `00:30:00Z → 00:45:00Z`，此时 `is_closed=0`；
- 等 **167.9 秒**到 bar 闭合后，读 `candles` 里同一根 bar 的 `open` 比对：

| 标的 | 实时 open | 收盘后 open | 差 |
|---|---|---|---|
| BTCUSDT | 80745.60 | 80745.60 | 0 |
| ETHUSDT | 2345.71 | 2345.71 | 0 |
| SOLUSDT | 101.2400 | 101.2400 | 0 |
| BNBUSDT | 743.250 | 743.250 | 0 |
| XRPUSDT | 1.3274 | 1.3274 | 0 |

`all_match = true`，`abs_diff = 0.0`。闭合后落库延迟 207~1128 ms。

### 3.4 重要修正：bar 边界后实时行会**消失约 3.4 秒**（实测，`out/03c_row_presence.json`）

上面 §3.2 说「bar 一开盘就存在」—— **不完全对**。实测发现了一个 DELETE→INSERT 的窗口。

02:15:00Z 边界前后每 100ms 采样 5 个标的（318 个样本，实际间隔 p50 = 105 ms）：

| 时刻 | ETHUSDT 在 `realtime_klines` 里的状态 |
|---|---|
| 边界前 2,991 ms | 有行，`open_time=02:00:00Z`、`is_closed=0`、`open=2354.49` |
| **边界后 3,163 ms** | **无行**（该标的整行不存在） |
| 边界后 6,482 ms | 有行，`open_time=02:15:00Z`、`is_closed=0`、`open=2358.26` |

**5 个标的（ETH/BTC/SOL/BNB/XRP）的转折时刻完全一致到毫秒**，缺口都是 **3,319 ms**。

**代码层根因（不是猜测）**：`realtime_collector.py:1538`

```python
# 收盘后把该 bar 从临时层移除（它已是已收盘历史）
con.execute("DELETE FROM realtime_klines WHERE exchange=? AND market=? AND symbol=? "
            "AND timeframe=? AND open_time=?", (exchange, market, symbol, tf, b["open_time"]))
```

也就是说：bar 收盘 ⇒ 写进已收盘历史 ⇒ **把实时表那一行删掉**；新 bar 的行要等下一个 tick 才 INSERT。
5 个标的同步，是因为采集器一轮处理、一次提交。

**这对本单意味着什么（三条，都是好的）**：

1. 执行点后 0~3.4 秒内读不到那根 bar ⇒ 引擎返回 `pending_future_fill` ⇒ **挂单等一个周期**，
   **不拒单、不回填**。这正是「缺数据不伪造成交」的实现。
2. 真实成交时刻不是「执行点后 1.5 秒」，而是「执行点后约 6.5 秒」（等新行出现）。
   仍然是**秒级**，相比改动前的 15 分钟偏差，量级差 3 个数量级。
3. 那条 DELETE 是「先提交删除、再等新 tick 插入」⇒ 若进程在两者之间挂掉，
   该标的实时行会**一直缺到重启**。这时后续信号会拿到
   `fill_point_closed_without_realtime`（拒单）。**这是数据层风险，不是本单引入的**，已列入 §10。

### 3.5 旧实现到底晚多久（`out/01_probe.json` q4，40 根 bar）

| 指标 | min | p50 | p90 | max |
|---|---|---|---|---|
| `available_at − close_time` | 95 ms | **1540 ms** | 3105 ms | 3109 ms |
| `imported_at − close_time` | 2993 ms | 4485 ms | 8030 ms | 8045 ms |

旧实现要等到 `is_closed=1` 才有 bar，而 bar 收盘在执行点之后 15 分钟
⇒「执行」实际发生在执行点后 **15 分 1.5 秒**（乐观）到 15 分 8 秒（含入库延迟），
但账本 `entry_ts` 写的是执行点那一刻。**这就是要修的偏差。**

---

## 4. 隔离证据

### 4.1 十个正反例（`out/02_isolation.json`，10/10 通过）

全部在**独立账本副本 + 独立子进程**里跑（`live_store` 在 import 时解析账本路径，
同进程换不了账本）。行情库一律 `mode=ro`。

| 用例 | 期望 | 实际 |
|---|---|---|
| P1 现实成交 | 执行点那根正在形成 ⇒ 当场用它的 open 成交，`reality_fill=1` | `ok=True` `reality_fill=True` `fill_source=realtime_15m_open` |
| P2 执行点已过不回填 | 拒单，不新增持仓 | `fill_point_passed_no_backfill`，新增持仓 0 |
| P3 执行点未到 | 登记挂单 | `pending_future_fill` + pending 行 |
| P4 旧行为对照（`legacy`） | 同一时刻旧口径**不能**成交 | `pending_future_fill`（bar `is_closed=0`） |
| P5 `prefer_realtime` 回退 | 回退闭 bar 但明确标记 | `ok=True` `reality_fill=False` `fill_source=closed_15m_open` |
| P6 无实时行情不伪造 | 拒单 | `fill_point_closed_without_realtime` |
| P7 幂等 | 同一 `signal_uid` 不重开 | `duplicate_signal_uid` |
| P8 epoch 门禁 | 早于纸面 epoch 不补开 | `before_paper_epoch_no_backfill` |
| P9 停机后不追补 | 执行 bar 在本进程启动前已收盘 ⇒ 拒单 | `fill_point_closed_before_process_start` |
| P10 重启不丢账 | 复制生产账本跑 `advance_pending`，持仓/计数逐项不变 | 持仓 `[1,2,3,4]`、计数同 = True |

P4 是**最容易被做成假通过**的一条，值得单说：初版 P4「失败」了（`ok=True`），
因为脚本在开头读了一次 bar，等用例真正执行时那根已经闭合，`legacy` 也能成交。
修法是让 P1/P4 **各自在用例执行那一刻重新读**当前 bar。修完还失败——根因是
`resolve_fill_bar` 对 `legacy` 仍会走实时分支（实时表里已有当前那根 ⇒ 直接现实成交）。
代码因此加了一条 `if not merged and mode != "legacy":`，让 `legacy` **完全跳过**实时路径。
**没有这条，`legacy` 对照臂就是假的。**

### 4.2 边界对齐的精确撮合（`out/03_boundary_fill.json`）

02 的 P1 虽然成交了，但 `lag_ms` 里含着「脚本准备时间」（bar 已开盘几分钟才跑到用例），
不能代表生产条件（生产 `step_once` 每 1~2 秒一轮）。所以单独做一条把时序钉死，
**并且严格照生产两段式**（为什么必须两段式，见 §9 第 12 条）：

| 时刻 | 动作 | 期望 |
|---|---|---|
| `S` = 刚过去的 15m 边界 | 信号确认 bar 收盘 = `event_time` | — |
| 立刻（`S` 之后 ~10s） | `open_position(event_time=S, origin=live)` | 登记挂单 `pending_future_fill`，写入 `paper_request_anchor` |
| `T0 = S + 15m` | 执行点（确认后下一个合法 15m 执行点） | — |
| `T0 + 1.5s` | 读实时 bar，应已是 `T0` 那根且 `is_closed=0` | — |
| `T0 + 1.5s` | `advance_pending(T=T0+1.5s)` 推进撮合 | `reality_fill=1`、`fill_lag_ms ≤ 60s` |
| `T0 + 20s` | 再读同一根 | **open 在 bar 内恒定**（不是某一瞬间的报价） |
| `T0 + 15m + 90s` | 读 `candles` 里该 bar 已闭合的 `open` | 与成交原始价**逐位相同** |

`origin` 取**生产原值 `live`**，且把 `WX_REALITY_FILL_MODE` 从环境里 pop 掉
⇒ 验证的是**不带任何开关时的生产默认路径**，比显式指定更硬。

标的用 **ETHUSDT**（不是 BTCUSDT）：隔离工作区是生产账本副本，里面 #4 是
`test_probe_succ` 的 BTCUSDT 未平仓持仓，会先触发「同币不加仓」而挂不上单（§9 第 13 条）。

> 结果见 §7「边界实测」。

### 4.3 零回归对照（`out/04_regression_parity.json`）

同批 `unittest` 跑两遍：一遍「已改版」，一遍把 `paper_engine.py` 临时换回改动前快照
（`a99f8af0…`，哈希已核等于冻结记录），逐条比对。

| 同通过 | 同失败 | 回归 | 修好 |
|---|---|---|---|
| 34 | 1 | **0** | 0 |

唯一同失败是 `test_paper_live_01::test_03` 的止损断言（`价格击穿止损线必须触发止损平仓退出`）。
**这是既有失败，不是本单回归**——A/B 对照证明：换回改动前快照跑同一测试，同样失败于同一条断言。
脚本用 try/finally 保证跑完恢复文件，`restore_ok = true`，恢复后哈希 `bae9d934…17a5e`。

---

## 5. 运行实例已加载（内容级证明）

### 5.1 重载纪律

- **只**重启 Runtime + API；**不动** Worker（`68775`/`88794` 前后不变）、**不动**采集器（`42278` 前后不变）。
- 不用 `restart`（会连带停 Worker）——改为「核验身份 → SIGTERM 目标 pid → `sh start.sh --enable-paper`」。
- 不写 `live_run`、不重置 epoch、不改策略规则/阈值/风险/仓位倍数。
- 失败即停，保留现场，不做破坏性恢复。

### 5.2 前后对照（`out/05_reload.json`）

| 项 | 重载前 | 重载后 |
|---|---|---|
| Runtime pid | 43827（10-27 18:19:02Z 起，cycle 4581） | **77433**（01:04:37Z 起，cycle 从 0 重新增长） |
| API pid | 43819 | **77425** |
| Worker | `[68775, 88794]` | `[68775, 88794]`（**不变**） |
| 采集器 | 42278 | 42278（**不变**） |
| `fill_policy` 键数 | 11 个 | **17 个** |
| `quality.note` | 旧公式 | 出现「执行点」新文案 |

`fill_policy` 新增的 6 个键：`realtime_fill_mode`、`realtime_fill_price`、`reality_fill_flag`、
`on_exec_point_already_passed`、`known_gap_history`、`effective_mode_runtime`。

### 5.3 13 项检查：全部成立（其中 1 项需等 bootstrap 完成后再读）

重载当场判定通过 12 项：runtime pid 变了 / api pid 变了 / Worker 不变 / 采集器不变 /
`fill_policy` 有新键 / `note` 出现「执行点」/ 状态 running / 健康 healthy /
启动时刻晚于源码 mtime / BTC 持仓逐项不变 / 持仓总数不变 / 闸门仍为 true。

**当场唯一 FAIL 是 `universe_526`**：05 等到 `status == running` 就立刻读，此时 `cycle_count=0`、
`universe=0`、`watermark=None` —— **bootstrap 还没跑完**（实测 bootstrap 约 7.6 分钟），
这个检查读得太早了。

`05b_recheck.py` 等到 bootstrap 完成后复查，**6/6 通过**：

| 复查项 | 值 |
|---|---|
| `universe_526` | **526** |
| `cycle_count_grew_from_zero` | 0 → 21（持续增长） |
| `watermark_present` | `wm1h=2026-10-28T00:00:00Z`、`wm4h=2026-10-27T20:00:00Z` |
| `runtime_status_running` | running |
| `runtime_health_healthy` | healthy |
| `allow_new_entries_still_true` | true |

复查结果**追加**在 `05_reload.json` 的 `after_bootstrap` 字段，**不覆盖**原 `checks` ——
「取样太早」这个事实要留着，不能被抹掉。

### 5.4 重载后 API 直接透出的内容级证据

```
GET /api/v1/paper/accounts  →  quality.fill_policy  共 17 键
  effective_mode_runtime       = realtime_only          ← 运行时实际生效模式
  realtime_fill_price          = open_of_the_same_15m_bar_read_at_bar_open_from_realtime_klines
  reality_fill_flag            = 1=执行点当场成交；0/NULL=闭 bar 后补记
  on_exec_point_already_passed = reject_fill_point_passed_no_backfill（不回填、不顺延到下一根）
  fill_price                   = open_of_the_15m_bar_at_or_after(...)：默认在 bar 刚开盘就从
                                 realtime_klines 读；仅 legacy 模式才等 is_closed=1
quality.note                   含「执行点 = max(信号确认时刻,信号可用时刻)+15m」
```

**为什么这条比 pid / 哈希更硬**：`effective_mode_runtime` 是进程**在响应请求时实时调用**
`PE.realtime_fill_mode()` 算出来的，只有加载了新代码才可能存在这个键、才可能返回这个值。
旧进程即使文件已经改了，也只会返回旧的 11 个键（重载前实测就是 11 个）。

### 5.5 重载顺带完成了加性迁移，且零行被改写

| | 重载前 | 重载后 |
|---|---|---|
| `paper_position/order/fill` 有 `reality_fill` 等三列 | **False**（列不存在） | **True** |
| `paper_position` | 4 | 4 |
| `paper_order` | 15 | 15 |
| `paper_pending` | 4 | 4 |
| `paper_processed` | 4 | 4 |
| 持仓 | #1/#2/#3 closed+voided，#4 BTCUSDT open | **逐项相同** |

四个臂逐个核对（`out/00_freeze_compare.json`）：3a/3b/3c/3d 的**所有表行数差异 = 无**，
只有文件哈希变了（因为加了三列）。也就是说：**加列发生了，行数一条没变，历史行没被改写**
（新列值为 NULL）。

---

## 6. 自然扫描 / 候选 / 订单 / 成交：全部为零（如实记零）

重载完成后（新实例 pid **77433**）观测 **13 分 30 秒**，28 个采样点，全程只读。
原始数据：`out/06_natural_observe.json`（110 KB，含 28 条逐样本记录）。

### 6.1 运行是活的（先证明这个，否则「零」没有意义）

| 项 | 首样本 01:12:44Z | 末样本 01:26:15Z |
|---|---|---|
| pid | 77433 | 77433（**全程未变，没重启**） |
| `cycle_count` | 19 | 189（**+170**，一直在扫） |
| `universe` | 526 | 526 |
| 状态 / 健康 | running / healthy | running / healthy |
| `errors` | — | **空**（14 分钟里没有一条报错） |

### 6.2 但业务侧一项都没有

| 项 | 首 | 末 | 说明 |
|---|---|---|---|
| 自然信号（`origin='live'`） | **0** | **0** | `live_signal` 里只有 `historical_research` **289** 条，没有一条自然行 |
| 挂单（`pending`） | 0 | 0 | — |
| 未平仓持仓 | 1 | 1 | 就是那条保留的 BTC 测试持仓 #4，不是新开的 |
| 订单 `filled` | 4 | 4 | 全是历史行 |
| 订单 `rejected` | 11 | 11 | 全是历史行 |
| `reality_fill=1` 的持仓 | **0** | **0** | 没有任何一笔是新口径成交的 |

**最硬的一条**：`recent_reject_reasons` 里最新的拒单时间戳还是 **2026-10-27T15:46:41Z**
（`test_probe`，`before_paper_epoch_no_backfill`）。也就是说这 14 分钟里
**连一次「尝试开仓 → 被拒」都没有发生**，不是「被新口径挡住了」，是**根本没有候选**。

### 6.3 水位为什么没动

`watermark_1h` 全程 `2026-10-28T00:00:00Z`、`watermark_4h` 全程 `2026-10-27T20:00:00Z`。
这不是卡住：1h 水位要等 `01:00Z` 那根 1h bar **收盘（02:00Z）**才推进，4h 要等 `00:00Z` 那根
收盘（04:00Z）才推进。观测窗口（01:12–01:26）落在两根 bar 中间，**不动是正确行为**。

### 6.4 零的归因（不猜，只列已证事实）

本单**没有**、也不该宣称「零候选是因为新口径」：

- 自然候选为 0 的既有归因（前序工单已定案）：FT-LOW4H 是**反转策略**
  （`BOX → SWEEP(须跌破下沿) → RETEST → 突破上沿`），单边行情里等不到跌破下沿；
  短窗口卡点在 `BOX→SWEEP`（`state_counter.SWEEP` 恒 0）。
- 本次观测与之**不矛盾**：`signals_generated_total = 0`，即连候选生成这一步都没到，
  还没轮到撮合层做判断。
- 所以「零成交」**不能**用来证明或否定新撮合逻辑——它证明的是「这段时间没有素材」。
  新撮合逻辑的正反证据在 §4（隔离）与 §7（边界实测）。

---

## 7. 边界实测：执行点后 6.5 秒成交（`out/03_boundary_fill.json`）

这是本单**最硬的一条**证据：一次完整的「信号确认 → 登记挂单 → 执行点撮合」全链路，
标的 ETHUSDT，走**生产默认路径**（`origin="live"`、无任何环境变量开关）。

### 7.1 逐事件时间链

| 事件 | 时刻 | 相对执行点 |
|---|---|---|
| 信号确认（`event_time`，= 02:00 那根 15m bar 收盘） | 02:00:00.000Z | −15 分 0 秒 |
| 下单请求（真实挂钟，写入 `paper_request_anchor`） | 02:03:03.279Z | −11 分 56.7 秒 |
| **计划执行点**（确认 + 15m） | **02:15:00.000Z** | 0 |
| 执行点后首次读到那根 bar | 02:15:06.505Z | **+6,505 ms** |
| 数据可得时刻（`received_at`） | 02:15:05.963Z | +5,963 ms |
| **撮合时刻**（`matched_at`） | **02:15:06.515Z** | **+6,515 ms** |
| 执行 bar 收盘 | 02:30:00.000Z | +15 分 |

### 7.2 成交明细

| 项 | 值 | 核对 |
|---|---|---|
| 原始价（`raw_price`） | **2358.26** | = 02:15 那根 bar 的 `open` |
| 成交价（`entry_price`） | **2359.44** | = 2358.26 × 1.0005（5bps 滑点，口径未改） |
| 执行 bar 开盘（`entry_ts`） | 02:15:00.000Z | **= 执行点**，不是处理时刻 |
| `fill_lag_ms` | **6,515** | 撮合比执行点晚 6.5 秒 |
| `reality_fill` | **1** | 当场成交 |
| `fill_source` | `realtime_15m_open` | 实时表取数 |
| `data_available_at` | 02:15:05.963Z | 比撮合时刻**早 552 ms** ⇒ 数据先到手、再撮合，**无前视** |

### 7.3 三项关键性质

1. **执行点那根 bar 当时正在形成**：首次读到它时 `is_closed = 0`（还在走）。
2. **`open` 在 bar 内恒定**：执行点后 20 秒复读同一根，`open` 仍是 `2358.26`，`open_unchanged = True`。
   ⇒ 用它当成交价不是「抓了某一瞬间的报价」。
3. **撮合发生在执行点后 6.5 秒，不是 15 分钟**。对照 §3.5：改动前「执行」实际发生在
   执行点后 15 分 1.5 秒 ~ 15 分 8 秒。**偏差从「分钟级」压到「秒级」。**

### 7.4 6.5 秒是怎么来的（不是引擎慢）

不是引擎慢，是 §3.4 那个采集器 DELETE→INSERT 空窗：
执行点后 0~3.4 秒实时表里没有该标的的行 ⇒ 引擎挂单等待 ⇒ 新行出现（+6.5 秒）后下一轮就成交。
**如果实时行在 +1 秒就存在，成交会在 +1 秒左右**（`out/03b_diag_repro.json` 的副本复现里，
`fill_lag_ms` 就是 1,500 ms）。

### 7.5 判定（12 项）

| 检查 | 结果 |
|---|---|
| `step0_registered_pending` | PASS（`pending_future_fill`） |
| `effective_mode_is_realtime_only` | PASS |
| `bar_first_seen_within_60s` | PASS（+6,505 ms） |
| `bar_at_exec_point_was_forming` | PASS（`is_closed=0`） |
| `bar_at_exec_point_is_expected_one` | PASS（`open_time=02:15:00Z`） |
| `fill_happened_within_60s_of_exec_point` | PASS（6,515 ms） |
| `reality_fill_flag_is_1` | PASS |
| `fill_source_is_realtime` | PASS |
| `open_constant_within_bar` | PASS |
| `raw_price_equals_realtime_open` | PASS |
| `raw_price_equals_candles_open` | 待 02:30 收盘后比对（本节在收盘比对前写就，完成后回填） |
| `entry_ts_equals_exec_point` | PASS |
| `matched_at_after_exec_point` | PASS |

### 7.6 这个执行点之前的三次失败（保留现场，不删）

| 次数 | 现象 | 真因 | 是不是缺陷 |
|---|---|---|---|
| 1 | 用例传 `origin="test"` | `test` 按设计回落 `legacy`，测的不是现实撮合 | 不是，用例错 |
| 2 | 拒单 `earliest_fill_before_first_request_clock` | 单段式调用，`first_req` 取真实挂钟 ⇒ 撞上「旧信号不得续期」保护 | **不是，是保护生效** |
| 3 | 执行点后 1.5 秒读到 `None`，挂单没推进 | 采集器 DELETE→INSERT 空窗（§3.4），脚本只推进一次 | 不是，引擎行为正确 |

---

## 8. 既有 BTC 测试持仓：只读溯源，一个字节都没动

工单要求「既有 BTC 测试持仓须先只读溯源并保留账本，不删除、不强平、不改写历史成交」。

`数据/paper/paper_3a.sqlite3` 现有 4 条持仓，本单**全程只读**：

| id | 标的 | 状态 | voided | 入场价 | `signal_uid` | 性质 |
|---|---|---|---|---|---|---|
| 1 | BTCUSDT | closed | 1 | 82033.1 | `sig_closeloop_btc_…` | 闭环测试合成，已作废 |
| 2 | BTCUSDT | closed | 1 | 81970.0 | `sig_closeloop_btc_…` | 闭环测试合成，已作废 |
| 3 | BTCUSDT | closed | 1 | 81970.0 | `sig_ui_test_…` | 页面手工测试，已作废 |
| 4 | BTCUSDT | **open** | 0 | 80469.5 | `test_probe_succ` | 人工探针持仓，**保留** |

#4 的 `entry_ts = 1793117700000`（2026-10-27T14:55Z）、止损 76566.2、`origin=signal`。
它是人工探针，**不是自然策略成交**，成绩统计必须排除（`voided` 那 3 条同样排除）。
本单未删除、未强平、未改写，`paper_position` 行数与内容前后逐项相同（§5.4）。

3b/3c/3d 三个臂是空库，未动。

---

## 9. 失败、反例与踩过的坑（全部保留）

这一节不是自我批评，是给复核者省时间：**这些地方看起来会像「通过」，其实是坑。**

1. **`candle_versions` 没有 `imported_at` 列** —— 探测脚本一开始直接查，报
   `no such column: imported_at`。改走 `LEFT JOIN sources s ON s.id = v.source_id` 取 `s.imported_at`。
2. **隔离脚本读不到 `reality_fill` 列** —— 因为副本是加性迁移**之前**的账本。没有让读数失败，
   而是加 `PRAGMA table_info` 探测 + `NULL AS 列名` 占位，并在结果里如实标 `has_reality_columns`。
   **不能让「读不到」变成「没有」。**
3. **P4「legacy 旧行为对照」两次失败** —— 见 §4.1。第一次是取样时刻错，第二次是代码真的有问题
   （`legacy` 仍走实时路径）。修的是代码，不是用例期望。
4. **把既有测试弄坏了两次** —— 第一次 `pending_matched 0 != 1`（未按来源分派，回放被强制现实撮合），
   第二次 `exits_triggered 0 != 1`（**既有失败**，A/B 对照已证）。第一次是回归，已修；第二次不是。
5. **`04` 初版解析到 0 个用例** —— unittest 对带 docstring 的用例分两行打印（测试名一行、
   `... ok` 在 docstring 行末），tail 匹配逻辑写错了。修完才是 34/1/0/0。
6. **`pytest` 不存在、系统 `python3` 无 numpy** —— 改用项目 venv + `unittest`；
   纯标准库的只读探测用 `versions/3.13.12`。
7. **`ps` 命令被禁**（`operation not permitted: ps`）—— 改用 `psutil.process_iter` 与 `os.kill(pid,0)`。
8. **`paper_runtime_3a_status.json` 有两个** —— 旧文件里的 pid 98765 早已不存在、时间停在 10-23；
   活跃状态在 `locks/paper_runtime_3a_status.json`。**看错文件会得出「进程没了」的错误结论。**
9. **孤儿 Worker pid 88794**（10-24 起，指向 `isolation_test_04_ipqsy2ry` 临时库）——
   不属本单授权范围，只如实列出、**不动它**。
10. **`05` 用错解释器直接崩**（`KeyError: 'worker_pids'`）—— `psutil` 只有项目 venv 里有。
    已加硬保护：**没有 psutil 就拒绝重载**（否则 `wait_gone` 恒返回 True，
    可能在旧进程没死时拉起新进程，双 Runtime 抢同一账本）。
11. **`03` 第一次跑错了来源标记** —— 用例传 `origin="test"`，而按来源分派 `test` 会回落 `legacy`
    ⇒ 那条用例根本测不到现实撮合，跑到 15 分钟末尾会全判失败。已改为生产原值 `live`。
    （这次是**在结果出来之前**发现的，原始失败记录是「被 kill 的运行」，不是数据。）
12. **`03` 第二次仍然不成立，原因更隐蔽** —— 只在执行点直接调一次 `open_position`、
    把 `event_time` 设成 15 分钟前，会撞上 `paper_engine.py:1259-1262`：
    `origin='live'` 且无 `pending_id` ⇒ `is_replay=False` ⇒ 首次请求墙钟取**真实挂钟（= 现在）**
    ⇒ `earliest_fill_ms(= 执行点) < first_req(= 现在)` ⇒ 拒单 `earliest_fill_before_first_request_clock`。
    **这不是缺陷，正是「旧信号不得续期」的保护**（SIGNAL-NOW-01-WORK 修的就是这条）。
    真实生产是**两段式**：信号时刻 `open_position` 登记挂单（写入 `paper_request_anchor`）→
    执行点 `advance_pending` 推进撮合。所以 `03` 必须照两段式跑，否则测的不是生产链路。
13. **`03` 第三个坑：BTCUSDT 挂不上单** —— 隔离工作区是生产账本的**副本**，
    里面 #4 是 `test_probe_succ` 的 BTCUSDT 未平仓持仓（工单要求原样保留），
    于是先触发「同币已有持仓 ⇒ 不加仓」，挂单根本登记不上。
    改用 ETHUSDT（副本里无持仓，P1 已实测实时行情可用）。**不能为了跑测试去删那条持仓。**
14. **`06` 输出被 `| tail` 缓冲** —— 观测期间看不到实时行，只能等结束一次性出 JSON。
    这是取数方式问题，不影响结果；但**不能因为「没看到输出」就以为它在空转**。
15. **`03` 第四个坑（最有价值的一个）：bar 边界瞬间实时表里可能没有该标的的行** ——
    01:45 那一跑，执行点后 **1.5 秒**读 `realtime_klines`，ETHUSDT 返回 **None**；
    20 秒后再读又有了（`open_time=01:45:00Z`、`open=2357.15`）。
    那一刻引擎走闭 bar 路径、找不到已闭合 bar ⇒ 正确返回 `pending_future_fill`
    （**不拒单、不回填**），但脚本只推进一次就永远等不到。
    修法：照生产 `step_once` 的 1~2 秒节奏**重试**，并逐次记录「第几次才拿到实时行」。
    **这条同时说明一件重要的事**：瞬时缺数据不会导致误拒或回填，只会让这笔多等一个周期。

---

## 10. 未验证 / 待裁决（不擅自改规则）

| 项 | 事实 | 影响 | 最小待决 |
|---|---|---|---|
| `fill_ts` 语义 | 仍是**本轮循环起始时刻**，不是撮合完成时刻（`step_once` 入口取一次 `now`，扫完 526 标的才撮合） | `matched_at` 只能当「本轮开始处理」的下界；`fill_ts − bar_close` 会**低估**真实延迟 | 是否改成撮合完成时刻（改代码） |
| `data_available_at` 与 `matched_at` 的先后 | 实测 P1 里 `data_available_at` 比 `matched_at` **早 6.2 秒**（顺序正确）；但代码层没有强制 `data_available_at <= attempt_ms` | 理论上采集器可能在 `attempt_ms` 之后、读取之前写入一行 ⇒ 出现「数据可得时刻晚于撮合时刻」的读数 | 是否补这条校验（改代码） |
| 代码层没有 `bar_close <= attempt_ms` 校验 | 只信 `is_closed`（反重力 D4 实证）；真实数据里该前置条件不出现 | 潜在缺口，非当前实际缺陷 | 是否补（改代码） |
| 挂单推进路径 `is_replay` 恒 True | `paper_engine.py:1093` 含 `or pending_id is not None` ⇒ 挂单路径上那道 `earliest_fill_before_first_request_clock` **从不执行** | 与注释「外部时钟不得改变成交下限」矛盾；危害有限（`bar_open ≥ earliest_fill ≥ first_req` 已成立） | 补校验还是改注释 |
| `ratification_status` | `FILL_POLICY.ratification_status` 仍是 `pending_human_ratification` | **成交政策本身未获人工批准**——本单只改「什么时候取价」，没有、也不能替人批准政策 | 需人工批准（不属本单） |
| `realtime_klines` 只覆盖 binance um | 522/528，okx swap 侧该表停更 | 6 个标的会走「无实时行情 ⇒ 拒单」 | 是否扩采集范围（不属本单） |
| 拒绝码语义重叠 | 生产 live 信号若「首次被看到时已过执行点」，会先撞 `earliest_fill_before_first_request_clock`（因为 `first_req` 取真实挂钟），而不是 `fill_point_passed_no_backfill` | **结果都是拒单、都不回填**，安全性一致；但拒绝码不能直接当「错过执行点」的统计口径 | 是否把这条码拆细（改代码）；在此之前**统计时两种码要合并看** |
| 采集器删行有 ~3.4 秒空窗 | `realtime_collector.py:1538` 在 bar 收盘时 `DELETE` 该行，新行等下一个 tick 才 INSERT（§3.4 实测 3,319 ms） | ① 执行点后 0~3.4s 读不到 ⇒ 挂单等一周期（安全）；② 若进程在 DELETE 与 INSERT 之间挂掉，该标的实时行会缺到重启，后续信号会被 `fill_point_closed_without_realtime` 拒单（**误拒**） | 是否改成单事务 upsert 或「先 INSERT 新行再 DELETE 旧行」（改采集器，**不属本单**，本单只记录） |

**没有动规则**：策略状态机、确认条件、阈值、OI 门槛、风险参数、仓位倍数、有效期规则、
`signal_execution_delay_minutes=15`、`fee_each_side`、`slippage_each_side` 全部逐字保留。

---

## 11. 目录与复现

```
evidence/PARALLEL-STRATEGY-01-WORK/
├── README.md                      ← 本文件
├── scripts/
│   ├── 00_freeze_snapshot.py      只读冻结（源码/配置/运行身份/账本）
│   ├── 01_probe_realtime_source.py 实时行情可行性 + --wait-forward 前向一致性
│   ├── 02_isolated_reality_fill.py 10 个正反例
│   ├── _iso_case.py               单例执行器（子进程，隔离账本）
│   ├── 03_boundary_fill.py        边界对齐精确撮合（生产两段式 + 生产节奏重试）
│   ├── 03c_row_presence.py        跨边界高频「实时行存在性」探测（发现 3.4 秒空窗）
│   ├── 04_regression_parity.py    零回归 A/B 对照
│   ├── 05_controlled_reload.py    受控重载
│   ├── 05b_recheck.py             bootstrap 完成后复查
│   ├── 06_natural_observe.py      自然观测（只读采样）
│   └── 07_changeset.py            变更清单 + 三份最小 diff
├── out/                           所有原始输出（JSON / diff）
└── isolated/                      隔离账本副本（每个用例一份）
```

复现（**注意解释器**：`05` 需要 `psutil`，用项目 venv；其余纯标准库即可）：

```sh
cd evidence/PARALLEL-STRATEGY-01-WORK/scripts

# 只读类
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 00_freeze_snapshot.py --tag after_reload
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 01_probe_realtime_source.py
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 07_changeset.py

# 隔离类
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 02_isolated_reality_fill.py
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 04_regression_parity.py

# 边界（需等 15 分钟边界；两段式，全程约 28 分钟）
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 03_boundary_fill.py ETHUSDT
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 03c_row_presence.py

# 重载（会重启 Runtime + API；不动 Worker/采集器）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python 05_controlled_reload.py
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 05b_recheck.py --wait 600

# 自然观测（只读）
/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 06_natural_observe.py --minutes 12 --interval 30
```

**回滚**：把 `paper_engine.py` 换回 `evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/snapshot/paper_engine.py`
（`a99f8af0…`）、`live_store.py` 回 `git show HEAD:框架/backend/live_store.py`（`02eaf41b…`）、
`live_api.py` 按 `out/07_live_api.diff` 反向应用（`585c9727…`），再按 §5 的纪律重载一次即可。
三列是**加性**的，回滚代码后它们仍留在库里、值为 NULL，不影响旧代码运行。
