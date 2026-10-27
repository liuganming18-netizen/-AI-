# SIGNAL-HISTORY-FIX-02｜修复生效、历史入口与信号一致性

- 工单：`SIGNAL-HISTORY-FIX-02`
- 项目：`/Users/boos/Desktop/威科夫因子共振趋势`
- 证据目录：`evidence/SIGNAL-HISTORY-FIX-02/`
- 冻结规则：`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md`
  sha256 `1f1167de35412cb7d1d3608d8069c4108324f7641de7c369bf94882f1679a8ab`（13,315 B）
- 策略：`WX_EVENT_TREND_V0_1`（`box_bars_4h=42`、`er_threshold=0.30`、`2A~8A`、`atr_period_4h=14`、`atr_warmup_bars=200`）**一个参数都没改**

---

## 0. 一句话结论

**代码修复已落到运行环境并四项验证通过；「历史只有 47 天」这个上一单结论被推翻——冷层 parquet 完整覆盖 180 天（505/526 标的），存储缺失 0。**

**同时必须写清三件不能含糊的事：**

1. **ATR 修复让原来 4 个「已知历史候选」全部消失**，另出现 1 个新的（`ZEREBROUSDT`）。这 4 个候选是「42 根重算 ATR + 无 200 根预热」的产物，按冻结规范**本来就不该成立**。它们不是「信号丢了」，是「假信号被规范排除」。
2. **第一次重启把 runtime 降级成 `disabled_safe_mode`（我搞错了启动条件）**，第二次按旧进程真实启动条件恢复成功。这段如实记录在 §5，不粉饰。
3. **全池 526 的「资格/水位/状态」是静态检查**；行为验证只有 4 个候选 + 80 标的逐时点 ATR 对照。**不把这两者互相外推**。

---

## 1. 先复用现有方案（工单「一」）

盘点结果：项目**已有**四层行情存储 + 一套读路由 + 一套启停管理，本单**没有新建引擎、没有新建数据管道**。

| 层 | 位置 | 实测覆盖 | 策略能否读 |
|---|---|---|---|
| 热库 | `数据/市场数据/db/market_v2.sqlite3`（11.78 GB） | 1h **1139h（≈47.5 天）**、4h 1068h；526 标的；每标的 1h 均 1105 根 | 是（`PoolMarketWatcher` 唯一生产入口） |
| 冷层 parquet | `数据/市场数据/parquet/{candles,candle_heads,sources}`（32,920 分片） | 1h **11,158,237 行**、4h **2,761,363 行**，年度分片 **2019~2026** | 是（既有路由 `MarketDataRepository.from_archive`，`candles()` 每次 ≤2000 根） |
| 官方归档 zip | `数据/raw/{1h,4h,15m}/<SYMBOL>/*.zip` + `.CHECKSUM` | 1h 21,095 个 / 4h 19,425 个，**2019-12 ~ 2026-10** | 否（需解压导入；本单**不下载、不解压**） |
| `cold_archive/` | 空目录 | — | — |

**关键纠正**：上一单「数据不足 180 天」**只在热库口径下成立**。热库是滚动保留窗口，不是全部历史。把热库的 47 天当成「全部历史只有 47 天」是**外推错误**。

### 180 天读取目标（复用既有路由，不下载新数据）

`code/01_history_180d.py` 用既有 `MarketDataRepository.from_archive(hot_db, archive_root)` 拼出隔离库
`out/180d_market.sqlite3`（schema 与生产同构），保留**来源、去重（按 `open_time`）、升序、`is_closed=1` 且 `close_time<=end`**。

实测（`out/01_180天覆盖.json`，`downloaded_anything=false`、`read_only_on_production=true`）：

| 判据 | 1h | 4h |
|---|---|---|
| `symbols` | 526 | 526 |
| **`covers_180d`** | **505** | **505** |
| `listed_late`（上市不足 180 天） | **21** | **21** |
| **`storage_missing`（存储缺失）** | **0** | **0** |
| `no_data_at_all` | 0 | 0 |

