# SIGNAL-UI-LINK-07｜交易信号生成、入库与页面显示闭环

> 本目录只放**本单**证据。上游证据一律只读引用，不搬、不改、不覆盖。
> 所有数字都能用 `probes/` 里的脚本**当场复算**；复算路径写在每个数字后面。

---

## 0. 一句话结论（三态，不合并）

| 对象 | 判定 | 依据 |
|---|---|---|
| 信号链「候选 → 入库 → 接口 → 页面」 | **通过**（隔离环境） | 同一条真实候选 ID 在页面卡片、接口返回、数据库行三处同时出现，刷新不重复（`out/09_iso_page_e2e.json`） |
| 生产「有自然候选时页面能显示」 | **未验证** | 生产 13.6 天零自然候选，没有样本可显示（`out/10_prod_reconcile.json`） |
| 生产「没有信号时页面解释清楚」 | **通过** | 页面 8 行诊断 + 51 条历史候选明细，逐项与接口一致（`out/08_browser_page.json`） |
| 真实挂钟自动成交 | **仍未验证** | 沿用上一单结论，本单未涉及 |

**不能说的话**：不能说「信号页已经通了」——生产仍然一张卡都没有；也不能说「信号页坏了」——隔离环境里它确实能把候选一路显示出来。准确说法是：**链是通的，样本是零的**。

---

## 1. 逐环定位（要求 1）

五个环节，每环都给出「进了没有 / 为什么没进 / 在哪一行代码」。

| 环节 | 本次实际结果 | 未进入下一环的原因 |
|---|---|---|
| ① 候选产生 | 预热回放产生 **51** 个候选；本进程启动后产生 **0** 个 | 本进程刚启动，还没走到下一个 4h 收盘（`since_start` 漏斗全 0，与库里 0 行自洽） |
| ② 生成时刻 | 51 个快照时间：`2026-07-24T08:00Z` ~ `2026-10-11T20:00Z` | 全部早于启用时点 `2026-10-14T01:00Z`（冻结常量 `PAPER_EPOCH_MS`）⇒ 按纪律不补开 |
| ③ 持久化 | `live_signal` 表 **0 行** | 上面 51 个都在启用时点之前，写库会被 `before_paper_epoch_no_backfill` 判成永不可成交 ⇒ 不落库，只记明细 |
| ④ 接口 | `scope=current` **0 条**、`scope=all` **0 条** | 库里 0 行，接口不凭空造；`excluded_breakdown` 六项全 0 说明**不是被过滤掉的** |
| ⑤ 页面 | **0 张卡**，落空态 | 接口 0 条 ⇒ 空态（这是对的），但旧版空态只有一句「暂无实时信号」，等于不解释 |

**核对过的过滤项**（逐项确认没有「字段不匹配 / 静默丢弃 / 错误过滤」）：

- `origin`：原写死 `origin='live'`，会把重启后恢复的 `recovered` 真实候选整条丢掉 —— **已修**（见缺陷 A）；
- `executable_state`：`enterable` 才进，`display_only` / 非本臂结构一律不进（符合契约）；
- `payload.role`：必须 `enterable_arm_entry`，结构证据提示（`enterable_structure`）不算 —— 有效；
- 有效期 / 下限：`max(live_start, paper epoch)`，按真实区间右边界 `+1ms` 比较 —— 有效（`signal_bar_right_ms`）；
- 分页：过滤发生在 `LIMIT` 与 `total/has_more` **之前** —— 有效；
- 跨库终态拒单：`NOT EXISTS` 排除 `missed_fill_moment_no_backfill` / `before_paper_epoch_no_backfill` —— 有效。

---

## 2. 修了什么（6 处，全部有前后对照）

### 缺陷 A：`origin='recovered'` 被当前区排除（后端 + 前端各一处）
进程短暂停机后重启，`bootstrap()` 会把「仍在成交时效内」的真实候选以 `origin='recovered'` 落库并交给路由。实测 `persisted=1 / registered=1`，但 `scope=current` 只认 `live` ⇒ **库里有行、账本里有挂单、信号区永远看不到**。
- 后端：`live_api.py` 白名单改 `origin IN ('live','recovered')`；
- 前端：`adapters.ts` 白名单加 `recovered`、`types/crypto.ts` 扩联合类型、`SignalListPanel.isCurrentSignal` 同步。
- 证据：`out/04_recovered_origin.json`（修复前 total=0）→ `out/09_iso_page_e2e_recovered.json`（修复后页面卡片 `data-origin=recovered`）。

