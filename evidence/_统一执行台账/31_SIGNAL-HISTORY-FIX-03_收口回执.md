# 31｜SIGNAL-HISTORY-FIX-03 收口回执

- 工单：`SIGNAL-HISTORY-FIX-03｜半年历史正式接入与小时线推进`
- 证据：`evidence/SIGNAL-HISTORY-FIX-03/`（主报告 `SIGNAL-HISTORY-FIX-03_主报告.md`、`MANIFEST.json`）
- 状态：**已收口**（历史接入 + 小时线推进两段验证 PASS；策略可交付性**仍未通过**）
- 真实交易：**保持禁用**（`unified_runtime.py` 内无任何真实下单符号，机械自证 7 项计数全 0）

## 六条要点逐条回执

| 工单要点 | 回执 |
|---|---|
| 一、半年历史接入生产初始化 | 新建 `history_loader.py`（冷层 parquet JOIN `candle_heads` + 热库 `candles`，同 key 热层优先）。全池 526：1h 冷 2,155,409 / 热 562,761 / 重叠 480,179 / 物化 609,045；4h 冷 538,609 / 热 142,418 / 重叠 121,904 / 物化 559,123。**`overlap_value_mismatch=0`、`missing_bars_total=0`、`downloaded_anything=false`、505/526 覆盖完整 180 天、21 个晚上市单列**。生产重启后 `bootstrap.data_source=merged_cold_hot` |
| 二、读取量与使用量分开记 | 读取 180 天；**ATR 用最多 790 根 4h**（连续递推）、**箱体/ER/突破量只用 1160h 声明窗口**（最多 289 根 4h、1,159 根 1h）。`atr_bars_seen`（含窗口前预热）与 `box_gate_bars`（只数窗口内）**两个独立计数**，闸门口径保持修复前语义 ⇒ 未改箱体窗口 |
| 三、小时线是否落后 | 口径统一为 `open_time`（原实现拿 close 减 open ⇒ `lag_4h_bars` 恒 -1、`aligned_4h` 恒 False）。重启后 `aligned_1h=true / lag_1h=0 / aligned_4h=true / lag_4h=0`，逐币 `lag_1h_histogram={"0":526}`、`n_lag_1h_gt1=0`、`lagging=[]`。**1h 节奏隔离实测 7,200 根全部处理、中位与最大延迟均 0 分钟、>60min 堆积 0 个** ⇒ 不等下一根 4h。**允许落后根数保持 ≤1，未放宽到 4** |
| 四、隔离三场景 + 窗口移动 | 变量分离后：**喂法等价（一次喂完 vs 建档后 step 追加）526/526 × 2 组**；**只换窗口起点（差 60h）24/526 差异**，且 24 个差异**没有一个含 `atr`** ⇒ ATR 不受窗口移动影响。机制机械归因：建档闸门 `box_gate_bars>=200` 锚在窗口起点，525 个可算标的中 **524 个**开闸时刻恰好差 **-60.0h** |
| 五、受控重启与生产核验 | 一次 `service_manager restart --enable-paper --arm 3a --symbol all`（带 `ENABLE_PAPER_RUNTIME=1`），10.3s、返回码 0、**未重试**。**13/13 核验 PASS**：pid 97293→58910、采集器 42278 未变、进程晚于代码、`data_source=merged_cold_hot`、`cold_rows_scanned=538,609`、`bars_4h_max=289`（修复前 271）、`atr_prewarm_bars_max=790`、水位双口径对齐、`blocked_n=6`、账本持久文件逐字节未变、`orders_routed_total` 0→0、`open_positions` 0→0 |
| 六、异常标的定性 + 阻断决策 | 全池扫出 **6 个**（不止 3 个）`SETTLING` 标的，`deliveryDate` 全部已过。**三源一致**（官方 `exchangeInfo` + 官方 REST `klines` + 官方**日**归档 zip）：`volume==0 且 O=H=L=C`，占位价冻结在最后成交价。**不删行、不改价、不插值**；固定时间截点行级指纹前后逐位相同（8,161,218 行）。闸门拦下 470/469/469/109/38/37 根 1h 与 72/72/72/27/9/9 根 4h 建档 |

## 必须记住的四个硬结论

1. **「文件已改」≠「运行已修」**：重启前进程启动 `2026-10-24T14:40:05Z`、`unified_runtime.py` mtime `2026-10-25T01:01:23Z`，落后 **37,278 秒 ≈ 10.4 小时**；`bootstrap.data_source=null`、`data_quality=null`、`lag_4h_bars=-1` ⇒ **FIX-02/03 的修复在重启前从未生效**。
2. **「声明 ≠ 接上」**：前端 `ExecutionDaemonView.atrCaliber` 字段声明了、`PositionPanel` 也渲染了，但 `fetchPaperDaemonStatus` **从未给它赋值** ⇒ 「ATR 口径」那一行在生产页面上**从来没出现过**。本轮补上映射，并新增 `watermarkCheck` / `dataQuality` 两块只读展示（3/3 块与 API 数字 parity 通过）。
3. **零信号没有被这次修复消除，也不该被消除**：主因是供给稀缺 + 数据不足（FIX-01 六类归因：数据不足 526/526、条件未达到 523、数据坏死 3）。`SIGNAL-LINK-01` 的速率结论不变：全市场 0.085 个/天、≈12 天/个候选。
4. **A/B 里那 1 个历史候选是截断窗口的产物**：`hot_only` 有 `ZEREBROUSDT @ 1791982800000`，合并历史 0 个。`02_窗口归因.json` 实测 `A≡C=true`、`A≡B=false`（C = 合并历史但窗口对齐 A + 180d 预热）⇒ 差异只来自窗口覆盖率，**不是 ATR**。