- 每标的 1h **4320 根**、4h **1079 根**（正好 180 天），`candles` 视图 = heads = **2,797,325 行**，构建 69.4 s。
- **上市不足 180 天与存储缺失分开统计**：21 个标的是冷层全历史最早一根就晚于请求起点（真实上市晚），不是缺存储。
- **没有改箱体窗口**（`box_bars_4h=42` 不变），**没有伪造缺口**。

---

## 2. ATR 口径：属实现偏差，已按规范修复，参数未动（工单「四」）

### 2.1 规范明文，没有冲突

规范第 17 行：

> ATR：4h Wilder ATR(14)，前 14 个 TR 的算术平均作为初值，随后递推；TR=max(high-low, abs(high-prev_close), abs(low-prev_close))。首根没有前收盘时 TR=high-low。**至少预热 200 根完整 4h K 线。**

规范第 32 行：

> 每个 4h 收盘检查最近 42 根 bar：同时满足 2 <= (H-L)/A <= 8、ER <= 0.30 时建档。数据必须连续，ATR 有效。

**回测**（`runner.py:225-230`）用全历史连续序列 → 符合规范。
**旧实时**（`unified_runtime.py` 修复前）**每根 4h 只取最近 42 根重算 ATR** → Wilder 递推**每根被重置**，且**完全没有 200 根预热**。

⇒ 判定 `IMPLEMENTATION_DEVIATION`（不是规范冲突），`params_changed=false`。**没有「选一个更容易出信号的版本」这个问题——规范只给了一个定义。**

### 2.2 修复内容（`框架/backend/unified_runtime.py`）

| 项 | 修复前 | 修复后 |
|---|---|---|
| ATR | 每根取最近 42 根重算（递推每根重置） | 新增 `_WilderATR`，按装载窗口**逐根连续递推** |
| 200 根预热 | **无** | `atr_bars_seen < atr_warmup_bars(200)` ⇒ **不建档**，并记 `last_box_skipped_warmup_ms` |
| 文件 | `b3dd3744…` / 80,221 B（FIX-01 收口登记） | `9e2572e0…` / 89,831 B |
| 参数 | — | **`EventTrendConfig` 一个字段都没改** |

> 说明：本轮动手前未做字节快照（该文件在 git 里是未跟踪状态）。修复前基线取 FIX-01 收口台账登记的 `b3dd3744…`（80,221 B）；本轮新增 9,610 B，与新增代码量相符。改后文件已快照到 `out/unified_runtime_post_fix.py`。

### 2.3 修复前后影响证据（`out/02_ATR口径对照.json`）

- **A. 规范 vs 实现**：`IMPLEMENTATION_DEVIATION`
- **B. 增量等价性**：`_WilderATR.update()` 逐根 vs `compute_wilder_atr_series(全前缀)[-1]`
  → 5 个标的 `max_abs_diff = 0.0`，`all_identical = True`（**修复本身没写错**）
- **C. 同前缀三口径差**（**80 标的** × 全部 4h 时点，共 **82,617** 个时点）：

| 指标 | 值 |
|---|---|
| ATR 相对差 中位 | **0.95%** |
| 相对差 p95 | **4.19%** |
| 相对差 最大 | **140.5%** |
| 相对差 >1% 的时点 | 大量（见 CSV） |
| 宽度闸（2A~8A）翻转 | **1,020** |
| 建档「旧有新建档没有」 | **9,668** |
| 其中**因缺 200 根预热门槛** | **9,175** |
| 建档「新有旧没有」 | 280 |

- **D. 候选级**（**全池 526**，生产同款 1160h 预热窗口，只换 ATR 提供者）：**17 → 0**

> **覆盖范围必须分开写**：C 段是 **80 标的**逐时点口径差；D 段是 **全池 526** 候选级影响。**不把 80 标的外推成 526**。

---

## 3. 4 个已知历史候选：全部覆盖，三场景全中（工单「五」）

### 3.1 先解决「候选身份拿不到」的问题

