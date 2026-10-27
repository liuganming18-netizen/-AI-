# 30｜SIGNAL-HISTORY-FIX-02 收口回执

- 工单：`SIGNAL-HISTORY-FIX-02｜修复生效、历史入口与信号一致性`
- 证据：`evidence/SIGNAL-HISTORY-FIX-02/`（主报告 `SIGNAL-HISTORY-FIX-02_主报告.md`）
- 状态：**已收口**（代码修复已落到运行环境，四项验证 PASS）
- 真实交易：**保持禁用**（无真实交易路径；纸面模拟恢复为旧进程原状）

## 六条要点逐条回执

| 工单要点 | 回执 |
|---|---|
| 一、先复用现有方案 | 盘点四层存储（热库/冷层 parquet/官方归档 zip/空 `cold_archive`）+ 既有路由 `MarketDataRepository.from_archive` + 既有 `service_manager.py`。**未新建引擎/管道**。**推翻**「全部历史只有 47 天」：热库 47.5 天只是滚动保留窗口 |
| 二、让修复真正生效 | 重启前 `process_older_than_code=true`（进程落后代码 **16,924 s**）⇒ 跑的是修复前镜像。受控重启后 `running`，四项验证全 **通过**。采集器 pid 未变、账本 0 新增 |
| 三、半年历史从哪读 | 复用既有路由建 180 天隔离库：**`covers_180d=505` / `listed_late=21` / `storage_missing=0` / `no_data_at_all=0`**；每标的 1h=4320、4h=1079。不下载、不伪造缺口、不改箱体窗口 |
| 四、ATR 口径 | 规范第 17 行明文「连续递推 + 至少预热 200 根」，回测符合、旧实时不符合 ⇒ **`IMPLEMENTATION_DEVIATION`**，按规范修复，`params_changed=false`。增量实现与全前缀序列 **`max_abs_diff=0.0`** |
| 五、衔接验证 | **4 个候选全部覆盖**，三场景（连续回放/初始化后追加/中途重启）**全中**、终态一致。全池 526 只做静态资格检查，**不外推** |
| 六、真实归因 | 无自然成交链路：新增候选 `ZEREBROUSDT 10-14T13:00Z` 属预热期按纪律丢弃，`order_id=null`。**隔离回放不冒充自然成交** |

## 必须记住的三个硬结论

1. **ATR 修复让原来 4 个「已知历史候选」全部消失**（`VELVETUSDT/SUSDT/WALUSDT/BBUSDT`），另出现 1 个新的 `ZEREBROUSDT`。生产同窗口只换 ATR 提供者：**旧 4 个（与快照一致）→ 新 1 个**；隔离库同口径身份比对 `identical=true`。
   ⇒ 上一单「历史回放能出 4 个信号」的乐观结论**不成立**，那 4 个是「42 根重算 + 无预热」的产物。
2. **200 根预热吃掉 800h（33.3 天）**，1160h 预热窗口里真正留给箱体演化的只剩 **360h（15 天）**。这是冻结规范的设计（`atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours`），**不是 bug**；但它意味着**修复后候选速率只会更慢**。
3. **`candidate_id` 不能当跨运行身份**：`cand_{event_id}_{close_time_ms}`，`event_id` 是运行内计数器。稳定身份 = `(symbol, available_at_ms)`。

## 踩过的坑（下次别再犯）

- **`ENABLE_PAPER_RUNTIME=1` 是 runtime 的启动条件**：旧进程 cmdline 无 `--enable-paper`，靠这个环境变量拉起。不带它 `restart` 会把 runtime 置为 `disabled_safe_mode`（我第一次就把在跑的进程弄停了）。`status --json` 里的 `paper_enabled=false` 是 **status 命令自己的默认值**，不是在跑进程的参数。
- **`_WilderATR` 以装载窗口首根为原点** ⇒ 窗口锚点一变，200 根预热后的建档起点就平移。做衔接验证**必须共用生产同一锚点**（热库 4h 首根），否则制造假阴性。
- **旧口径 = 旧 ATR 提供者 + `atr_warmup_bars=0`**，两者是同一缺陷的两面。只换 ATR 不放开预热会造出混合体。
- **断言也会写错**：V1 曾把 4h 收盘时刻与开盘时刻直接比（得 -1 根假阴性）；V4 取错键名。**修正的是断言，不是系统**；修正后用 `--verify-only` 不重启复核。
- 隔离库 `candle_versions` 没有 `data_version` / `source_id` 列 ⇒ 读它用 `SELECT *`。

## 当前系统状态（收口时）

- runtime：`running`，新进程晚于代码（`process_older_than_code=false`）
- `atr_caliber`：`n_engines=526`、`n_warmed=525`、`n_not_warmed=1`、`atr_bars_seen` 140~268、`last_atr_invalid=0`
- `history_candidates_discarded=1`（= 漏斗 `history.CANDIDATE`）
- `candidates_since_start=0`、`orders_routed_total=0`、`open_positions=0`、账本 0 新增
- 采集器 pid 42278 未变

## 未解决 / 需人决策

- **候选速率**：修复后更慢，攒可统计样本需数月到数年，或改冻结规则（须走裁决流程）。本单**未改门槛**。
- **`max_holding_hours=240` 仍在生效视图配置里**（进 `view_sha256` 与设计身份），实际不执行；**谁若「照参数实现」会把 10 天强平重新引入**。
