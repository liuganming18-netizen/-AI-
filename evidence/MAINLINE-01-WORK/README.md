# MAINLINE-01-WORK｜现实时间成交收尾

> 生成时间：2026-10-28（北京时间）｜执行方：Work｜证据目录：`evidence/MAINLINE-01-WORK/`
> 承接 `evidence/PARALLEL-STRATEGY-01-WORK/`（旧产物完整保留、未删改）。
> **本文件只记录做了什么、证据在哪、哪些还没证明，不做验收结论**——验收由主代理独立进行。

---

## 0. 一句话

上一单把「什么时候取价」提前到了执行点，但账本里仍然只有**一个**挂钟时刻
（`fill_ts` = 本轮循环起始），既读不到「行情什么时候到手」，也读不到「撮合什么时候完成」。
本单把**三个时刻拆开落库并强制顺序**：`data_available_at ≤ read_at ≤ matched_at = fill_ts`，
并补了两条前视保护；`entry_ts` 继续只当「价格事件时刻」，不再兼任成交时刻。

**没有改任何经济口径**：成交价仍是「执行那根 15m bar 的 open」，滑点、手续费、止损、
风险、有效期、15 分钟延迟规则、状态机、阈值、OI 门槛一个字没动。

---

## 1. 四层分开报告（不合并成一句「通了」）

| 层 | 结论 | 证据 |
|---|---|---|
| **① 源码** | 改 2 个文件（`paper_engine.py` +103/−18、`live_store.py` +17/−0）；`live_api.py` 一个字没动 | §2；`out/09_changeset.json`、`out/09_*.diff` |
| **② 隔离行为** | 负向 5/5；重启/有效期/停机 **5/5**；真实时钟边界 **18/18**（重载前）+ **17/17**（重载后）；零回归（34 同通过 / 1 既有失败 / **0 回归**）；账本级不变式 3 笔新行 **0 违反** | §4、§5、§5.2、§6、§11 |
| **③ 运行实例已加载** | 已重载；内容级证据：`fill_policy` 由 17 键变 **21 键**，新增 4 个本单键 | §7、§8 |
| **④ 自然候选/订单/成交** | 自然信号 0、新挂单 0、新成交 0——**如实记零** | §9 |

③ 成立不等于 ④ 有结果。④ 为零的原因是「这段时间没有素材」，不是新逻辑挡住了什么
（证据：`live_signal` 里 `origin='live'` 的行数全程 0）。

---

## 2. 改了什么

### 2.1 变更清单（哈希前后 + diff 统计）

| 文件 | 改前 sha256 | 改后 sha256 | 行数 | diff |
|---|---|---|---|---|
| `框架/backend/paper_engine.py` | `bae9d934…517a5e` | `cbbde84a…4b6e9fba` | 3130 → 3215 | **+103 / −18** |
| `框架/backend/live_store.py` | `ba85a66d…de120d6` | `bcea899f…a12eef6c` | 1008 → 1025 | **+17 / −0** |
| `框架/backend/live_api.py` | `94302c96…b0f7fa2` | **同左（未改）** | 2223 → 2223 | 0 |

**没改的（逐个核对哈希，全部未变）**：

- `框架/backend/unified_runtime.py` — `e30e2135…5995470`
- `策略/WyckoffMaster/event_trend_v0_1/config.py` — `122683f2…0eedeb46`（**冻结规则，未动**）
- 生效视图 `10_研究参数生效视图_v2_2_user5000_shared.json` — `82dec362c182…`
- 闸门 `框架/backend/live/paper_new_entry_gate.json` — `f4691db2…`（mtime 仍为 2026-10-23，未改）

### 2.2 基线为什么可信

三个待改文件在动手**之前**整文件复制到 `isolated/backup/`（`out/00_backup_sha.txt`），
其 sha256 与 `out/00_freeze_before.json` 的冻结记录**逐位相同** ⇒
`diff(backup, 当前)` 就是本单改动，不含别人未提交的改动。
`09_changeset.json` 里 `all_three_baselines_verified = true`。

### 2.3 `live_store.py` 改了什么

`PAPER_COLUMNS` 末尾**加性**追加 6 项（3 表 × 2 列）：

| 列 | 语义 | 不变式 |
|---|---|---|
| `read_at_ms` | 读到成交所用行情那一刻的真实挂钟（**决策输入时刻**） | `data_available_at ≤ read_at_ms` |
| `matched_at_ms` | 撮合**完成**那一刻的真实挂钟（**实际执行时刻**） | `read_at_ms ≤ matched_at_ms`，且 `= fill_ts` |