`bootstrap` 里 `discarded += len(eng.process_market(...))`（`unified_runtime.py:590`）**只计数、不保留身份** ⇒ 运行态只能给「4 个」这个数，给不出是哪 4 个。必须**同口径、同窗口重放**把身份取回来。

同时发现：`candidate_id = f"cand_{event.event_id}_{close_time_ms}"`，`event_id` 是**运行内计数器** ⇒ **`candidate_id` 跨运行不可比**。跨运行稳定身份是 `(symbol, available_at_ms)`。`event_id` 仍照记，用于**同一次运行内**的「候选→订单→成交→复盘」关联。

### 3.2 生产同窗口、只换 ATR 提供者（`out/03_A1_生产库复现候选.json`）

| 口径 | 候选数 | 与生产快照 |
|---|---|---|
| **旧 ATR**（= 生产进程实际在跑的代码） | **4** | `snapshot_n=4`，**一致** |
| **新 ATR**（= 修复后） | **1** | — |

- 旧口径 4 个：`VELVETUSDT 10-02T15:00Z`、`SUSDT 10-06T06:00Z`、`WALUSDT 10-10T11:00Z`、`BBUSDT 10-11T07:00Z`
- 新口径 1 个：`ZEREBROUSDT 10-14T13:00Z`
- **`only_in_legacy` = 上述 4 个全部**；**`only_in_fixed` = ZEREBROUSDT**

> `legacy_matches_snapshot=true` 说明**复现口径与生产一致**；此时新旧之差**只能**归因到 ATR 口径修复。

### 3.3 隔离库同口径身份比对（`out/03_A2_隔离库复现候选.json`）

隔离库用**生产实际生效的预热起点**（热库 4h 首根 `2026-09-09T20:00Z`）和**生产预热截止**做锚点：

- 旧口径：隔离库 **4** 个 vs 生产 **4** 个 → **`identical = true`，`only_prod=[]`，`only_iso=[]`**
- 新口径：**1** 个（`ZEREBROUSDT`）

> 为什么锚点必须共用：`_WilderATR` 以装载窗口首根为原点递推，而 200 根预热门槛会把「可建档起点」整体后移 800h ⇒ **窗口锚点一变，建档起点就变**。若按「候选时刻−1160h」逐候选平移锚点，会造出假阴性（实测第一版正是如此，已修正并在脚本 docstring 里写明原因）。

### 3.4 三场景衔接（`out/03_C_候选衔接验证.json` / `.csv`）

驱动方式三分（全部走既有 `RealSignalEngine.process_market`，不另建引擎）：

- **S1 连续回放**：从锚点一路喂到「候选+24h」
- **S2 初始化后追加**：先回放到「候选−72h」，再继续喂到「候选+24h」
- **S3 中途重启**：回放到「候选−72h」→ 喂到「候选−12h」→ **新引擎全量重放**到「候选−12h」→ 再喂到「候选+24h」
  （`export_state` 声明 `restore_supported=False`、`recovery_method="bootstrap_full_replay"` ⇒ 「重启」的真实语义就是**重放**，不是读断点续跑）

| 候选 | 口径 | S1 | S2 | S3 | 终态 | 旧口径可见 | 修复后可见 |
|---|---|---|---|---|---|---|---|
| VELVETUSDT 10-02T15:00Z | legacy | ✓ | ✓ | ✓ | IDLE/IDLE/IDLE | ✓ | ✗ |
| SUSDT 10-06T06:00Z | legacy | ✓ | ✓ | ✓ | IDLE/IDLE/IDLE | ✓ | ✗ |
| WALUSDT 10-10T11:00Z | legacy | ✓ | ✓ | ✓ | IDLE/IDLE/IDLE | ✓ | ✗ |
| BBUSDT 10-11T07:00Z | legacy | ✓ | ✓ | ✓ | IDLE/IDLE/IDLE | ✓ | ✗ |
| ZEREBROUSDT 10-14T13:00Z | fixed | ✓ | ✓ | ✓ | IDLE/IDLE/IDLE | ✗ | ✓ |

