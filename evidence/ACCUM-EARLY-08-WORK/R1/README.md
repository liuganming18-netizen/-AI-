# ACCUM-EARLY-08-WORK / R1｜V3.1 Work 信号接入（续单）

- 日期：2026-10-27
- 分支：`wyckoff-v3.1-main`（基线提交 `e20e0cb8`）
- 执行方：Work
- 本目录：`evidence/ACCUM-EARLY-08-WORK/R1/`（原始交付 `../` 一字未改）
- 判定总表：**126 项断言，126 通过**（`out/*.json` 里逐项可查）

---

## 一、结论（先看这一段）

反重力 `arm_b` 的 **289 条研究信号**已按**既有** `live_signal` 契约接入生产信号库：

- 全部 `origin='historical_research'`、`executable_state='historical_not_executable'`、`payload.approved=false`；
- 真实 HTTP 查得到（`scope=all&origin=historical_research` → 289 条，`has_more=false`）；
- 当前交易区 HTTP 与**页面**都是 **0 行**，页面自己请求的 `scope=current` 同时报告「289 条被排除」——
  证明页面读的是同一本库，研究信号是被规则挡住的，不是「没进库」；
- 生产**行情库、四本纸面账本零写入**；生产后端**未重启**（pid 未变）。

**不成立的宣称（不要写）**：

- ❌「自然信号 → 订单闭环已打通」：自然候选 0，无法验证；
- ❌「研究信号可执行」：四层条件 + `approved=false`，恒不可执行；
- ❌「重启后自动恢复」：生产后端未重启，进程级未验证；
- ❌「信号页研究视图已做」：前端只有 `scope=current`，没有研究视图（需裁决）。

---

## 二、逐项状态

### 工作要求 1–6

| # | 要求 | 状态 | 证据 |
|---|---|---|---|
| 1 | 复用现有存储/API/页面；按最新边界修订契约，不因旧草案未核对停工 | **完成** | `接口契约_R1.md`（§9 逐条列与草案差异）；`scripts/20–70` 全部走既有 `live_store` / `adapter._v1_signals_merged` / 既有页面 |
| 2 | 真实隔离回测信号接入；保留身份字段、来源、确定性 uid、`approved=false`、不可执行态与具体原因；四时刻互不替代，无证据标 `unproven` | **完成** | `out/20_隔离演练.json`（26/26：`config_hash` 289/289、`data_version` 289/289 为空、`available_at` 289/289 为空且标 `unproven`、`bar_open≠event_time` 289/289、`bar_open<bar_close` 289/289） |
| 3 | 研究来源显式标记，不进当前可交易区，不得默认 `origin=live`，复用既有来源取值 | **完成** | `origin='historical_research'`（既有值）、`source='antigravity_08_backtest'`、`payload.role='research_only'`；`out/40` 27/27、`out/60` 22/22 |
| 4 | 写入现有/隔离存储；生产行情库与账本零写入；不得以「禁止写入」阻止接线；不得自动下单 | **完成** | `out/30_生产接入.json`（15/15：四账本 sha256 逐位不变、`current_area_rows=0`）；`out/60`（行情库 SQL 级 0 写语句 + 四种 `origin` 真调路由 `processed=0 / registered=0`） |
| 5 | 幂等 / 重启恢复 / 批准闸门拒绝执行 / 失败原因可见；真实 HTTP 与页面验证；隔离测试不启动第二生产实例、不污染生产库 | **完成（进程级重启除外）** | `out/50`（9/9：新进程 `new=0/updated=289`、`first_seen` 不变、身份列逐位一致）；`out/40`（27/27，含 Playwright 截图）；`out/60`（22/22 五层矩阵）；隔离全部在 `R1/isolated/` 副本上，生产 pid 未变 |
| 6 | 与反重力共用 manifest 字段，定义一致；Work 独占接口接入代码 | **完成** | `out/manifest_antigravity_08_arm_b.json`（§7 字段表逐项落地）；生产代码只新增 `框架/backend/research_signal_import.py` 一个文件 |

