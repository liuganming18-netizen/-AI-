# ACCUM-EARLY-09 Work｜研究信号查看与隔离恢复核验

> 执行方：Work（本单自报）。**本文件里所有「通过」都是 Work 自报，未经主代理独立验收。**
> 生成时刻：2026-10-27。证据目录：`evidence/ACCUM-EARLY-09-WORK/`。
> 前置：`ACCUM-EARLY-08-WORK/R1` 已把 289 条 `historical_research` 写进生产信号库。

---

## 一、一句话结论

**研究信号现在可以被逐条查看、且不会被当成可交易信号；隔离环境下的跨进程恢复成立；
自然零候选定位在「回踩→候选」这一层，但本单没有给出根因。**
生产后端**没有重载**（工单禁止重启健康服务），所以生产页面上的运行/配置身份字段目前显示
「未提供」——这是如实降级，不是伪造。

---

## 二、逐项状态（六项工作范围）

| # | 工作项 | 状态 | 判定依据（脚本 / 证据） |
|---|---|---|---|
| 1 | 现状快照（只读） | **完成** | `scripts/10_现状快照.py` 15/15；`out/10_现状快照.json`、`out/10_研究记录_289.json`、`out/10_页面_改动前.png` |
| 2 | 研究查看落点 | **完成**（隔离环境已验证；生产为降级态，见 §五） | `scripts/30_页面与一致性.py` 39/39；`out/30_页面_研究区.png` |
| 3 | API 与页面一致性 | **完成** | 同上：页面 289 行 uid 集合 == API 289 条；抽样 12 行 × 10 字段逐字段相等 |
| 4 | 跨进程恢复 | **完成**（隔离库 + 隔离服务） | `scripts/50_跨进程恢复.py` 42/42；进程 A/B/C/D 四个不同 pid |
| 5 | 自然零候选只读分因 | **部分完成**：定位到层，**未给根因** | `scripts/70_自然分因.py` 32/32；`out/70_自然分因.json` |
| 6 | 下单防护核验与方案 | **完成**（矩阵 14 格 + 补丁方案；补丁**未实施**） | `scripts/60_单因素闸门矩阵.py` 30/30；`out/60_闸门矩阵.json`；`out/80_变更清单.json` |

另附：`scripts/21_后端字段.py` 52/52（后端字段验收）、`scripts/80_变更清单.py` 16/16（变更清单、路径图、探针正反例）。

**总计断言：15 + 52 + 39 + 42 + 30 + 32 + 16 = 226 项，全部通过（Work 自报）。**

---

## 三、触及的文件（只有 3 个，开工前已登记）

开工前指纹取自 `out/10_现状快照.json` 的「开工前_允许触及文件」，改动后由 `80_变更清单.py` 逐项对照。

| 文件 | 类型 | 开工前 | 现在 | 改了什么 |
|---|---|---|---|---|
| `框架/backend/live_api.py` | 修改 | 113,929 B / `312ecf9c…` | 115,527 B | 只在 `live_signals()` 组装条目处加一处 `09-WORK` 标记块：**仅当** `origin=='historical_research'` 时给条目附 `research` 子对象（16 个身份字段）；既有 34 个键一个没动 |
| `UI界面/web/src/components/ResearchSignalSection.tsx` | **新建** | 不存在 | 12,229 B / 267 行 | 只读折叠区，只调 `apiGet`，无 `apiPost`、无执行按钮，`data-testid` 全部以 `research-` 开头 |
| `UI界面/web/src/components/SignalListPanel.tsx` | 修改 | 48,281 B / `0a6b1330…` | 48,767 B | 两处：加一行 import；把 `<ResearchSignalSection />` 插在 `signal-row` 列表容器**之外**（结构上保证当前区不被污染） |

**没有动**：入场/退出规则、阈值、策略参数、资金管理、生产交易行为、`config.py`、冻结规则 md、
闸门文件、任何 `origin` 取值、任何数据库表结构、任何页面路由。

---

## 四、运行身份与隔离边界