- **4 个候选全部覆盖，没有一个被单一起点剔除**
- `all_three_found_for_all = True`、`states_all_equal_for_all = True`
- `legacy_targets_visible_under_legacy = True`、**`legacy_targets_absent_under_fixed = True`**、`fixed_targets_visible_under_fixed = True`
- 即：**候选集合对「驱动方式」不敏感，对「ATR 口径」敏感** —— 这正是修复应当产生的效果

> 一个如实记录的坑：第一版 `drive(legacy=True)` 只换了 ATR 提供者、**没同时把 `atr_warmup_bars` 归零**，造出「旧 ATR + 新预热门槛」的混合体，4 个候选一个都复现不出。修正后全中。**这是证据脚本的错，不是系统的错。**

---

## 4. 全池检查 vs 代表性行为测试：覆盖范围分开写（工单「五」末段）

| 检查 | 覆盖 | 结论 |
|---|---|---|
| 数据资格 / 水位 / 状态 | **全池 526**（`out/03_B_全池资格.csv`，静态 SQL + 生产快照） | **`ok=526`**，全部合格 |
| ATR 口径差逐时点 | **80 标的**（02 C 段） | 中位 0.95% / p95 4.19% / max 140.5% |
| 候选级影响 | **全池 526**（02 D 段） | 17 → 0 |
| 衔接行为（三场景） | **4 个历史候选 + 1 个新增候选**（03 C 段） | 三场景全中、终态一致 |

**不把 4 个候选或 80 标的的结果外推成「526 全部通过」。**

---

## 5. 受控重启与修复生效（工单「二」）——含一次失败

### 5.1 重启前：进程比代码旧 4.7 小时（「文件已改 ≠ 运行已修复」的机械证据）

`out/04_重启前体检.json`（`2026-10-24T14:33Z`）：

- runtime pid **59109**，`create_time = 2026-10-24T09:49:02Z`
- `unified_runtime.py` mtime `2026-10-24T14:10:59Z`
- **`process_older_than_code = true`，落后 16,924.4 s**
- API `signal_engine` 键里**没有** `atr_caliber` / `watermark_check`
- `history_candidates_discarded = 4`
- 安全闸：任务队列非终态 **0**、持仓 **0**、挂单 **0**、采集器 `managed_by_this_instance=false` ⇒ 全通过

旧进程真实命令行：`unified_runtime.py --arm 3a --symbol all --interval 2.0`（**无** `--enable-paper`）。

### 5.2 第一次重启：失败，我把 runtime 降级成 `disabled_safe_mode`

不带 `ENABLE_PAPER_RUNTIME=1` 时，`ServiceConfig.enable_paper` 取默认 `False`，`start()` 把 runtime 置为：

```
status = disabled_safe_mode
exit_reason = 普通UI/API启动模式下交易链默认禁用，不开放自动交易
```

**这是我把「更安全」和「把原来在跑的进程弄停了」搞混了。** 我先前读到的 `config_identity.paper_enabled=false` 是 `status` 命令自己构造的 ServiceConfig 默认值，**不是**在跑进程的参数——这是误判，记录在此。

### 5.3 第二次重启：按旧进程真实启动条件恢复，成功

`ENABLE_PAPER_RUNTIME=1 service_manager.py restart --arm 3a --symbol all`
（`ENABLE_PAPER_RUNTIME=1` 是**恢复**旧进程的启动条件，不是新开能力；纸面模拟 ≠ 真实交易，真实交易链已封存在 `_legacy_archive_v31/`）

- `rc=0`，`waited=30.6s`，`runtime_status=running`
- `has_atr_caliber=true`、`has_watermark_check=true`
- **采集器 pid 42278 前后未变**（`collector_untouched=true`）
- **账本新增文件 0、变更文件 0**

### 5.4 四项验证（`out/04b_验证复核.json`，全 PASS）