历史行保持 NULL，不改写任何既有成交（`#4` 逐项核对相同，见 §2.5）。

### 2.4 `paper_engine.py` 改了什么（4 处）

1. **`resolve_fill_bar` 读行之前取一次挂钟**，写进 `info['read_at_ms']`
   —— 这是「我们什么时候去读的」，不是「数据什么时候来的」。
2. **`open_position` 加两条前视保护**（在拿到 bar 之后、任何写库之前）：
   - `realtime_fill_without_available_time`：声称当场成交（`reality_fill=1`）却拿不出
     `received_at` ⇒ 无法证明「这个价当时已经拿得到」⇒ **拒单**；
   - `data_available_after_read_no_lookahead`：`data_available_at > read_at_ms`
     ⇒ 读到的是读取之后才写进来的行（前视/时钟倒挂）⇒ **拒单**。
3. **`fill_ts` 与 `paper_order.ts` 改写成 `matched_at_ms`**（撮合完成时刻），
   不再写 `attempt_ms`（本轮循环起始）。`entry_ts` 仍是 `bar_open`（价格事件时刻），**未改**。
   另加一条 `matched_before_read_clock_skew`：时钟回拨导致时刻链不成立 ⇒ 拒单。
4. **落库与返回**：三表各写 `read_at_ms` / `matched_at_ms`；`market_source` 标签追加
   `|read_ms=…`；返回 dict 与 `timeline` 把四个时刻分开列。

### 2.5 为什么 `entry_ts` 不改（这条容易被误读成「没修好」）

`entry_ts = bar_open` 是**价格事件时刻**——「这个价格代表哪个时点」。
bar 先开盘、采集器后写入，所以 `entry_ts` **必然早于** `data_available_at`。
这不是缺陷，而是价格的定义时刻；它继续承担三个既有职责：
退出扫描区间 `closed_15m(sym, entry_ts, T)`、资金费计费窗 `(entry_ts, exit_ts]`、
最长持仓计时起点。

工单要求的「`entry_ts`/`fill_ts` 不得早于所用价格的可得时刻」，
对 `fill_ts` 是**真的约束**（本单已改为 `matched_at_ms`，恒 ≥ `data_available_at`）；
对 `entry_ts` 只能理解为「**不得把它当成成交时刻**」——本单的做法是：
把真实成交时刻单独落库（`matched_at_ms` / `fill_ts`），`entry_ts` 只标价格时点。

改之前的账本正好是反例（`out/01_baseline_times.json`，唯一未平仓持仓 #4）：

| 时刻 | 值 |
|---|---|
| 价格事件（`bar_open`） | 2026-10-27T16:15:00Z |
| 该 bar 收盘 | 16:29:59.999Z |
| `fill_ts`（记录时刻） | **16:30:14.728Z**（比收盘还晚 14.7 秒 ⇒ 确实是闭 bar 后回读） |
| `entry_ts` | 16:15:00Z ⇒ **比真实处理早 15 分 14.7 秒** |
| `reality_fill` | NULL（不是当场成交） |

另外 3 笔作废成交的 `fill_ts − bar_open` 都是 **1.0 分钟**，同样是把 bar 开盘当成交时刻。

---

## 3. 三个时刻的定义（交付口径）

| 时刻 | 落库字段 | 含义 | 谁生成 |
|---|---|---|---|
| 价格事件时刻 | `entry_ts` / `bar_open_time` | 执行那根 15m bar 的 `open_time` | 交易所 K 线 |
| 数据可得时刻 | `data_available_at` | 该行情在库里可被读到的时刻 | 采集器 `received_at` |
| 读取/决策时刻 | `read_at_ms` | 我们读到这一行那一刻的挂钟 | 本进程 |
| 实际执行时刻 | `matched_at_ms` / `fill_ts` / `paper_order.ts` | 撮合完成那一刻的挂钟 | 本进程 |

不变式（本单在代码里构造 + 在隔离里断言）：

```
data_available_at <= read_at_ms <= matched_at_ms == fill_ts == paper_order.ts
entry_ts（价格事件时刻）独立存在，必然早于以上三者，不得当作成交时刻
```

---

## 4. 真实时钟边界：逐次读取时间线（`out/03_boundary.json`）