| 对象 | 身份 |
|---|---|
| 生产后端 8787 | pid 91940（**全程未重启**，前后 pid 一致） |
| 生产前端 3000 | pid 42341（未动） |
| 生产信号库 | `框架/backend/live/live_signals.sqlite3`，289 行，**全部** `historical_research`（live/recovered 行 = 0） |
| 隔离后端 | 端口 8899，用**生产同一份** `adapter.Handler` + `live_api`，只把 `LIVE_DB`/`PAPER_LEDGER_DIR`/`PAPER_LOCK_DIR` 指到 `isolated/` |
| 隔离前端 | 端口 3101，`API_PROXY_TARGET=http://127.0.0.1:8899`、`VITE_CACHE_DIR=isolated/vite-cache` |
| 隔离库 | `isolated/live/live_signals.sqlite3`（生产在线备份）、`isolated/paper/`（四本账本在线备份）、`isolated/recovery/`（跨进程恢复用）、`isolated/gate/`（闸门矩阵沙箱） |

**为什么不用 `adapter.main()`**：`main()` 会跑 `_prewarm()` 与 `backtest.startup()`，两者是生产写。
隔离 runner（`scripts/40b_隔离API服务.py`）只起 `_BoundedThreadingHTTPServer`，路由与响应体走同一份代码。

**零写入怎么证的**：进程内 SQL 写探针（`scripts/probe_sql.py`），在 import 业务模块**之前**装上，
按库路径统计 `INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER/VACUUM`。不用文件指纹——
行情库被采集进程持续写，文件必变，只比文件会把「别人的写」算成「我们的写」。

探针本身做了正反例（`scripts/80b_探针正反例.py`）：正例在临时库建表+插入+更新，探针报出 ≥4 条写；
反例只读同一个库，探针报 0 条且确实读到了 2 行数据。**尺子是准的，0 写才算 0 写。**

---

## 五、必须说清的限制（不要让「通过」掩盖这些）

1. **生产页面是降级态。** 生产后端进程 91940 是改代码**之前**启动的，模块已在内存里，
   不会吐出 `research` 子对象。所以生产页面（3000）现在能看到研究区和 289 行，
   但「运行/配置类型/引擎视图/批准」显示「未提供」，并有一条
   `data-testid="research-identity-missing"` 说明「后端进程未重启」。
   证据：`out/80_生产页面_现状.png`（实拍）、`out/80_变更清单.json` 的「生产页面现状」。
   **要看到完整身份字段，必须重载生产后端——这需要你拍板，本单没做。**
2. **不能宣称「信号页全链路通了」。** 隔离环境（8899 + 3101）是全字段可追溯的；
   生产环境（8787 + 3000）是「结构在、身份字段缺」。两者不能混为一谈。
3. **不能宣称自然信号闭环完成。** 生产库 live 行 = 0，自然候选 = 0，本单只交扫描分因。
4. **不能宣称策略有效或无效。** 本单只定位「零候选发生在哪一层」，不评价策略。
5. **`total` 不是库内总数。** 审计通道 `total = len(merged)`（本页条数），完整性只认 `has_more`。
   本单所有完整性判定都用 `has_more`。
6. **隔离账本上确实出现了写语句。** 隔离 API 服务连账本时会执行 `CREATE TABLE IF NOT EXISTS`
   / `CREATE INDEX IF NOT EXISTS`（幂等建表，`live_store` 连库自带），60 单里共 64 条。
   它们全部落在 `isolated/` 下、全部是幂等 DDL、无 INSERT/UPDATE/DELETE，
   且启停前后账本行数与信号库行数逐项不变。**所以不能写「零写」，只能写「对生产三库零写」**。
7. **前端依赖树有个既有缺陷**（不是本单引入）：`node_modules/@tailwindcss/oxide` 是软链到
   `node_modules/.codex_new_packages/@tailwindcss/oxide`，而原生绑定
   `@tailwindcss/oxide-darwin-arm64` 只在 `.codex_new_packages/@tailwindcss/` 下，
   按标准 node_modules 链解析必然 `MODULE_NOT_FOUND`，vite 起不来。
   本单用**环境变量兜底**（`NODE_PATH` 指到 `.codex_new_packages`），不建软链、不改 `package.json`。
   这是临时绕过，正式修法要重装依赖（不在本单范围）。
8. **3100 端口上有个来源不明的遗留 vite 进程（pid 55420）**，不归本单管，没有动它；
   隔离前端因此用 3101。

---

## 六、第 2–3 项：研究信号怎么看、怎么对得上