### 缺陷 B：`bootstrap()` 漏掉最后一根 4h 之后**已收盘**的 1h
`bootstrap` 只按 4h 收盘驱动 `process_market`，最后一根 4h 之后收盘的 1h **从不喂入**。后果不是「少一次状态迁移」而是**永久漏候选**：那根 1h 确认的突破既不会被 bootstrap 产生（进不了 recovered），又会被 `step_once` 的 `available_at >= started_at` 过滤掉。
- 实测：同一份数据 `upto=15:30` 时 `recovered=0`，`upto=16:00` 时 `recovered=1`（`out/04b_recovered_4h_boundary.json`）；
- 修法：补一次 `process_market(upto_ms)`（`unified_runtime.py`），候选去向仍由既有「成交时效」边界与 `FILL_POLICY` 决定 —— 不新增门槛、不放宽 epoch、不补开过期订单；
- 修复后 `upto=15:30` 即得 `recovered=1, scope=current total=1`（`out/06_recovered_after_fix_1530.json`）。

### 缺陷 C：51 个历史候选「有数字、查不到」
原来只有一个 `discarded += 1` 计数器 ⇒ 守护卡显示「丢弃 51 个」，但一个 ID 都查不到，「51」变成无法核对的口号。
- 修法：逐条留身份与不可执行原因（ID / 事件 ID / 币种 / 快照时刻 / 可用时刻 / 最早可成交时刻 / 距今多久 / 拒单原因 / 箱体高低 / ATR / ER / 突破收盘价 / 量比 / OI 增速 / 初始止损），`discarded_detail_n` 与 `discarded_detail_consistent` 自校验；
- 证据：`discarded=51 / detail_n=51 / consistent=True`，页面可展开 51 条并逐条核对（`out/08_browser_page.json`）。

### 缺陷 D：空表把「没有数据」显示成具体时刻
`scope=current` 空表时 `as_of` 曾是 `1970-01-01T00:00:00Z`；改完 `_iso(None)` 之后，合并层又把 `None` 回落成旧适配层的「现在」⇒ 「没有数据」与「数据刚好到现在」不可区分。
- 修法：`live_api.py` 空表返回 `null`；`adapter.py` 合并层在当前区**无条件采信**实时层的 `as_of`（含 `None`）；
- 证据：`as_of=None`（`out/08_browser_page.json` 网络记录、`out/10_prod_reconcile.json`）。

### 缺陷 E：空态只有一句「暂无实时信号」，且把「加载中」诬告成「故障」
三种完全不同的情况（正常等待 / 候选过期不可执行 / 接口失败）显示成同一句话，等于把信息删掉；更糟的是首次加载要排队十余秒，那段时间写的是「守护状态接口未返回」——**接口明明返回了，只是还没轮到**。
- 修法：`SignalListPanel` 新增 `EmptyStateWithDiagnostics`，渲染 8 行（最近处理到 / 行情最新 / 当前阶段 / 本进程漏斗 / 历史回放 / 最近候选 / 空转原因 / 样例）+ 可展开的 51 条历史候选明细；新增 `diagnosticsPending` 区分「正在读取」与「取不到」；接口失败仍走独立红框分支（`signalsError`），三者不再混为一谈；
- 证据：`out/08_browser_page.json`、`out/08_page_empty_diagnostics.png`。

### 缺陷 F：`bootstrap()` 热库分支引用未定义变量，直接抛异常
回归测试暴露：`hist` 为空（只读热库、无冷层历史）时走 `else` 分支，但下面「42 根上下文」用的是 `b4_all`，该名字只在 `if hist` 分支里赋值 ⇒ `UnboundLocalError`，`bootstrap()` 整个中断，**一个候选也产生不了**。
- 修法：`else` 分支补 `b4_all = b4`（热库分支下「窗口内 4h」就是「全部 4h」）；
- 证据：`tests/test_paper_live_01.py` 两个用例由 FAILED 转 PASSED；生产侧修复前后状态分布逐项相同（`IDLE 191 / BOX 300 / SWEEP 21 / RETEST 14`），说明生产走的是 `hist` 分支、结果未被改变。