这是本单最硬的一条。**全程真实时钟**、生产两段式（信号时刻登记挂单 → 执行点推进撮合）、
`origin="live"`、`WX_REALITY_FILL_MODE` 未设（走生产默认 `realtime_only`）。
账本用生产 3a 副本，行情库 `mode=ro`。

### 4.1 时序

| 事件 | 时刻（UTC） | 相对执行点 |
|---|---|---|
| 信号确认 bar 收盘（`event_time` = S） | 02:30:00.000 | −15 分 |
| 登记挂单（真实挂钟） | 02:30:01.680 | −14 分 58 秒 |
| **计划执行点** T0 | **02:45:00.000** | 0 |
| 执行点后首次采样（行还在旧那根） | 02:45:00.780 | +0.78 秒 |
| **实时行开始缺失** | 02:45:02.09 | +2.09 秒 |
| **实时行重新出现**（`received_at`） | 02:45:13.268 | +13.27 秒 |
| 读到该行（`read_at`） | 02:45:14.616 | +14.62 秒 |
| **撮合完成**（`matched_at`） | **02:45:14.660** | **+14.66 秒** |
| 执行 bar 收盘 | 03:00:00.000 | +15 分 |

### 4.2 逐次采样（共 758 个样本，间隔 ~1.2 秒）

| 阶段 | 样本序号 | 实时行状态 | 引擎决策 |
|---|---|---|---|
| 执行点前 | 1 – 746 | 有行，`open_time=02:30:00Z`、`is_closed=0` | 不推进（时钟未到） |
| 执行点 +0.78 秒 | 747 | 有行，仍是 02:30 那根 | `still_pending=1`（等） |
| **执行点 +2.09 ~ +13.33 秒** | **748 – 757** | **整行不存在**（10 个样本） | **`still_pending=1`（等），无一次拒单** |
| 执行点 +14.59 秒 | 758 | 有行，`open_time=02:45:00Z`、`is_closed=0`、`open=2357.25` | **`filled=1`** |

**这就是工单说的「执行点后 1.5 秒实时行情行可能为空」**——本单实测空窗
**约 11.3 秒**（比上一单测到的 3.4 秒长；不同时刻采集器节奏不同，不是固定值）。
空窗期间引擎一律「等待挂单」，**没有一次拒单、没有一次回填**。

### 4.3 账本核对（隔离副本 `paper_3a.sqlite3`，position_id=5）

| 字段 | 值 |
|---|---|
| `entry_ts`（价格事件） | 2026-10-28T02:45:00Z |
| `data_available_at` | 02:45:13.268Z |
| `read_at_ms` | 02:45:14.616Z |
| `matched_at_ms` | **02:45:14.660Z** |
| `fill_ts` | **02:45:14.660Z**（= `matched_at_ms`） |
| `paper_order.ts` | 02:45:14.660Z（= `matched_at_ms`） |
| `raw_price` / `fill_price` | 2357.25 / 2358.43（= ×1.0005，5bps 滑点口径未改） |
| `reality_fill` / `fill_source` | 1 / `realtime_15m_open` |
| `market_source` | `…realtime_klines_15m.open\|reality=1\|fill_source=realtime_15m_open\|data_avail_ms=1793155513268\|read_ms=1793155514616` |

### 4.4 18 项断言（`checks` 全 true）

成交一次 / `reality_fill=1` / 取数路径为实时 / 三个时刻都在 / `fill_ts=matched` /
`order.ts=matched` / `data_avail ≤ read` / `read ≤ matched` / `matched ≥ data_avail` /
`entry_ts` 等于执行 bar 开盘 / `entry_ts ≤ matched` / 撮合在执行点后 60 秒内 /
挂单终态 filled / 首次拿到执行 bar 在 60 秒内 / **缺行期间无一次拒单**。

### 4.5 同一根 bar 的 open 在 bar 内恒定

执行点后 20 秒复读同一根：`open` 仍是 `2357.25`、`open_time` 仍是 02:45:00Z
⇒ 用它当成交价不是「抓了某一瞬间的报价」。

### 4.6 重载**之后**再跑一次（`out/07_boundary_after.json`）

同一条流程在**新实例已加载**的状态下再跑一遍（03:00 信号 → 03:15 执行点），
17 项断言全 PASS：