**落点**：既有信号面板（`signal-panel`）底部，一个默认折叠的 `<details data-testid="research-signals">`，
标题「历史研究（不可交易）」，摘要行显示 `289 条 · has_more=false · limit=500`。
展开后每行 `data-testid="research-signal-row"`，逐条显示：
来源 `historical_research`、角色 `research_only`、可执行态 `historical_not_executable`、
批准 `false（未批准）`、确定性 uid、运行 `antigravity_08_arm_b`、方案（4a/4b/4c）、
策略 `ACCUM-RESEARCH-v0::4x`、规则版本、配置哈希、配置类型 `fixture`、引擎视图 `82dec362c182`、
四个时刻（事件/确认/可得/确认 bar）、入场 bar 开盘、数据版本、不可执行原因、批准阻塞项。

**一致性怎么证的**（`30_页面与一致性.py`）：

- 页面 289 行的 `data-signal-uid` 集合 **==** API 289 条的 `signal_id` 集合（289/289，无重复）；
- 抽样 12 行 × 10 个字段（origin/role/exec/approved/run_id/event_time/confirmed_at/
  available_at/available_at_status/config_hash）与 API 对应条目**逐字段相等**；
- 页面发出的 `/signals` 响应条数 289、`has_more=false`；
- 当前交易区 `signal-row` 数量 **0**，且 `signal-row[data-origin="historical_research"]` 数量 **0**；
- 研究区内 `button[data-testid*=order|execute|open]` 数量 **0**。

**API 字段验收**（`21_后端字段.py`，52 项）：289/289 条都带 `research` 子对象、16 个键齐全、
`approved` 全为 `false`、`actionable` 全为 `false`、`available_at` 全为 `null`、
`entry_bar_open_ms` 全非空；且**摘掉 `research` 后与生产 8787 的同 uid 条目逐字节等价（289/289）**
——证明「既有字段一个没动」，live 行的响应与改动前完全一致。

---

## 七、第 4 项：跨进程恢复（真进程，不是同进程重连）

`50_跨进程恢复.py`，四个**不同 pid** 的进程接力：

| 步骤 | 进程 | pid | 做了什么 | 结果 |
|---|---|---|---|---|
| 1 | 准备 | — | 生产库在线备份 → 删掉研究行 | 起始 0 行 |
| 2 | A | 94945 | 真实导入器 `research_signal_import.py --apply --require-ok` | 新增 289、更新 0、写后 289 |
| 3 | B | 94946 | 同一条命令**重放** | 新增 **0**、更新 289、总数仍 289 |
| 4 | C | 94947 | 隔离 API 服务（新进程）HTTP 读回 | 289 条、`has_more=false` |
| 5 | D | 94961 | **再起一个**新进程读回 | 289 条，与 C 逐条完全一致 |
| 6 | 前端 | — | 在 D 上开隔离前端，页面读回 | 289 行，uid 集合 == D 读回集合 |

逐字段对照结论：重放后 `first_seen_at` 全部不变、`payload_json` 289/289 哈希相同、
uid 集合不变；C/D 读回的「库列 / payload 身份 / 可得性状态」与库内快照**逐条相等**。
`updated_at` 允许变（幂等更新），它不是身份字段，本单不比。

---

## 八、第 5 项：自然零候选分因（只定位，不给根因）

**扫描范围**：526 个标的（`universe == declared == 526`），ATR 预热 525/526，箱体门开 525。
**水位**：1h 与 4h 都与库内最大一致，`lag=0`；526 个标的 `lag_1h` 直方图全是 0。
本单**自己查行情库**对 526 个标的逐个复算了 1h 水位，与运行时自报的直方图**完全一致**
（`candle_heads` 最大 1h open == 运行时 `db_1h_max`，无 1h 数据的标的 = 0）。

**漏斗（是迁移次数，不是事件数）**：

| 阶段 | 本进程 | 48 天回放 |
|---|---|---|
| BOX | 86 | 8,061 |
| SWEEP | 22 | 2,008 |
| RETEST | 19 | 1,220 |
| **CANDIDATE** | **0** | **0** |
| VOLUME_REJECTED | 0 | 7 |
| OI_UNAVAILABLE | 0 | 51 |
| OI_REJECTED | 0 | 0 |
| INVALIDATED | 40 | 4,220 |
| EXPIRED | 74 | 3,398 |
| MISSED_WITHOUT_RETEST | 0 | 33 |