---

## 3. 「有效新候选能显示」的完整路径验证（要求 2）

生产没有自然候选（见第 5 节），所以这条只能在**隔离环境**里证，且必须证明生产没被碰。

**隔离三件套**

| 层 | 做法 | 生产是否被改 |
|---|---|---|
| 数据 | `iso_e2e/live_signals.sqlite3`：由 `probes/30_iso_e2e.py` 用**真实历史行情**按时间推进、由**实际策略引擎** `step_once(now=2026-10-02T15:00:00Z)` 产生，走**正式持久化** | 否（独立文件） |
| 后端 | `probes/45_iso_api_launcher.py` 起在 **8799**，`LIVE_DB` 指向上面那份库；`PAPER_EPOCH_MS` 只在**该进程内存**改成模拟启用时点 `2026-10-01T16:00Z` | 否（`live_store.py` sha 前后一致） |
| 前端 | 用**生产正在跑的同一份页面**（3000），只在浏览器网络层把 `/api/v1/signals*`、`/api/v1/paper/*` 的出口改指向 8799 | 否（页面代码一行未改） |

**时钟自洽**：隔离时钟 `2026-10-02T15:00Z`（候选确认时刻）> 模拟启用时点 `2026-10-01T16:00Z`（早 23 小时）> 状态锚点 `2026-06-27T06:45Z`。候选有效期 `execution_earliest=2026-10-02T15:15Z`，落在 1 小时成交时效内。

**同一条候选 ID 的三方对账**

```
cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000
  页面卡片 data-signal-uid   ✓（data-origin=live, data-executable-state=enterable）
  接口 scope=current          ✓（total=1）
  数据库 live_signal          ✓（1 行，origin=live）
```

- 刷新一次页面：仍 1 张卡、库里仍 1 行（重复处理不产生重复记录）；
- `step_once` 再跑一次同一时刻：`new_signals=0`（幂等，见 `out/03_iso_e2e.json`）；
- 同一套流程对 `origin=recovered` 再跑一遍，页面同样出卡（`out/09_iso_page_e2e_recovered.json`）；
- 生产实时库 sha 前后一致：`91e0979b9458…` → `91e0979b9458…`。

---

## 4. 历史候选：可追溯，但不冒充当前（要求 3）

- 51 条**全部**能在页面展开、逐条看到 ID / 币种 / 时刻 / 不可执行原因；
- 51 条**全部**早于启用时点（复算：晚于启用时点的有 **0** 条）；
- 51 条拒单原因全部落在终态集合 `{before_paper_epoch_no_backfill, missed_fill_moment_no_backfill}` 内（异常 **0** 条）；
- ID 唯一（51/51）；
- 条数与漏斗 `history.CANDIDATE` **精确相等**（51 = 51），不是两个各说各话的数字；
- 它们**不进**当前可执行列表（`scope=current` 0 条），**不补下**过期订单（`paper_3a` 挂单仍 3 条，未新增）。

---

## 5. 生产为什么现在没有信号（要求 4）

三层，缺一层都解释不清：

**第一层：引擎在跑，不是没跑。**
526 个标的全部就位（`universe=526`），当前状态分布：箱体观察 300 / 空转 191 / 已扫低 21 / 待突破 14。空转的 191 个里：冷却中（距上次事件不足 24h）141、箱体条件未满足（宽度或 ER 超阈）31、条件已满足等下一根 4h 收盘建档 19。

**第二层：历史上有候选，但全部早于启用时点。**
51 个候选横跨 `2026-07-24T08:00Z` ~ `2026-10-11T20:00Z`（涉及 48 个币），全部早于 `2026-10-14T01:00Z`。按纪律不补开、不落库 —— 这是**规则决定的**，不是缺陷。