### 交付要求

| 项 | 状态 | 位置 |
|---|---|---|
| 更新契约 | **完成** | `接口契约_R1.md` |
| 接入实现 | **完成** | `框架/backend/research_signal_import.py`（新建，22 KB） |
| 真实 HTTP 与页面证据 | **完成** | `out/40_HTTP与页面.json` + `out/40_页面_当前交易区.png` |
| 幂等 / 恢复 / 闸门正反例 | **完成** | `out/20`、`out/50`、`out/60` |
| 生产零写入证据 | **完成** | `out/30`、`out/60`（行情库用 SQL 级探针，不用文件指纹，见 §五） |
| 逐项状态 | **完成** | 本文件 |
| 续单产物存 `R1/`，保留原始交付 | **完成** | `../README.md`、`../接口契约.md`、`../scripts/`、`../out/` 均未改动 |

### 阻塞 / 未完成

| 项 | 状态 | 原因 |
|---|---|---|
| 生产后端进程重启后 API 恢复 | **未验证** | 工单禁止中断健康服务 |
| 自然信号端到端闭环 | **未验证** | 自然候选 0（`out/70`） |
| 页面研究视图 | **未做（阻塞于裁决）** | 前端只请求 `scope=current`；加视图属前端架构改动 |
| `available_at` 真实可得时刻 | **未验证** | 反重力无证据，全部 `unproven` |

---

## 三、证据清单

| 文件 | 断言 | 通过 | 说明 |
|---|---|---|---|
| `out/20_隔离演练.json` | 26 | 26 | 隔离副本上 dry-run → apply ×2（两个独立进程）→ 字段逐项统计 → 闸门扫描 → 正向对照 → 生产零写入 |
| `out/30_生产接入.json` | 15 | 15 | 写前快照（已存在不覆盖）、写生产库、写后读回、非目标库零写入、后端 pid 未变 |
| `out/31_写前快照重建.json` | 5 | 5 | 重建写前状态（删除 289 行后 0 行，与 08-WORK 只读探测记录的写前行数一致），标注为重建件 |
| `out/32_首次接入复演.json` | 7 | 7 | 在写前状态副本上复演首次接入：0 行 → 289 行，净增 289、更新 0 |
| `out/40_HTTP与页面.json` | 27 | 27 | 真实 HTTP 三条路径 + Playwright 页面取证（截图 421 KB） |
| `out/50_重启恢复.json` | 9 | 9 | 新进程重放：`new=0`、`first_seen` 不变、身份哈希一致、`updated_at` 全推进 |
| `out/60_闸门正反例.json` | 22 | 22 | 生产库真调路由 4 种 origin + 副本上五层失效矩阵 + 原因可见性 |
| `out/70_自然扫描.json` | 15 | 15 | 水位对齐、漏斗、空转分因、观察区 333 条、闸门状态 |
| **合计** | **126** | **126** | |

配套脚本（`scripts/`）：`lib_r1.py`、`probe_market.py`、`page40.mjs`、`20_隔离演练.py`、`20b_隔离核验_worker.py`、`30_生产接入.py`、`31_写前快照重建.py`、`32_首次接入复演.py`、`40_HTTP与页面.py`、`50_重启恢复.py`、`60_闸门正反例.py`、`60b_闸门矩阵_worker.py`、`60c_生产路由只读.py`、`70_自然扫描.py`。

---

## 四、生产影响面

**写了什么**