| 项 | 结论 | 机械依据 |
|---|---|---|
| **① 水位与实际处理的 K 线一致** | **通过** | `processed_1h_close_max == processed_4h_close_max`（`1792843199999 == 1792843199999`）⇒ 最后一根已处理 4h 收盘之前的 1h **全部**喂进去了；`processed_4h_max_open == global_4h`（`1792828800000`）；`lag_1h_bars=2 ≤ 4`（结构性：4h 收盘之间最多夹 4 根 1h，不是漏处理） |
| **② 无漏处理 / 重复候选 / 重复订单** | **通过** | 漏斗 `history.CANDIDATE=1` == `bootstrap.history_candidates_discarded=1`；`candidates_since_start=0`；`orders_routed_total=0`；账本重复订单 **0** |
| **③ 历史候选不补成当前订单** | **通过** | `history_candidates_discarded=1` 而 `orders_routed_total=0`、账本新增 **0** 行、`open_positions=0` |
| **④ 新增诊断在 API 与用户入口可见** | **通过** | API `atr_caliber` / `watermark_check` 均在；前端源码 `daemon-atr-caliber`（`PositionPanel.tsx`）+ `atrCaliber`（`liveTrading.ts`）均在 |

> 断言修正记录：第一版 V1 把 4h **收盘**时刻与 **开盘**时刻直接比，得到 `-1` 根的假阴性；V4 取错键名（`ui_has_atr_caliber` → `ui_source_has_atr_caliber`）。**修正的是我的断言，不是系统行为**；修正后用 `--verify-only` 不重启复核。

### 5.5 新增诊断字段（用户入口可见）

- `signal_engine.atr_caliber`：`caliber` / `period=14` / `warmup_bars=200` / `n_engines=526` / **`n_warmed=525`** / **`n_not_warmed=1`** / `atr_bars_seen_min=140` / `max=268` / `last_atr_valid=526` / `last_atr_invalid=0` / `last_box_skipped_warmup_utc=2026-10-24T11:59:59Z`
- `signal_engine.watermark_check`：`global_1h/4h`、`processed_1h/4h_max`、`aligned_*`、`lag_*_bars`
- 前端：`PositionPanel` 新增 `data-testid="daemon-atr-caliber"` 一行，显示「4h Wilder ATR(14) 连续递推 · 预热 200 根 · 已达标 525/526 · 未达标 1」

> `n_warmed=525 / n_not_warmed=1` 本身是有意义的：**修复后 526 个标的里有 1 个在本次预热窗口内没攒够 200 根 4h**，此时它**不会建档**——这属于「还没到」，不是「没机会」。

---

## 6. 真实归因与身份关联（工单「六」）

### 6.1 候选 → 订单 → 成交 → 复盘

- **修复后新增的自然候选：`ZEREBROUSDT 2026-10-14T13:00:00Z`（1 个）**
- 但它产生于**预热回放期**，按纪律 `history_discarded_by_bootstrap` ⇒ `order_id = null`，**没有订单、没有成交**
- `candidates_since_start = 0`、`orders_routed_total = 0`、账本 0 新增
- ⇒ **本单没有任何「候选→订单→成交→复盘」的自然链路可给**。**隔离回放不冒充自然成交**，`out/03_C_候选衔接验证.csv` 里每行 `order_id` 为空、`order_reason` 如实写明

### 6.2 逐币归因（全池 526）

**`out/03_B_全池资格.json`：`eligibility = {ok: 526, insufficient_4h: 0, insufficient_1h: 0, no_data: 0}`**
⇒ 按「4h≥50 根、1h≥200 根」的资格判据，**526 个标的全部合格**。逐币阶段/实际值/阈值见 `out/03_B_全池资格.csv`；ATR 逐币差异见 `out/02_ATR逐币差异.csv`。

> 注意：**「资格合格」≠「能出信号」**。资格只证明数据够算，不证明策略条件成立。

### 6.3 3 个异常行情标的：已查明，**不是「市场无成交」**