**第三层：最近 13.62 天零候选，而且这 13.62 天的数据是齐的。**
最近一个候选是 `2026-10-11T20:00Z`（METISUSDT）。此后到 `2026-10-25T10:46Z` 没有任何候选。
同期行情库：`binance` 4h 已收盘 bar **42,768 根 / 528 个标的 / 每个 81 根**（期望 82，差的是尚未收盘的那一根），1h 172,128 根 —— **没有数据缺口**。
⇒ 所以不是「没数据」，是**规则在这 13.6 天确实没触发**。此前 79.5 天里一共只有 51 个候选、摊在 48 个标的上，说明候选本来就是稀疏事件（摊到单个标的约 80 天一个）；13.6 天零候选属明显偏冷区间，但这是**观察**，不是结论 —— 样本量不足以推断规则失效。

**页面现在长这样**（`out/08_page_empty_diagnostics.png`）：

```
当前没有可执行信号
  最近处理到   1h 10-25 09:00 UTC · 4h 10-25 04:00 UTC
  行情最新     1h 10-25 09:59 UTC · 4h 10-25 07:59 UTC
  当前阶段     箱体观察 300 · 空转 191 · 已扫低 21 · 待突破 14
  本进程漏斗   箱体 0 → 扫低 0 → 回踩 0 → 候选 0
  历史回放     箱体 7932 → 扫低 1923 → 回踩 1165 → 候选 51（丢弃 51 · 恢复 0）
  最近候选     本次启动后没有产生候选
  空转原因     冷却中 141；箱体条件未满足 31；条件已满足等下一根 4h 19
  样例         0GUSDT：冷却中（距上次事件不足 24h）（实际 2026-10-25T14:00Z / 阈值 冷却 24h）
  ▸ 展开历史回放候选明细（51 条，均不可执行）
```

---

## 6. 页面 / 接口 / 数据库三方核验（要求 5）

| 核验方式 | 结果 |
|---|---|
| 真实浏览器（Playwright + `chromium_headless_shell-1248`） | 15 项全通过：空态 8 行齐全、旧文案消失、51 条明细可展开、页面明细与接口**逐位相同**、无 JS 报错 |
| 实际接口 | `scope=current` total=0 / `as_of=null` / `excluded_breakdown` 六项全 0；`paper/status` 的 `discarded=51 / detail_n=51 / consistent=True` |
| 数据库只读 | `live_signal` 0 行；`live_run` 三项与重启前一致（`paper_epoch_ms=1791939600000`、`live_start_ms=1792748844240`、`state_anchor_ms=1782542715686`）；四臂账本 `paper_3a` 持仓 3 / 订单 13 / 成交 6，`3b/3c/3d` 全 0（与重启前指纹一致） |
| 三方按候选 ID 对账 | 生产：库 0 行 / 接口 0 条 / 页面 0 张，一致；隔离：库 1 行 / 接口 1 条 / 页面 1 张，同一 ID |

**没有用「测试数量」或「运行健康」代替闭环证据**：上面的三方对账全部按**候选 ID** 对齐，不按条数、不按接口 200、不按截图。

---

## 7. 回归测试

| 范围 | 结果 |
|---|---|
| 信号链相关（`test_paper_live_01` / `test_web_readonly` / `test_lifecycle_isolation`） | **27 / 27 通过** |
| 全量 | **15 失败 / 364 通过 / 79 跳过**（基线 13 失败 / 366 通过 / 79 跳过） |

多出的 2 个失败全部落在 `test_system_isolation_03/04/05` 与 `test_system_startup_01`（系统启停 / 隔离类用例），失败原因明确是 `worker_already_running` 与端口占用 —— 因为本单要求**保留生产链运行**，这些用例要独占 jobs 队列与端口。与信号链模块无关（`364 + 2 = 366`，与基线通过数吻合）。

---

## 8. 复算方式（一条命令一个数字）

