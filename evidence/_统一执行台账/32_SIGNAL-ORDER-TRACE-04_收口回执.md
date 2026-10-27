# 32｜SIGNAL-ORDER-TRACE-04 收口回执

- 工单：`SIGNAL-ORDER-TRACE-04｜候选到订单、成交、复盘的完整追溯`
- 证据：`evidence/SIGNAL-ORDER-TRACE-04/`（主报告 `README.md` 12 节、`manifest.json` 1,467 文件 / 373.6 MB、`code/` 14 个脚本、`out/` 23 个顶层文件 + 6 个子目录）
- 状态：**已收口**（追溯链四类身份 + 四类场景 + 逐笔/逐次/汇总 + 双路导出，全部实跑并机械核对通过）
- 策略可交付性：**仍未通过**。本单证明的是「链路能走通且可核对」，不是「策略有效」；`WX-GATE-v2.0` 未过、`delivery_eligible=false` 不变
- 真实交易：**保持禁用**（本单不起真实下单；7 个交易所订单号 token 在后端 0 命中，机械自证）

## 六条要点逐条回执

| 工单要点 | 回执 |
|---|---|
| 一、补齐候选→事件→订单→成交→复盘关联，区分四类身份 | 四类身份逐一定性：**运行内 ID**（`event_id`/`candidate_id`/`transition_id`/`paper_order.order_id`/`paper_fill.fill_id`/`paper_position.position_id`/`paper_realized.id`，`paper_*` 是自增主键、每个账本文件各自从 1 开始）；**跨运行匹配键** `candidate_id`（B1 + B3 实测逐位相同）+ `cross_run_key`（本单新建，形态语义量 SHA-256，作冗余二次校验）；**引擎订单号** `paper_order.order_id`（账本内唯一、跨库不可比）；**交易所订单号**——**不存在**，7 个 token 0 命中，**不用 `order_id` 冒充**。关联边全部是真实外键：订单→候选走 `paper_order.event_uid`，成交→候选走 `paper_fill.signal_uid`，成交→订单走 `paper_fill.order_id` |
| 二、修复后规则下的真实历史候选，隔离环境验证完整执行链 | 180 天全池扫描 **65 个真实候选 / 526 标的 / 0 错误**（149 s）。隔离环境：`MARKET_DB`（自建 353.3 MB 行情库，60 标的，15m 14,016 / 1h 3,504 / 4h 875 / 真实资金费 43,599 条，`downloaded_anything=false`）+ 5 个独立隔离账本 + `PAPER_GATE_FILE` + `LIVE_DIR` + `EVENT_TREND_RUNS_DIR` + `EVENT_TREND_BATCH_ROOT`，`assert_isolated()` fail-closed。**隔离回放 48 笔开仓、48 笔全部平仓**，17 笔 `account_drawdown_locked` **原样保留（未绕过任何闸门）**；被拒的每笔都落了 rejected 订单 + `paper_blocked` + `paper_request_anchor` |
| 三、自然运行与隔离回放分开标识 | 自然成交 = **0**：生产 `paper_position` 3 笔**全部** `origin='manual'` + `voided=1` + `void_reason='synthetic_test_isolation'`；`paper_fill` 6 条、`paper_realized` 3 条**全 voided**。回放侧三重标记 `origin='replay'` + `run.json.research_period=true` + `not_natural_signal=true`。**回放成交一律不计入自然成交** |
| 四、逐笔订单、逐次成交、交易汇总 + 能力边界 | 三个 CSV：`05_逐笔订单_replay.csv`（113 订单）、`05_逐次成交_replay.csv`（96 成交）、`05_交易汇总_replay.csv`（48 笔）。关联链 **8 环 48/48 全通**，三方对账 **48/48 逐字段全对**。汇总：net **−637.9036706339**、gross −614.9958413、fee 20.7531642433、slippage 27.4416353、funding 2.1546650907、跳空 0、胜率 18.75%（9/39/0）。**能力边界如实写**：引擎**有**逐次成交事件（96 条 `paper_fill`，每张成交订单恰 1 条 = 全量成交），**不是**订单级投影；但**不支持部分成交展开**，不能据此推断撮合深度；交易所订单号 `None` |
| 五、实际导出入口下载并核对 + 注册工作台 + K 线可重开 + 订单可定位 | 双路都验：**端点级**（独立后端 8799，60 run × 6 端点，列表命中 60/60、行数 180/180 相符、账本对账 0 不符、列覆盖 154 全对 / 26 不适用 / 0 失败）；**按钮级**（第二个 vite 3001，Playwright 真点「导出」拿 download 事件，逐笔 2 行 / 订单 4 行 / 成交 4 行真落盘）。K 线重开 6/6，**并追加窗口覆盖核对**（数据窗口 ⊇ run 窗口 60/60、48 笔交易 entry/exit 全在窗口内、实拉 candle 首末时刻包住交易 all_ok=True）。订单定位点到 `trd_paper_pos_1`；页面自算 4 项指标 vs `summary.json` 全对 |
| 六、最少覆盖正常执行 / 有效拒绝 / 重复输入 | 四类场景实跑：① **正常执行** 48 笔；② **有效拒绝** 两种真闸门（`account_drawdown_locked` 17 笔 + `before_paper_epoch_no_backfill` 65/65 + `new_entry_blocked_pending_closure_review` 65/65）；③ **重复输入** 两个 case 全过——`open` 第 2/3 次返回 `duplicate_signal_uid`、`gate` 3 次同原因，**按账本计数判定幂等（不看返回值自报）**，计数一字不变 |