**独立单位（标的）**：`per_symbol_state` 526 条 —— IDLE 202 / BOX 297 / RETEST 16 / SWEEP 11，
与 `state_counter` 逐项相等，合计 526。**这个量才是「独立事件数」意义上的量**，
和上面的迁移次数不是一回事，禁止相加或换算。

**零候选发生在哪一层**：两个时间尺度都是「RETEST 有数、CANDIDATE 为 0」，
而量价拒绝（7）+ OI 不可用（51）远小于 RETEST（1,220），不足以解释 0 候选
⇒ **卡在「回踩 → 候选」这一层**。

**本单不下结论**：这一层的判定链为什么一条都没通过，需要单独立项核查。
本单只读、没注入候选、没改阈值（生产信号库运行期间 sha256 不变、冻结规则 md sha256 仍为
`1f1167de…9a8ab`）。

**异常/缺数/阻断**：扫描错误 0、跳过原因为空；数据质量按「连续 ≥3 根 1h 零成交量平盘」拦了 6 个标的
（逐条列出）；ATR 未预热 1 个；空转分因 202 条 = IDLE 标的数（箱体宽度/ER 不满足 73、
冷却未到 117、箱体就绪未扫低 12）。

---

## 九、第 6 项：下单防护单因素矩阵（14 格）

在 `isolated/gate/` 沙箱里跑**生产同一份** `_scan_actionable` 与 `route_pending_orders`。
`_scan_actionable` 实际有 6 条谓词：C1 可执行态、C2 事件角色、C3 所属臂、C4 时间齐备、
C5 来源过滤（**仅当调用方传 `origin` 才生效**）、C6 交易所/市场/周期三元组。

| 格 | 说明 | 被扫描到 | 登记为挂单 | 生成模拟开仓 |
|---|---|---|---|---|
| A | 全层有效（原始研究行） | 否 | 否 | 否 |
| B | 单因素移除 C1 | 否 | 否 | 否 |
| C | 单因素移除 C2 | 否 | 否 | 否 |
| D | 单因素移除 C3 | 否 | 否 | 否 |
| E | 单因素移除 C4 | 否 | 否 | 否 |
| F | 单因素移除 C6 | 否 | 否 | 否 |
| G | 单因素移除 C5（调用方不传 origin） | 否 | 否 | 否 |
| H | 仅保留 C1 | 否 | 否 | 否 |
| I | 仅保留 C2 | 否 | 否 | 否 |
| J | 仅保留 C3 | 否 | 否 | 否 |
| K | 仅保留 C4 | 否 | 否 | 否 |
| L | **全层失效（危险对照）** | **是** | **是**（`paper_pending=1`） | 否 |
| M | 仅保留 C5（调用方传 `origin=live`） | 否 | 否 | 否 |
| N | 全层失效 + 调用方传 `origin=live` | 是 | 是 | 否 |

读法（三件事必须分开说）：

1. **每一层单独都够挡**：B–F 每格只让一层变通过，其余仍不通过 ⇒ 全部不被扫描；
   H–K、M 每格只让一层不通过，其余全通过 ⇒ 全部不被扫描。**不是**「逐步累计改坏」得出的结论。
2. **全层失效才进得来**：L/N 两格六层全通过 ⇒ 被扫描到、且真的登记了挂单（`paper_pending=1`）。
   这说明装置是活的，不是空跑。
3. **只有挂单、没有持仓**：L/N 的 `paper_position=0`。旧反例（08 单）也只有
   `paper_pending=1 / paper_position=0`，本单结论一致：**只能证明「登记挂单」，不能描述成「已开仓」**。

**边界必须说清**：矩阵读的是**生产信号库的副本**（`isolated/gate/live/`），写的是
**隔离账本**（`isolated/gate/paper/`）；`PAPER_LEDGER_DIR` 指向隔离副本，不是生产账本。
沙箱闸门文件是生产闸门文件的逐字节副本（`allow_new_entries=true`），
**没有动生产闸门文件**（前后 sha256 一致）。所以本单**没有**验证生产账本交易路径端到端。

---

## 十、最小补丁方案（**只出方案，未实施**）