```bash
cd evidence/SIGNAL-UI-LINK-07/probes
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node

$PY 10_baseline_snapshot.py        # 四环基线只读快照 → out/00_baseline.json
$PY 20_iso_replay.py               # 隔离重放，候选全集逐条判因 → out/02_iso_replay.json
$PY 30_iso_e2e.py                  # 隔离端到端（生成→落库→API），双口径 → out/03_iso_e2e.json
$PY 35_recovered_origin.py --upto 2026-10-02T15:30:00Z   # 缺陷 B 前后对照 → out/06_…
$NODE 40_browser_page_check.mjs    # 真实浏览器核验空态 + 51 条明细 → out/08_browser_page.json
$NODE 50_iso_page_e2e.mjs          # 隔离页面级验收（live）→ out/09_iso_page_e2e.json
$NODE 50_iso_page_e2e.mjs --iso iso_recovered --expect-origin recovered --out-suffix recovered
$PY 60_prod_reconcile.py           # 生产三方对账 + 51 条复算 → out/10_prod_reconcile.json
```

---

## 9. 边界（明确不宣称的）

- 不宣称「生产信号页已有信号」：生产 `live_signal` 0 行，页面 0 张卡；
- 不宣称「真实挂钟自动成交已验证」：沿用上一单结论，仍未验证；
- 不宣称「13.6 天零候选意味着规则失效」：数据齐、引擎在跑，只能记「观察」；
- 不宣称「回归全绿」：全量 15 失败，其中 2 个是本单运行方式引入的环境性失败；
- 不调参凑信号、不删除旧证据、不扩大范围：本单只改信号链的 5 个文件 + 1 处 bootstrap 崩溃点。

---

## 10. 文件清单

```
evidence/SIGNAL-UI-LINK-07/
├── README.md                         本文件
├── probes/
│   ├── 10_baseline_snapshot.py       四环基线只读快照
│   ├── 20_iso_replay.py              隔离重放：候选全集 + 逐条判因
│   ├── 30_iso_e2e.py                 隔离端到端：生成→落库→接口（同 ID）
│   ├── 35_recovered_origin.py        origin=recovered 可见性（含 --upto 对照）
│   ├── 40_browser_page_check.mjs     真实浏览器：空态诊断 + 51 条明细
│   ├── 45_iso_api_launcher.py        隔离 API 启动器（启用时点仅进程内存改）
│   ├── 50_iso_page_e2e.mjs           隔离页面级验收（live / recovered 两跑）
│   └── 60_prod_reconcile.py          生产三方对账 + 历史候选复算
├── out/
│   ├── 00_baseline.json              修复前四环基线
│   ├── 01_funnel_fullhistory.json    全历史漏斗（重放口径）
│   ├── 02_iso_replay.json            隔离重放候选全集
│   ├── 03_iso_e2e.json               隔离端到端（同 ID + 幂等）
│   ├── 04_recovered_origin.json      缺陷 A：修复前 recovered 不可见
│   ├── 04b_recovered_4h_boundary.json 缺陷 B：upto 边界对照
│   ├── 05_iso_replay_after_fix.json  修复后重放
│   ├── 06_recovered_after_fix_1530.json 缺陷 B 修复后
│   ├── 07_pre_restart_fingerprint.json 重启前库指纹
│   ├── 08_browser_page.json          浏览器核验（15 项）
│   ├── 08_page_empty_diagnostics.png 空态 + 51 条明细截图
│   ├── 09_iso_page_e2e.json          隔离页面验收（live，17 项）
│   ├── 09_iso_page_e2e_recovered.json 隔离页面验收（recovered，17 项）
│   ├── 09_iso_page_candidate.png     候选卡片截图（live）
│   ├── 09_iso_page_candidate_recovered.png 候选卡片截图（recovered）
│   └── 10_prod_reconcile.json        生产三方对账（17 项）
├── iso/ · iso_e2e/ · iso_recovered/  隔离实时库 + 隔离账本（可丢弃，不是证据主体）
└── logs/
```

**改动文件**：`框架/backend/live_api.py`、`框架/backend/unified_runtime.py`、`框架/backend/adapter.py`、
`UI界面/web/src/api/adapters.ts`、`UI界面/web/src/types/crypto.ts`、`UI界面/web/src/api/liveTrading.ts`、
`UI界面/web/src/components/SignalListPanel.tsx`、`UI界面/web/src/App.tsx`。
**未改**：`live_store.py`（启用时点常量与 fail-closed 逻辑原样）、门禁文件、冻结规则 md、任何账本数据。