- `框架/backend/live/live_signals.sqlite3`：新增 289 行 `live_signal`（`origin='historical_research'`）。
  写前状态 **0 行**（出处：08-WORK 原始交付的只读探测
  `../out/03_隔离与空链路.json → 隔离链路/生产库只读探测/rows_before=0`；
  首次接入行为由 `out/32_首次接入复演.json` 在写前状态副本上复演为 0 → 289）。
  写前快照原件的生成动作在 `30` 里做了（45,056 B，sha256 `18de9c2f…e6449`），
  但**被我自己的复跑动作删掉了**（见 §六 第 4 条），
  现由 `scripts/31_写前快照重建.py` 重建为「等价内容状态」并标注为**重建件**：
  `isolated/pre_import/live_signals_before_import.sqlite3`（45,056 B，sha256 `bb80e36f…64d995`）
  + `重建说明.json`。重建件只保证**内容等价**（删除 289 行后剩 0 行 = 写前行数），
  不保证字节布局与原件一致，因此**不能**用它证明「原件未被篡改」。
- 生产代码：新增 1 个文件 `框架/backend/research_signal_import.py`（未被任何既有模块 import，不改任何既有调用链）。

**没写什么**

| 对象 | 判据 | 结果 |
|---|---|---|
| 行情库 `数据/市场数据/db/market_v2.sqlite3` | 进程内 SQL 级写探针（`probe_market.py`） | 连接 1 次，**写语句 0 条** |
| `paper_3a/3b/3c/3d.sqlite3` | sha256 逐位比对 | **4/4 未变** |
| 生产后端进程 | pid + `lsof` | **未重启、未中断** |
| 交易路径 | `route_pending_orders` 四种 origin 真调 | `processed=0 / registered=0 / errors=[]` |

---

## 五、两个必须说清的判据问题

### 1. 行情库不能用文件指纹判「零写入」

行情库由采集进程**持续写入**，几分钟窗口里 size/mtime 必然变。
`60` 第一次跑就是因此判「不通过」——但变的原因是采集，不是我们。
改用**进程内 SQL 级写探针**（`probe_market.py` 包 `sqlite3.connect` + `set_trace_callback`，
只数 INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER），结论是 **0 条写语句**。
账本不常写，仍用 sha256 逐位判（更硬）。

### 2. 接口的 `total` 不是库内总数

实测：`limit=1 → total=1、has_more=true`；`limit=1000 → total=289、has_more=false`。
审计通道 `total = len(merged)`（`adapter.py:3551`）。
**判「读全了没有」只能看 `has_more`**，把 `total` 当总数会得出「库里只有 1 条」的错误结论。

---

## 六、本单纠正的五处自己的错

| 错 | 现象 | 纠正 |
|---|---|---|
| `bar_open_time` 填了 15m 入场 bar 开盘 | 出现 `bar_open_time > bar_close_time`（倒挂 900,001 ms），与 `live_signal_engine.py:232` 的列语义不符 | 改为 `confirm_close_ms − 3,600,000 + 1`（确认 bar 开盘），入场时点单列 `payload.timestamps.entry_bar_open_ms`；新增 2 项断言（跨度必须 3,599,999 ms、入场 bar 开盘必须晚于确认收盘） |
| 用 symbol 判「页面没出现研究信号」 | `ACTUSDT` 命中页面，但它来自**观察区**（另一个接口），不是研究信号 | 改用**确定性 uid** 判（uid 只可能来自研究信号），symbol 命中逐个归因到观察区 |
| 行情库用文件指纹判零写入 | 采集进程正常写入被判成「我们写了」 | 改 SQL 级探针（见 §五） |
| `reset_iso()` 会 `rmtree` 整个 `isolated/` | 复跑 `20` 时把 `30` 写的**写前快照原件删掉了**（回滚材料被自己的脚本销毁） | `reset_iso()` 改为保留 `pre_import/`；写前状态由 `31` 重建为等价件并标注「重建件」 |
| `30` 复跑会覆盖首次接入记录 | 「写前行数 0、净增 289」这条**首次接入的事实**被复跑改写成「写前 289、净增 0」 | `30` 改为**不覆盖已有快照** + 每次运行追加 `out/30_生产接入_历次.json`；首次接入行为由 `32` 在写前状态副本上**复演**（0→289，净增 289）补证 |