**结论先说**：现状**已经挡得住**（第九节 14 格），所以**不建议**为了「防研究行开仓」去改执行路径。
但现状的防护是「四个字段碰巧都不满足」的**隐式**防护——只要有人改错其中任一字段，
或未来导入器把研究行写成可执行态，防护立刻消失，且没有任何测试会报警。

**P-01（推荐，最小，不改行为语义）**
- 文件：`框架/backend/unified_runtime.py`
- 函数：`SignalPersistenceAndRouter._scan_actionable`
- 改动：在现有 6 条谓词**之后**追加一条来源白名单
  `origin IN ('live','recovered','replay')`（或等价地 `origin <> 'historical_research'`）。
- 行为影响：研究行从「被 4 条谓词分别挡住」变成「被来源显式拒绝」；对 live/recovered/replay 零影响。
- 回归证据要求：重跑 60 矩阵，L/N 应从「被扫描到」变「不被扫描到」；另补一格合法 live 行仍须被扫描到（防误杀）。
- 风险：若未来有模块用 `historical_research` 走回放路由会被一并挡住——目前全库只有导入器写、页面读，无此调用方。

**P-02（更硬，必须单独裁决）**
- 文件：`框架/backend/paper_engine.py`，函数 `open_position`
- 改动：入口读 `signal_payload`（参数已存在）里的 `approved`，`approved is False` ⇒ 直接
  `_reject(..., 'research_not_approved')`，排在配置契约校验之前。
- 影响：自动/手工/挂单三路都过此函数，一处覆盖三路。
- 代价：`route_pending_orders` 当前**不传** `signal_payload`，要同时改调用方 ⇒ 属「改变执行行为」的改动。

**本单立场**：两条都**没有实施**。工单明确要求本单不得实施对现有执行行为有影响的改动、
不关闭全局开仓闸门、不改变原策略行为。且 60 已用隔离负例证明现状可挡住，改动缺乏证据支撑。

---

## 十一、调用路径图（文本，逐跳带文件与函数）

```
链路A：研究信号 → 页面（本单新增的查看路径）
  evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/raw.json（289 候选）
    → 框架/backend/research_signal_import.py::load_run / build_rows / import_rows
        （写 live_signal，origin=historical_research，executable_state=historical_not_executable）
    → 框架/backend/live/live_signals.sqlite3（live_signal 表，289 行：4a=105 / 4b=87 / 4c=97）
    → 框架/backend/live_api.py::live_signals(scope=all&origin=historical_research)
        〔09-WORK 新增：仅研究行附 research 子对象〕
    → 框架/backend/adapter.py::_v1_signals_merged → route_v1('/api/v1/signals')
    → UI界面/web/src/api/client.ts::apiGet
    → UI界面/web/src/components/ResearchSignalSection.tsx（折叠区，只读）
    → UI界面/web/src/components/SignalListPanel.tsx（挂在列表容器之外）

链路B：研究信号 → 订单（只读追踪，本单未改）
  框架/backend/unified_runtime.py::SignalPersistenceAndRouter.route_pending_orders
    （生产调用方只传 origin=None 或 'replay'）
    → _scan_actionable（6 条谓词：executable_state / exchange+market+timeframe / role / arm /
       confirmed_at+available_at / origin）
    → 框架/backend/paper_engine.py::open_position（配置契约 → 新开仓闸门 → 行情 → 成交时钟）
    → 隔离账本 paper_pending / paper_order / paper_position
```

当前实际状态：生产库 289 行**全是** `historical_research`，`executable_state=historical_not_executable`、
`role=research_only`、`available_at=NULL`、`payload.arm ∈ {4a,4b,4c} ≠ 3a`
⇒ 链路 B 的 4 条谓词**各自独立**即可挡住（第九节矩阵已逐格证明）。

---

## 十二、口径与踩过的坑（留给复核）

1. **`total` 不是库内总数**：审计通道 `total=len(merged)`；完整性只认 `has_more`。
   实测 `limit=50` 时 `total≠289`，`limit=1000` 时 `has_more=false` 且 289 条。
2. **`paper_order` / `paper_blocked` 的键是 `event_uid`，不是 `signal_uid`**。
   第一版矩阵按 `signal_uid` 查这两张表，全部抛 `no such column` 并被记成「没有记录」——
   是「查询写错」被误读成「事实如此」。已改为两列都试并如实记录。