| 标的 | 4h 总根数 | 零量平价根数 | 占比 | 最新 4h 是否零量平价 |
|---|---|---|---|---|
| `1000000BOBUSDT` | 1079 | **114** | **10.57%** | **是**（`volume=0`，`O=H=L=C=0.0096`） |
| `PROMPTUSDT` | 1079 | **114** | **10.57%** | **是**（`volume=0`，`O=H=L=C=0.02037`） |
| `PUMPBTCUSDT` | 1079 | **114** | **10.57%** | **是**（`volume=0`，`O=H=L=C=0.0371`） |

**三个标的的零量平价根数完全相同（114 根 ≈ 19 天），且都落在序列尾部、最新一根就是占位行。**
`prod_4h_all_zero_flat=False`、`prod_1h_all_zero_flat=False` ⇒ **不是全序列坏死**。

定性依据：`框架/backend/history_lifecycle.py` 记载「币安官方归档会产出**零量平价占位行**（`volume==0` 且 `O=H=L=C`）」。
**19 天连续同一价格 + 零成交额，是采集/占位的签名，不是市场事实。** 上一单把它们标为 `IDLE_DEAD_BARS`（4h 全零）只描述了**尾部窗口**的现象，**不能据此归因于「市场无成交」**。

⇒ 待办（不在本单范围）：这 3 个标的的尾部 114 根占位行需按 `history_lifecycle.py` 的零量行规则**从趋势输入中剔除**并回补真实数据；在此之前它们的 `ATR=0` 会让 `idle_breakdown` 恒判 `IDLE_DEAD_BARS`。明细见 `out/03_B_全池资格与异常标的.json` 的 `anomaly_symbols` 段（含 `sources` / `collector_rounds` / `collector_state` 与官方归档 zip 清单）。

---

## 7. 结构性结论（不是缺陷，需人决策）

1. **180 天数据不是问题**（505/526 完整覆盖，存储缺失 0）；**候选速率才是问题**。上一单算出的 ≈12 天/个在修复后**只会更慢**，因为 200 根预热吃掉了 800h（33.3 天），1160h 窗口里真正留给「箱体演化」的只剩 **360h（15 天）**。这是冻结规范本身的设计（`atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours`），**不是 bug**。
2. **4 个历史候选全部作废**，说明上一单「历史回放能出 4 个信号」的乐观结论**不成立**。真实速率要按修复后重估。
3. 想攒可统计样本仍需**数月到数年**（时间维度），或**改冻结规则**（须走裁决流程）。本单**没有改门槛**。

---

## 8. 交付物

| 类型 | 文件 |
|---|---|
| 脚本 | `code/00_common.py`、`01_history_180d.py`、`02_atr_parity.py`、`03_candidates_continuity.py`、`04_restart_verify.py` |
| 数据来源 | `out/01_历史来源盘点.json`、`out/01_180天覆盖.json`、`out/180d_market.sqlite3` |
| ATR | `out/02_ATR口径对照.json`、`out/02_ATR逐币差异.csv` |
| 候选 | `out/03_A1_生产库复现候选.json`、`out/03_A2_隔离库复现候选.json`、`out/03_C_候选衔接验证.json`、`out/03_C_候选衔接验证.csv` |
| 全池 | `out/03_B_全池资格与异常标的.json`、`out/03_B_全池资格.csv` |
| 重启 | `out/04_重启前体检.json`、`out/04_重启与验证.json`、`out/04b_验证复核.json` |
| 版本锚 | `out/unified_runtime_post_fix.py`（89,831 B，sha256 `9e2572e0…`） |

---

## 9. 边界遵守

- 不重跑 45 组实验 ✅（本单未触发任何实验）
- 不降低信号门槛 ✅（`EventTrendConfig` 零改动）
- 不覆盖旧证据 ✅（所有写入都在 `evidence/SIGNAL-HISTORY-FIX-02/`）
- 不下载新数据 ✅（`downloaded_anything=false`）
- 不修改箱体窗口 ✅（`box_bars_4h=42`）
- 真实交易保持禁用 ✅（无真实交易路径；纸面模拟恢复为旧进程原状）