## 必须记住的七个硬结论

1. **自然成交 = 0 是规则决定的，不是缺陷**：65 个真实候选**全部早于 paper epoch（`PAPER_EPOCH_MS` = 2026-10-14T01:00Z）** ⇒ 严格按生产规则跑，65/65 被 `before_paper_epoch_no_backfill` 拒绝。**不要为了「看到成交」去动 epoch 或门槛。**
2. **交易所订单号在这个系统里不存在**：纸面撮合由「已闭合 15m bar 开盘价」本地完成，7 个 token 后端 0 命中。任何「交易所订单号」字段一律 `None` + 原因，**用 `paper_order.order_id` 顶上去就是伪造身份**。
3. **`candidate_id` 是窗口无关的跨运行键（旧结论作废）**：构造原文 `state_machine.py:241/584` + B1（同窗两次逐位相同）+ B3（换 30 天窗口后**共同候选**逐位相同）三者合起来证明。**作废**「`candidate_id` 含箱体建档时刻 ⇒ 换窗口即变」这一旧说法——`bar_4h_close_ms` 是同一根 4h bar 的收盘，同一根 bar 在哪个窗口被看到都是同一根。但 B3 交集只有 1 个样本，**不宣称统计意义上的强证据**，正式采纳需裁决。
4. **4h 出场路径的字段口径会造出负持有期**：`advance_position_trailing` 在 `low_4h <= cur_stop` 时直接 `close_at_price`，`bar_open_time/bar_close_time` 传的是 4h bar 时间。48 笔里 **15 笔走此路径，其中 14 笔的 bar 起点早于入场** ⇒ 拿成交 bar 时间当 `exit_time` 会算出负值。**投影一律用 `paper_position.entry_ts/exit_ts`**；引擎侧只记录不改。
5. **隔离光设 `EVENT_TREND_RUNS_DIR` 不够**：`/batches` 走 `EVENT_TREND_BATCH_ROOT`，不设就**回落读生产侧**（生产 runs 目录收口时实测 2,291 条目）。已补该变量 + `batches_endpoint.isolated` 自证（`total=0`、`batch_ids=[]`）。**另外：软链一个 run 目录当批次根会让 `/batches` 500，用空批次根。**（早期在页面上看到生产 run 混进来时，那个「总数」没有落盘 ⇒ 本回执只写能回到 `out/07_导出核验.json` 的 2,291。）
6. **「自己验自己」不成立**：05 段的对账用的是本单自己的引擎调用 + 自己写的投影，天然一致。新增 **12 段**：不读 `out/05_*.json`、不 import 本单计算函数，只用 `sqlite3` 只读直连账本，用 SQL 重算净额恒等式 / 悬空外键 / 成交↔订单逐字段 / 资金费两层 / 与报告等价——**全 0 违规、五项全同**（容差 1e-9，不拿打印小数位当一致）。
7. **一个只会输出 `False` 的检查项不构成判据**：07 段记了 `window_matches_run_window=False` 却没说清它意味着什么。拆成 C3（数据窗口 ⊇ run 窗口）/ C4（交易 entry/exit 在窗口内）/ C5（实拉 candle 首末时刻包住交易）三条可判定判据后，结论从「False」变成「**不相等但包住，48 笔交易全在窗口内**」——这才是「图对得上交易」的真正判据。