## 踩过的坑（下次别再犯）

- **`locks/unified_runtime_status_3a.json` 是固定桩**：`updated_at=2026-10-23T03:00:00Z`，字段名（`health`/`pid`/`cycle_count`）与 `get_full_status()` 完全对不上。拿它当就绪判据会「永远没就绪」或「读到两天前的假状态」。**就绪一律走 API**。
- **在活跃采集库上「数总行数」当不变性判据必错**：采集器一直在写新 bar，行数会在脚本运行期间变大。必须用**固定时间截点**（`open_time <= 截点`）做行级指纹。
- **本地比官方少最新 1~2 根是采集滞后，不是来源不一致**：比 `run` 长度前必须**截到重叠区间**（截止 = 本地最后一根 `open_time`）。
- **月度归档当月未发布会 404**；短占位段（如 `MITOUSDT` 1h 只有 40 根）用「最后一根往前 2 天」会落到占位段之前的真实行情上 ⇒ 必须按**占位段覆盖的自然日**多日回退。
- **`history_floor_ms` 必须与 bootstrap 窗口起点同源**：写成固定的 `now-1160h` 而 bootstrap 到过去某刻，会让 `process_market` 的 `open >= floor` 把前面若干根 4h 挡在门外而**永不处理**，ATR 就差那几根（我第一次做 S2/S3 就踩了，出现 2e-7 的假差异）。
- **`-shm` / `-wal` 是 SQLite 瞬时伴生文件**：连接关闭时 checkpoint 会删掉它们，拿它当「账本被改」会误判。只比 `*.sqlite3`。
- **本项目没有 `PAPER_LEDGER_DIR` 这个符号**（旧记忆里的名字是错的）：真实账本目录是 `live_store.LEDGER_DIR = 数据/paper`。
- **A/B 与窗口对照必须变量分离**：把「喂法」和「窗口起点」混在一个对照里，差异无法归因。
- **`watermark_check` 的逐币字段在 `per_symbol` 里**：`lag_1h_max` / `n_lag_1h_gt1` / `lag_1h_histogram` 都不在顶层（顶层只有 `lag_1h_bars` / `aligned_1h`）。从顶层读会得到 `null`，看起来像「系统没给」。

## 当前系统状态（收口时）

- runtime：`running`，pid **58910**，`started_at=1792891759549`（2026-10-25T01:29:19Z），`process_older_than_code=false`
- `signal_engine`：`universe=526/526`、`data_source=merged_cold_hot`、`ready=526`、`skipped=0`、`history_candidates_discarded=0`
- `used`：`replay_window_hours=1160`、`atr_prewarm_bars_max=790`、`bars_4h_max=289`、`bars_1h_max=1159`
- 水位：`aligned_1h=true`、`lag_1h_bars=0`、逐币 `{"0":526}`；`aligned_4h=true`、`lag_4h_bars=0`
- 数据质量闸门：`blocked_n=6`（6 个 SETTLING 标的）
- `orders_routed_total=0`、`orders_rejected_total=0`、`open_positions=0`、账本 0 新增
- 采集器 pid **42278** 全程未变
- **新增 1h 已被处理（实测）**：`01:39:49Z` 水位 `00:00Z` → `02:00:17Z` 水位 **`01:00Z`**（`db_1h_max` 同步到 `01:00Z`），全程 41 个采样 `lag_1h_bars` 恒 `0`；该小时 `funnel.since_start` 全 0 ⇒ **这一小时确实没新信号，不是漏处理**

## 未解决 / 需人决策

- **建档闸门锚点（最重要）**：`box_gate_bars>=200` 锚在窗口起点 ⇒ 每次重启窗口前移，开闸时刻跟着前移 ⇒ 窗口移动 60h 会改变 **24/526（4.6%）** 标的的箱体/状态。改用 `atr_bars_seen` 可让重启不再改状态，但**会改变历史候选集合**（`ZEREBROUSDT` 的候选消失）。两条路都碰「不改箱体窗口/ATR 规范」⇒ **须裁决，本单未改**。
- **候选速率**：加标的这条路已走完（526 已是全部合规合约），只剩加时间（30 个样本≈1 年、100 个≈3.2 年）。
- **`max_holding_hours=240` 仍在生效视图配置里**并进 `view_sha256`，实际不执行；**谁若「照参数实现」会把 10 天强平重新引入**。
- **真实挂钟自动成交仍未验证**：本轮全池自然市场 0 新信号，账本 0 笔已平 ⇒ 日常部署**不可交付**。