---

## 七、复跑方法

```bash
cd evidence/ACCUM-EARLY-08-WORK/R1
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY scripts/20_隔离演练.py     # 隔离副本，不碰生产（会清 isolated/，但保留 pre_import/）
$PY scripts/30_生产接入.py     # 会写生产信号库（幂等；不覆盖已有写前快照；追加历次记录）
$PY scripts/31_写前快照重建.py # 仅当写前快照丢了才需要
$PY scripts/32_首次接入复演.py # 在写前状态副本上复演 0 → 289
$PY scripts/40_HTTP与页面.py   # 需 8787/3000 在跑
$PY scripts/50_重启恢复.py     # 幂等重放
$PY scripts/60_闸门正反例.py   # 副本上做失效矩阵
$PY scripts/70_自然扫描.py     # 只读
```

前置条件：生产 `8787`、前端 `3000` 在跑（本单**不负责**启停它们）；反重力产物
`evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/` 存在。
`20` 的判据按**相对增量**写（不写死 0），空库或已有 289 行都能复跑。

---

## 八、回滚

```sql
-- 首选：精确删掉本单写入的研究行，不动任何 live 行
DELETE FROM live_signal
 WHERE origin='historical_research'
   AND json_extract(payload_json,'$.source')='antigravity_08_backtest';
```

兜底（整库还原，会丢掉快照之后的 live 行，需先确认没有新 live 信号）：

```bash
cp evidence/ACCUM-EARLY-08-WORK/R1/isolated/pre_import/live_signals_before_import.sqlite3 \
   框架/backend/live/live_signals.sqlite3
```

⚠️ 该文件是**重建件**（原件被复跑误删），内容等价于写前状态（0 行），但字节布局与原件不同。
原件 sha256 `18de9c2f…e6449` 是**转引**（第一次接入时记在 `30` 的产出里，该产出已被复跑覆盖），
现只在 `重建说明.json` 与本文件里保留，不能作为「原件仍存在」的证据。

代码回滚：删除 `框架/backend/research_signal_import.py`（无任何既有模块引用它，删掉即回到原状）。

---

## 九、待裁决（本单没做，需要拍板）

| # | 事项 | 为什么必须你定 |
|---|---|---|
| 1 | 研究信号在**页面**哪里看 | 前端只请求当前交易区，加「研究/审计」视图属前端架构改动 |
| 2 | 是否新增 `origin='research'` | 新增取值要同步改白名单、前端类型、六态映射；本单复用 `historical_research` 零扩张 |
| 3 | 要不要给下单入口加「`approved=false` 直接拒」/ 研究期间关闸门 | **实测：五层条件全被破坏时，路由会真的登记挂单**（副本账本 `paper_pending=1`），而 `paper_engine.open_position` 没有 `approved` 硬拒；当前开仓闸门是**开着**的（`user_ui_control`，`since 1792753532432`） |
| 4 | 顶层身份字段要不要按条目区分 | 响应顶层是运行时身份 `FT-LOW4H-EXP-V2.2`，条目是 `ACCUM-RESEARCH-v0::4x`；只读顶层的消费方会误标 |

## 十、顺带查出的一个未解项（不下结论）

`70` 的漏斗：本进程 `箱体 86 → 扫低 19 → 回踩 19 → 候选 0`；48 天预热回放
`箱体 8061 → 扫低 2008 → 回踩 1220 → 候选 0`。
即**扫低与回踩都发生过，候选仍为 0**。
旧结论把「这波 0 候选」归因为「单边上涨里箱体等不到跌破下沿」，与这组数字对不上
（`RETEST=1220` 却 `CANDIDATE=0`）。
本单**不下结论、不据此改策略**，只如实记录，建议单独立项核查 `RETEST → CANDIDATE` 的判定链。
（另注：漏斗是状态迁移计数，不是独立事件数，不能当事件数用。）