| 时刻 | 值 |
|---|---|
| 价格事件（`entry_ts`） | 03:15:00.000Z |
| 数据可得（`data_available_at`） | 03:15:07.978Z |
| 读取（`read_at_ms`） | 03:15:09.516Z |
| **撮合完成**（`matched_at_ms` = `fill_ts` = `order.ts`） | **03:15:09.559Z** |
| 撮合比执行点晚 | **9,559 ms** |

空窗 14 个样本（约 12 秒），同样**全部是「等待」，无一次拒单**。

两次独立实测对照：

| | 重载前（03） | 重载后（07） |
|---|---|---|
| 执行点 → 数据可得 | 13.27 秒 | 7.98 秒 |
| 执行点 → 撮合 | **14.66 秒** | **9.56 秒** |
| 空窗样本数 | 10（≈11.3 秒） | 14（≈12 秒） |
| 断言 | 18/18 | 17/17 |

> 延迟随采集器节奏波动（14.7 秒 vs 9.6 秒），但**量级稳定在秒级**，
> 相比改之前的「15 分 1.5 秒 ~ 15 分 8 秒」差 3 个数量级。


---

## 5. 负向用例：新增判据真的会拦住（`out/04_negative.json`）

如果只跑正向，两条新校验永远不触发，等于装饰。所以构造**会失败**的条件。

构造方式：**合成行情库**（只复制生产库 schema、零数据），在 `realtime_klines` 里插一行，
`received_at` 按用例给值。**明确标注：这些是合成行，不是真实行情**——
真实行情的正向证据在 §4。

| 用例 | 构造 | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| **N0 正向对照** | `received_at = now−5s` | 成交 | 成交 | PASS |
| N1 | `received_at = NULL` | `realtime_fill_without_available_time` | 同 | PASS |
| N2 | `received_at = now+3600s` | `data_available_after_read_no_lookahead` | 同 | PASS |
| N3 | 实时行已是更晚一根 | `fill_point_passed_no_backfill` | 同 | PASS |
| N4 | 实时表无该标的、也无闭 bar | `pending_future_fill`（等，不伪造） | 同 | PASS |

**N0 是关键**：同一套合成构造下判据不误杀 ⇒ N1/N2 的拒绝不是「构造本身有问题」。

---

## 5.2 重启幂等 / 有效期 / 停机不追补（`out/11_restart_idempotent.json`）

工单第 2 条要求「重启后不重复下单、不丢已有订单/持仓管理」「超过既有有效期不补成交」。
四条正反例，5/5 通过：

| 用例 | 构造 | 期望 | 实际 |
|---|---|---|---|
| **R1 重启不丢账** | 复制生产账本，跑一轮 `advance_pending`（模拟重启后第一轮） | 持仓/订单/成交/挂单/已处理 四表行数与内容**逐项不变** | 完全一致 |
| **R2 不重复下单** | 用已存在的 `signal_uid=test_probe_succ` 再开一次 | `duplicate_signal_uid`，且持仓数不变（4 → 4） | 同 |
| **R3 超有效期不补** | `legacy` + 信号时刻 3 小时前（滞后 3.0 小时 > 1 小时容忍窗） | `missed_fill_moment_no_backfill`（不补、不顺延） | 同，`lag_ms=9,645,021` |
| **R4 停机不追补** | `legacy` + 应成交 bar 已收盘且 `process_start_ms` 晚于它 | `fill_point_closed_before_process_start` | 同 |

> R4 的时序踩过一次坑，留在脚本注释里：第一次取 `event_time = now−40min`，
> 执行点落在**还没收盘**的那根 bar 上 ⇒ 引擎正确返回 `pending_future_fill`（等），
> 用例期望写错了。改成 `now−60min` 才让「bar 已收盘但滞后仍在 1 小时内」成立。
> **这是用例错，不是代码错**——但它说明「拒单码」必须按前置条件算准，不能凭印象写。


---

## 6. 零回归对照（`out/05_parity.json`）

同一批 `unittest`（4 个 suite）跑两遍：一遍用**已改**代码，一遍把 `paper_engine.py`
临时换回 `isolated/backup/paper_engine.py`（`bae9d934…`，已核等于冻结记录）。

| 同通过 | 同失败 | **回归** | 修好 |
|---|---|---|---|
| 34 | 1 | **0** | 0 |

唯一同失败是 `test_paper_live_01::test_03_isolated_end_to_end_trade_closure`
——A/B 两边都失败在同一条断言 ⇒ **既有失败，不是本单回归**。
`restore_ok = true`，恢复后哈希 `cbbde84a…`（与改后一致）。