3. **写探针初版把总数封顶在 200**：`写语句总数 = len(样本列表)`，样本上限 200 ⇒ 超过 200 条时
   总数被截断成 200，看起来像「正好 200」。已改为独立计数器 + 独立字典（不封顶），
   样本列表才封顶，并单列「样本是否被截断」。**21/30/50/60 四个脚本已用修正后的探针全部重跑。**
4. **`live_api` 会对账本库 `ATTACH`**：ATTACH/DETACH 不是写，探针单列统计（实测 4 次，四臂）。
5. **隔离 API 服务不调 `adapter.main()`**：`main()` 里的 `_prewarm()` 与 `backtest.startup()` 是生产写。
6. **`/api/v1/signals` 有大响应字节缓存**（默认 3s + 10 倍保留期 ⇒ 最长 30s 陈旧）。
   跨进程恢复测试里必须关掉（`ADAPTER_RESP_CACHE_TTL=0`），否则会读到上一个进程留下的陈旧字节，
   把「读回失败」和「缓存未过期」混为一谈。
7. **前端 ESM 不认 `NODE_PATH`**：`page09.mjs` 用绝对路径 import playwright。
   反过来，vite 起不来那个 `MODULE_NOT_FOUND` 恰恰**需要** `NODE_PATH` 兜底（见 §五.7）。
8. **基线要选对**：矩阵最初取「uid 排序第一」的研究行当基准，那条事件在 2026-07，
   早于本轮 paper 启用下限，结果被 `before_paper_epoch_no_backfill` 终态拒单，
   把「登记为挂单」这一态整个掩盖掉。改用「确认时刻最晚」的研究行才看到 `paper_pending=1`。
9. **生产库当前没有 live 行**（289 行全是研究行），所以「当前交易区 0 条」既可能是
   「过滤对了」也可能是「本来就没有」——本单用 `per_symbol_state` / 漏斗 / 观察区 324 条
   三处交叉证明扫描链路在跑，不靠单一句子下结论。

---

## 十三、禁语（本单不得出现的说法）

- 不得说「信号页全链路通了」——生产是降级态（§五.1）。
- 不得说「自然信号闭环已打通」——自然候选 0，只交分因。
- 不得说「策略有效 / 无效」。
- 不得说「研究信号已开仓」——L/N 只有 `paper_pending=1`、`paper_position=0`。
- 不得说「生产账本交易路径端到端验证」——矩阵用的是隔离账本。
- 不得说「对任何库零写」——隔离账本上有 64 条幂等建表 DDL（§五.6）。
- 不得把漏斗的 BOX/SWEEP/RETEST 当成「事件总数」。

---

## 十四、下一步（需你拍板的两件事）

1. **要不要重载生产后端（8787）**：重载后生产页面才能显示运行/配置身份字段。
   不重载，生产页面就一直是「结构在、身份字段缺」的降级态。
   （重载会短暂中断服务，且当前有 0 持仓、0 挂单，中断成本低，但仍需你确认。）
2. **要不要做 P-01 / P-02 补丁**：本单证明现状能挡住，补丁是「把隐式防护变成显式拒绝」的加固，
   不是修 bug。要动就单独开工单，并按 §十 的回归证据要求做。

---

## 十五、复跑方式

```bash
cd evidence/ACCUM-EARLY-09-WORK
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

$PY scripts/10_现状快照.py        # 只读快照（会覆盖 out/10_*，是当前态而非开工前态）
$PY scripts/20_隔离准备.py        # 生成 isolated/ 副本
$PY scripts/21_后端字段.py        # 起停隔离后端 8899
$PY scripts/30_页面与一致性.py    # 起停隔离后端 + 隔离前端 3101 + Playwright
$PY scripts/50_跨进程恢复.py      # 四个进程接力 + 页面读回
$PY scripts/60_单因素闸门矩阵.py  # 14 格矩阵（沙箱 isolated/gate）
$PY scripts/70_自然分因.py        # 只读分因（含独立复算水位）
$PY scripts/80_变更清单.py        # 变更对照 + 探针正反例 + 生产页面实拍
```

注意：`10_现状快照.py` 复跑会把 `out/10_现状快照.json` 的「开工前指纹」覆盖成当前指纹，
**开工前基线只在第一次运行时有效**；复跑后请以 `out/80_变更清单.json` 里已固化的对照为准。