## 踩过的坑（下次别再犯）

- **`fills.jsonl` 必须是「事件 + payload」结构**：`ledger_links.all_fills()` 只从 `payload` 取字段，平铺会被读成 `None` ⇒ 前端成交表全空。
- **拒单订单的 `qty/price/fee/trade_id` 本来就是 NULL**（前置闸门在定量前就拒）。按 `status` 分流，否则判「空列」会误报 16 组。
- **`all()` 把 `None` 当 `False`**：三态（通过 / 不通过 / 不适用）必须显式写，不能 `all([...])`。
- **K 线判据要读信封**：载荷在 `body["chart"]["candles"]`，读 `body["candles"]` 会误报 6/6 失败。
- **前台跑 replay 会被 SIGTERM（exit 137）**：65 候选 × 14,016 根 15m 太重。改后台 + `drive_exit` 用 bisect 从 `entry_ts` 切片，401.3 s 完成。
- **千分位逗号会让「数字一致性核对」假阳性**：README 写 `14,016`，token 化后变成 `14` 和 `016`，与 JSON 的 `14016` 对不上。核对脚本必须先去掉 `(?<=\d),(?=\d{3}\b)`。
- **端口不复用未知进程**：`_port_free` 检查 + `finally` 里只终止自己起的子进程；收尾按 `psutil.Process.argv` 校验是 `adapter.py` 才动。**两个测试端口（8799/3001）收口时已确认 closed**，8787/3000 生产服务未动。
- **生产账本只比 `*.sqlite3`**：`-shm`/`-wal` 是 SQLite 瞬时伴生文件，连接关闭时 checkpoint 会删掉它们，拿它当「账本被改」会误判。
- **`PAPER_LEDGER_DIR` 是真实生效的 env var**（`live_store.get_ledger_dir()` 读它，`LEDGER_DIR` 在 import 时求值）⇒ **必须在 import 后端模块之前设好**，否则隔离失效。

## 当前系统状态（收口时）

- 生产账本 `数据/paper/paper_3a.sqlite3`：SHA `19825112…6da9479`，本单前后**相同**（`out/07_导出核验.json` 的 `isolation_proof` before/after）
- 生产 runs 目录 `回测日志/event_trend_v0_1/runs`：**2,291 条目**，本单 **0 新增**（本单 60 个 run 全在 `out/iso_runs/`）
- 端口：8787 / 3000 LISTENING（生产，未动）；8799 / 3001 **closed**（本单测试实例已回收）
- 本单交付：`evidence/SIGNAL-ORDER-TRACE-04/`，`manifest.json` `generated_at_utc=2026-10-25T03:19:09Z`，1,467 文件 / 373.6 MB，README 自身 SHA 在 manifest 中**新鲜**（`manifest.json` 不把自己算进列表）

## 待裁决（写进台账，不自行处置）

1. **【需人决策】4h 出场路径的部分重叠 bar 是否算前视**：14/48 笔受影响（参考 bar 在入场前已开始，其 `low` 含入场前价格，守卫只挡「整根在入场前」）。改则动冻结出场规则，须走裁决流程。
2. **【需人决策】`candidate_id` 能否正式升为跨运行键**：本单证据支持「能」（构造 + B1 + B3），但 B3 交集只有 1 个样本。若采纳，`cross_run_key` 降为冗余校验；若不采纳，双键并存。
3. **【能力缺口】`paper_order` 无 `signal_uid` 列**：订单→候选只能走 `event_uid`。当前够用；若将来一个候选要对应多张订单，`event_uid` 会不够。
4. **【未验证】真实挂钟自动成交**：本单全部是回放时钟（`now_ms` = 15m 收盘）。真实挂钟下的自动成交仍未被验证，`delivery_eligible` 依然 `false`。