---

## 7. 受控重载（`out/06_reload.json`）

纪律：**只**重启 Runtime + API；**不动** Worker、**不动**采集器；不用 `restart`；
不写 `live_run`、不重置 epoch、不改任何规则。

| 项 | 重载前 | 重载后 |
|---|---|---|
| Runtime pid | 77433（01:04:37Z 起） | **6195** |
| API pid | 77425 | **6188** |
| Worker | `[68775, 88794]` | `[68775, 88794]`（**不变**） |
| 采集器 | 42278 | 42278（**不变**） |
| `fill_policy` 键数 | 17 | **21** |
| 账本 `read_at_ms`/`matched_at_ms` 列 | 不存在 | **已存在**（加性迁移） |

15 项检查 **14 PASS / 1 FAIL**，唯一 FAIL 是 `universe_526`：
脚本等到 `status == running` 就立刻读，此时 `cycle_count=0`、`universe=0`
——**bootstrap 还没跑完**（与上一单同样现象）。bootstrap 完成后的复查见 §8。

历史持仓 `#4` 逐项相同、持仓总数不变、闸门仍 `allow_new_entries=true`。

---

## 8. 重载后 API 原文与加载身份（`out/10_api_after.json`）

内容级加载证据（不看 pid、不看哈希、不看报告自述）：

```
GET /api/v1/paper/accounts → quality.fill_policy  共 21 键
  timestamps_split = {price_event_at / data_available_at / read_at / matched_at / invariant}
  invariant = "data_available_at <= read_at <= matched_at = fill_ts；entry_ts 必然早于它们，不得当作成交时刻"
  fill_ts_semantics = "撮合完成时刻（matched_at_ms）。MAINLINE-01 前为「本轮循环起始时刻」…"
  on_lookahead_detected = reject_data_available_after_read_no_lookahead
  on_realtime_without_available_time = reject_realtime_fill_without_available_time
```

这四个键只有加载了新代码才可能存在——旧进程只会返回旧的 17 键（重载前实测就是 17）。
API 原始响应体全文见 `10_api_after.json` 的 `endpoints.*.raw`。

### 8.1 重载后运行身份（`10_api_after.json`，取样 03:10Z）

| 项 | 值 |
|---|---|
| Runtime pid | **6195**（`record_pid_alive=true`，`status=running`） |
| API pid | **6188** |
| `cycle_count` | 36（bootstrap 完成后持续增长） |
| `universe` | **526**（`bootstrap.ready=526`、`skipped=0`） |
| 健康 | `healthy` |
| `fill_policy` 键数 | **21** |

### 8.2 10 项内容级检查：全 PASS

`fill_policy` 有 `timestamps_split` / `fill_ts_semantics` / `on_lookahead_detected` /
`on_realtime_without_available_time`；`timestamps_split.invariant` 文本含
`data_available_at <= read_at <= matched_at`；`fill_ts_semantics` 明确写「撮合完成时刻」；
`effective_mode_runtime` 仍在；`record_pid_alive`、`status=running`、`universe=526`。

> 这 4 个新键**只有加载了新代码才可能存在**——重载前实测只有 17 键。
> 旧进程即使磁盘文件已改，也不会凭空多出这些键，所以这是内容级证据，不是自报。



---

## 9. 自然观察（`out/08_natural.json`）

重载后新实例（pid **6195**）观测 **20 分钟**（02:55:36Z – 03:15:29Z），40 个采样点，全程只读。
观测窗口**跨了 03:00 与 03:15 两个 15m 边界**，也跨了 bootstrap 完成时刻。

### 9.1 运行是活的（先证明这个，否则「零」没有意义）

| 项 | 首样本 02:55:36Z | 末样本 03:15:29Z |
|---|---|---|
| pid | 6195 | 6195（**全程未变，没重启**） |
| `cycle_count` | 0 | **107**（一直在扫） |
| `universe` | 0（bootstrap 中） | **526** |
| 状态 / 健康 | running / healthy | running / healthy |
| `watermark_1h` | None | **02:00:00Z**（03:00 收盘后推进） |
| `watermark_4h` | None | 2026-10-27T20:00:00Z（00:00 那根 04:00 才收盘） |

### 9.2 但业务侧一项都没有（如实记零）

| 项 | 首 | 末 | 说明 |
|---|---|---|---|
| 自然信号（`origin='live'`） | **0** | **0** | `live_signal` 只有 `historical_research` **289** 条 |
| 挂单 `pending` | 0 | 0 | — |
| 未平仓持仓 | 1 | 1 | 就是保留的 BTC 探针 #4，不是新开的 |
| 订单 `filled` / `rejected` | 4 / 11 | 4 / 11 | 全是历史行 |
| `reality_fill=1` 的持仓 | **0** | **0** | 没有任何一笔新口径成交 |
| `read_at_ms`/`matched_at_ms` 非空的行 | 0 | 0 | 同上 |

**最硬的一条**：`last_reject` 全程仍是
`order_id=22 / BTCUSDT / ts=2026-10-27T15:46:41Z / before_paper_epoch_no_backfill`
⇒ 这 20 分钟里**连一次「尝试开仓 → 被拒」都没发生**。
不是新逻辑挡住了什么，是**根本没有候选**。

### 9.3 零的归因（不猜，只列已证事实）

- 自然候选为 0 的既有归因（前序工单已定案）：FT-LOW4H 是**反转策略**
  （`BOX → SWEEP(须跌破下沿) → RETEST → 突破上沿`），单边行情里等不到跌破下沿。
- 本次观测与之不矛盾：`natural_live = 0` 说明**连候选生成这步都没到**，还没轮到撮合层判断。
- 所以「零成交」**不能**用来证明或否定新撮合逻辑——它证明的是「这段时间没有素材」。
  新逻辑的正反证据在 §4（真实时钟边界）、§5（负向）、§5.2（重启/有效期）、§11（账本不变式）。


---

## 10. 价格口径授权核对（工单第 3 条）

### 10.1 现在用的口径

`fill_price = open_of_the_15m_bar_at_or_after(order_request_ms + delay)`
—— 即「执行那根 15m bar 的**开盘价**」，从 `realtime_klines` 在 bar 刚开盘时读。

### 10.2 有没有明确授权

| 证据等级 | 事实 |
|---|---|
| **E1（有原文）** | `FILL_POLICY.defined_by = "work_2026-10-13"`；`approved_delay_rule_kept = true`；15 分钟延迟规则在生效视图 `signal_execution_delay_minutes: 15` 里有明确值 |
| **E1** | 滑点 `slippage_each_side: 0.0005`、手续费 `fee_each_side: 0.0005` 在生效视图里是显式值，本单未改 |
| **E3（本轮未找到批准原文）** | **「成交价取那根 bar 的 open」这一条本身，本轮没有找到用户批准的原始表述**；能查到的只是 `market_at_eligible_detail_open` 这个内部标识与 `defined_by=work_2026-10-13` |
| **E1** | `FILL_POLICY.ratification_status = "pending_human_ratification"` ⇒ **成交政策本身尚未获人工批准**（上一单已记录，本单未变） |

### 10.3 最小待决（只列，不擅自选）

| # | 需决定的事实/字段 | 若维持现状 | 若改 | 影响面 |
|---|---|---|---|---|
| **D1** | 成交价是否确认为「执行那根 15m bar 的 `open`」 | 本单与上一单的做法成立 | 需重新定义 `fill_price` 并回改 `FILL_POLICY` | 全部新成交的价格；历史成交不可比 |
| **D2** | 是否批准 `ratification_status`（当前 `pending_human_ratification`） | 严格口径下所有新成交只能算「未获批准」 | 需人工签署 | 成绩能否进正式统计 |
| **D3** | `fill_ts` 语义由「循环起始」改为「撮合完成」是否认可 | 本单已改（新行） | 回滚需换回 `attempt_ms` | 历史行与新行 `fill_ts` 语义不同（已用 `matched_at_ms` 列显式区分，可查） |

**这三条都不阻塞本单其余结论**：三时刻分离、前视保护、防回填、挂单等待、拒绝、
幂等、隔离与重载验证，都不依赖 D1/D2/D3 的选择。

---

## 11. 账本级不变式扫描（`out/12_ledger_invariant.json`）

前面各节证明的是「这一笔」。本节把**所有账本的所有成交行**扫一遍（96 个库），
逐行判三条不变式，有违反就全列出来：

| 不变式 | 内容 |
|---|---|
| I1 | `fill_ts >= data_available_at` |
| I2 | `data_available_at <= read_at_ms <= matched_at_ms` |
| I3 | `fill_ts == matched_at_ms` 且 `paper_order.ts == matched_at_ms` |

结果：**新口径成交行 3 笔，违反 0**（3 笔 = 03 的边界、04 的 N0 正向对照、07 的边界）。
生产账本 `paper_3a` 的 7 笔成交全是历史行（三列为 NULL），按设计不参与判定，
不计为违反——但它们的 `fill_ts − bar_open` 分别是 1.0 / 1.0 / 1.0 / 15.25 分钟，
见 §2.5，那是改之前的状态，本单**没有改写**它们。

---

## 12. 未验证 / 待裁决（不擅自改）

| 项 | 事实 | 影响 | 最小待决 |
|---|---|---|---|
| 自然候选/成交为 0 | 见 §9 | 无法用自然样本证明新逻辑 | 需要真实行情给出候选；不人为造单 |
| 实时行空窗时长不固定 | 本单 11.3 秒，上一单 3.4 秒 | 撮合延迟随采集器节奏波动 | 采集器 `DELETE→INSERT` 是否改单事务 upsert（**不属本单**） |
| 挂单路径 `is_replay` 恒 True | `paper_engine.py` 里 `origin in ('replay','test') or pending_id is not None` ⇒ 挂单推进时那道 `earliest_fill_before_first_request_clock` 从不执行 | 与注释矛盾；危害有限（`bar_open ≥ earliest_fill ≥ first_req` 已成立） | 补校验还是改注释（**未改**） |
| 代码层仍无 `bar_close <= attempt_ms` 校验 | 只信 `is_closed` | 潜在缺口 | 是否补（**未改**） |
| `realtime_klines` 只覆盖 binance um | 522/528 | 6 个标的会走「无实时行情」路径 | 是否扩采集（不属本单） |
| 孤儿 Worker pid 88794 | 10-24 起，指向隔离临时库 | 不属本单范围 | 只列出，**未动** |

---

## 13. 目录与复现

```
evidence/MAINLINE-01-WORK/
├── README.md                  ← 本文件
├── scripts/
│   ├── 00 冻结快照（内联，产物 out/00_freeze_before.json）
│   ├── 01_read_baseline.py    改前账本时刻取证（只读）
│   ├── 03_boundary.py         真实时钟边界：逐次读取时间线（约 21 分钟）
│   ├── 04_negative.py         新增判据负向用例（合成行情库）
│   ├── _iso_case.py           隔离单例执行器（子进程）
│   ├── 05_parity.py           零回归 A/B 对照
│   ├── 06_reload.py           受控重载
│   ├── 07_boundary_after.py   重载后再跑一次真实时钟边界
│   ├── 08_natural.py          自然观察（只读）
│   ├── 09_changeset.py        变更清单 + diff + 改后冻结快照
│   ├── 10_api_after.py        重载后 API 原文留证
│   ├── 11_restart_idempotent.py 重启不丢账/不重复下单/超期不补/停机不追补
│   └── 12_ledger_invariant.py 全账本时刻链不变式扫描
├── out/                       所有原始输出
└── isolated/
    ├── backup/                改前三个文件的原样副本（基线）
    ├── boundary/              03 的隔离账本
    ├── boundary_after/        07 的隔离账本
    ├── n0_… n4_…              04 各用例的隔离账本与合成行情库
    └── r1_… r4_…              11 各用例的隔离账本
```

复现（注意解释器：`05`/`06` 需要 `psutil`，用项目 venv；其余纯标准库）：

```sh
cd evidence/MAINLINE-01-WORK/scripts
PY=/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3
VENV=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

$PY 01_read_baseline.py
$PY 04_negative.py
$PY 11_restart_idempotent.py
$VENV 05_parity.py
$PY 09_changeset.py
$PY 10_api_after.py
$PY 12_ledger_invariant.py

# 边界（需等 15 分钟边界，各约 21 分钟）
$PY 03_boundary.py ETHUSDT
$PY 07_boundary_after.py ETHUSDT

# 重载（会重启 Runtime + API；不动 Worker/采集器）
$VENV 06_reload.py
$PY 08_natural.py --minutes 20 --interval 30
```

**回滚**：把 `isolated/backup/paper_engine.py`（`bae9d934…`）与
`isolated/backup/live_store.py`（`ba85a66d…`）复制回 `框架/backend/`，再按 §7 的纪律重载一次。
两列是**加性**的，回滚代码后它们仍留在库里、值为 NULL，不影响旧代码运行。
