# SIGNAL-UI-LINK-07-R1｜补齐异常空态与信号显示闭环

> 工单：`SIGNAL-UI-LINK-07-R1`
> 完成时刻（UTC）：2026-10-25T11:30
> 结论一句话：**「接口异常被显示成空列表」这条真因已经修掉并验证；两条真实候选路径按同一 ID 从引擎走到页面全部对齐；生产当前仍是 0 条信号——这是规则决定的正常空列表，不是故障，也不是本单没做完。**

---

## 一、先说要你拍板的事：没有

本单没有需要你决策的分叉。所有能自己办的都办完了。
唯一需要你知道的是两个**事实边界**，写在第四节。

---

## 二、五条要求逐条对应

### 要求 1：接口异常不能被显示成空列表

**真因在哪**：不在前端，在后端。`框架/backend/adapter.py` 的 `_v1_signals_merged` 有两条失败路径
（实时信号层 import 失败 / 读库抛异常），原来只往 `quality.note` 写一句话，
`quality.status` 仍留成 `"ok"`。前端按契约读 `status`，于是把「数据源没连上」和「读库炸了」
都当成「正常、0 条」。

**改了什么**（`adapter.py`，两处）：
- `live is None` 分支 → `status="not_connected"` + `missing_reason` 写明原因；
- 读取异常分支 → `status="error"` + `missing_reason` 同步写入。

**前端配套**（新建 + 改 4 个文件）：新增唯一的状态分类器
`UI界面/web/src/api/signalsState.ts`，把响应分成三态：

| 态 | 什么时候 | 页面显示 |
|---|---|---|
| `loading` | 首次请求还没回来 | 「正在读取实时信号…」 |
| `ok` | `status=ok`（含正常 0 条） | 空态诊断（若 0 条）/ 卡片列表 |
| `fault` | 六种：`no_watermark` / `not_connected` / `error` / `degraded` / `http` / `network` | 红框，写清是哪一种、不是什么 |

**关键纪律**：`quality.note` **恒有值**（它是一段口径说明，生产实测 172 字符）。
它单独出现**绝不**构成故障 —— 这正是「不能把所有非空说明字段一律当故障」的实现位置。
只有 `missing_reason` 非空、或 `status` 落在故障枚举里，才算故障。

**证据**：`out/20_contract_backend.json`（进程内构造四种情况，9 项全通过）、
`out/30_fault_recovery.json`（页面级，见要求 2）。

---

### 要求 2：故障恢复要验证

隔离浏览器会话跑了 8 个场景，**15 项检查全通过**（`out/30_fault_recovery.json`）：

| 场景 | 页面状态 | 卡片 | 结论 |
|---|---|---|---|
| 加载中 | `loading` | — | 显示「正在读取」，不显示「暂无信号」，也不显示错误 |
| 正常有卡 | `ok` | 1 张，ID 一致 | 基线 |
| 业务异常 200（`status=no_watermark`） | `fault:no_watermark` | 0 张 | 显示「有效下限缺失」，**不落空态诊断** |
| HTTP 503 | `fault:http`，带 503 | 0 张 | 显示「接口返回错误」，文案含真实状态码 |
| 网络不可达 | `fault:network` | 0 张 | 显示「无法连接后端」 |
| 正常空列表（`note` 非空） | `ok` | 0 张 | 落空态诊断，**不报错** |
| 恢复 | `ok` | 1 张，同一 ID | 错误框消失、列表自动重新加载 |
| 恢复后两轮轮询 | `ok` | 1 张，同一 ID | 稳定，旧卡片没有冒充当前信号 |

「恢复」是**靠页面自己的 5 秒轮询**完成的，**没有 reload** —— 这才叫真恢复。
每个故障态的标题、原因、「这不是什么」提示都是分开写的，不复用同一句话。

---

### 要求 3：两条真实候选路径，按同一 ID 核对

用现有真实历史行情（`market_v2.sqlite3`，只读），让**正式引擎**自己跑出候选。
两条路径用的是同一个候选时刻（2026-10-02T15:00:00Z），产出**同一个候选 ID**：

```
cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000
```

| | 新产生（`--mode live`） | 重启恢复（`--mode recovered`） |
|---|---|---|
| 怎么来的 | 进程 14:00 在线，`step_once(now=15:00, origin='live')` 现场处理那根 1h | 进程 15:30 才重启，bootstrap 尾部补齐 + `_route_recovered_candidates()` |
| 落库 `origin` | `live` | `recovered` |
| 币种 | VELVETUSDT | VELVETUSDT |
| 方向 | `long` | `long` |
| 确认时间 | 2026-10-02T15:00:00Z | 2026-10-02T15:00:00Z |
| 可执行状态 | `enterable` | `enterable` |
| 幂等（重复处理两轮） | 同 ID 集合，无新增 | 同 ID 集合，无新增 |
| 接口 `scope=current` | 1 条 | 1 条 |
| 页面卡片 | 1 张 | 1 张 |
| 库 / 接口 / 页面三处 | 同一 ID，四项字段逐个相同 | 同一 ID，四项字段逐个相同 |
| 刷新后 | 1 张，字段不变 | 1 张，字段不变 |
| 两轮轮询后（不 reload） | 1 张，字段不变 | 1 张，字段不变 |
| 接口重复请求 5 次 | 结果完全相同，不重复 | 结果完全相同，不重复 |

**证据**：`out/40_two_paths_live.json`、`out/40_two_paths_recovered.json`、
`out/50_two_paths_page_live.json`、`out/50_two_paths_page_recovered.json`，
截图 `out/50_page_live.png`、`out/50_page_recovered.png`。

> 这里踩过一个坑，值得记下来：上一单的 `03_iso_e2e` 是在**修复前**跑的，
> 当时 bootstrap 没有「尾部补齐」，所以候选只能由 `step_once` 产生。
> 本单 bootstrap 已经会补喂最后一根已收盘的 1h（这正是 `SIGNAL-UI-LINK-07` 修的缺陷），
> 于是同一个截止时刻下候选会被 bootstrap 提前吃掉、进 `recovered`，step 自然没有新信号。
> **这不是回归，是修复的正确后果**。所以本单把两条路径的截止时刻分开：
> live 停在 14:00 之前（模拟「那根 1h 收盘时进程在线」），recovered 停在 15:30（模拟「停机期间收盘」）。

---

### 要求 4：历史明细要能对上

生产实测（`out/60_history_reconcile.json`，12 项全通过）：

- **数量与明细一致**：计数 51 == 明细 51 == 明细计数 51，自洽标记 `True`，ID 无重复；
- **能查 ID / 时间 / 原因**：51 条每条都有 `candidate_id`、`event_id`、确认时间、
  最早可执行时间、不可执行原因；原因只落在两个已知枚举
  （`before_paper_epoch_no_backfill` 51 条、`missed_fill_moment_no_backfill` 51 条）；
  并且**原因与时间自洽**（标了「早于启用时点」的，确认时间确实早于 2026-10-14T01:00Z）；
- **不进当前可执行列表**：51 条与当前列表交集 0；生产实时库 0 行；
- **不补下过期订单**：四本账本（3a/3b/3c/3d）里**没有任何一行**引用这 51 个 ID。
  （3a 账本有 13 行 `paper_order`，但全是 BTCUSDT 的历史遗留行，与策略池无关，更与这 51 个候选无关。）

页面侧（`out/70_prod_page.json`，18 项全通过）：
- `data-signals-state=ok`、0 张卡、无错误框；
- 空态诊断 **9 行**（最近处理到 / 行情最新 / 当前阶段 / 本进程漏斗 / 历史回放 /
  最近候选 / 空转原因 / 样例 / 明细入口）；
- 「展开历史回放候选明细（51 条，均不可执行）」入口在，展开后 **51 条**，
  **页面 ID 集合与接口 ID 集合逐个相同**（差异 0 + 0）；
- 每条都写了时间与中文原因（如「早于模拟盘启用」）；
- 刷新后、两轮轮询后仍是 `ok` / 0 张 / 9 行，不闪成故障、不凭空冒卡片。

---

### 要求 5：加载并交付

**只重启了必要服务**：
- API（8787）PID `32935`，启动 2026-10-25T11:03:51Z
- Worker / Runtime 同批启动
- **采集器未动**（PID 不变，仍在实时采数）
- 账本、门禁文件、冻结规则文件 sha256 全部未变（见 `out/80_delivery_identity.json`）

**版本标识**（改了哪些文件、多少字节、sha 前 16 位）：

| 文件 | 字节 | 行 | sha256 |
|---|---|---|---|
| `UI界面/web/src/api/signalsState.ts`（新增） | 7,008 | 162 | `f2d408931c502053…` |
| `UI界面/web/src/api/useMarketData.ts` | 56,031 | 1,250 | `b1289ce6a2aa8d38…` |
| `UI界面/web/src/components/SignalListPanel.tsx` | 46,388 | 1,010 | `bf8eb3ce8df12718…` |
| `UI界面/web/src/App.tsx` | 21,206 | 487 | `07aefc6b8f82a8a7…` |
| `框架/backend/adapter.py` | 215,744 | 3,967 | `6218416fae5f6dc3…` |

**没动过**（复核用）：

| 文件 | 字节 | sha256 |
|---|---|---|
| `evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` | 13,315 | `1f1167de35412cb7…` |
| `框架/backend/live/paper_new_entry_gate.json` | 307 | `f4691db2c7f66c81…` |
| `框架/backend/live/live_signals.sqlite3` | 45,056 | `91e0979b9458338f…` |
| `数据/paper/paper_3a.sqlite3` | 163,840 | `19825112fecbdba7…` |
| `数据/paper/paper_3b.sqlite3` | 163,840 | `49d08d3923f7f3f7…` |
| `数据/paper/paper_3c.sqlite3` | 163,840 | `6c9fffd77e2da5b8…` |
| `数据/paper/paper_3d.sqlite3` | 163,840 | `b169aa30f19bfbf0…` |

**入口**：
- 生产页面：`http://127.0.0.1:3000/`
- 生产接口：`http://127.0.0.1:8787/api/v1/signals?scope=current&limit=500`
  （正常态：`quality.status=ok`、`missing_reason=null`、`quality.note` 172 字符）
- 可复用的隔离验证入口：见第六节，每条命令可原样复制执行。

---

## 三、本单修掉的缺陷（4 个）

| # | 缺陷 | 位置 | 怎么发现的 |
|---|---|---|---|
| 1 | 数据源未连接时 `status` 留成 `ok`，被前端当正常空列表 | `adapter.py` `live is None` 分支 | 读接口契约与代码路径 |
| 2 | 读库异常时同样 `status` 留成 `ok` | `adapter.py` 读取异常分支 | 同上 |
| 3 | `not_connected` 文案拼成「不可用：None」 | `adapter.py` | 契约探针打出原文 |
| 4 | 卡片没有可核对的「方向 / 确认时间」属性，验收只能靠人眼看文本 | `SignalListPanel.tsx` | 写页面验收脚本时发现读不到 |

另有一处**不是缺陷、但容易误判**：接口返回的 `confirmed_at` 是 ISO 字符串
（`2026-10-02T15:00:00Z`），数据库存的是毫秒（`1790953200000`）。
验收脚本一开始直接 `Number()` 比，得到 `NaN`，报了三处假失败。
**同一时刻的两种表示，不是数据不一致** —— 脚本已改成按时刻比较。

---

## 四、两个必须知道的事实边界

### 1. 生产当前 0 条信号，是规则决定的，不是缺陷

- 最近一个自然候选：**2026-10-11T20:00:00Z**
- 生产启用时点（paper epoch）：**2026-10-14T01:00:00Z**
- 也就是说，最近那个候选比启用时点还早 2.2 天，按纪律**不许补开**；
- 此后至今 **13.62 天零候选**，同期行情 4h 42,768 根 / 528 标的**无缺口**
  ⇒ 是规则没触发，不是数据断了。

结构上候选本来就稀疏（79.5 天 51 个，摊在 48 个标的上）。攒样本要数月到数年，
加标的已经走完（526 是全部合规合约），只剩加时间。
**改阈值 = 改冻结规则，必须走裁决**，本单不碰。

### 2. 隔离验收用的是「模拟启用时点」，不是生产启用时点

生产的 `live_store.get_paper_epoch_ms()` 是 **fail-closed**（`max(库里值, 冻结常量)`，只允许不降）。
这是对的，不能为了测试削弱它。所以隔离验收只在**验收进程的内存里**把
`live_store.PAPER_EPOCH_MS` 改成 2026-10-01T16:00Z，磁盘上一个字节没动
（`live_store.py` sha 前后一致，见 `out/50_two_paths_page_*.json`）。

---

## 五、不构成「通过」的事（说清楚，别被误读）

- 生产「**有自然候选时能显示**」这一条，**本单仍是未验证**（生产没有自然候选）。
  已证的是：隔离环境里，同一候选 ID 从引擎 → 库 → 接口 → 页面三处一致，且刷新/轮询不丢不重。
- 生产「**没有信号时能解释清楚**」这一条，**通过**（9 行诊断 + 51 条明细可查）。
- 本单**没有**验证「真实挂钟自动成交」（那需要等自然候选，不是本单范围）。
- 回归测试：与本单改动相关的 48 个用例 **全部通过**；全量结果见第七节。

---

## 六、复验方法（每条命令可原样复制）

工作目录统一：
```bash
cd /Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-UI-LINK-07-R1/probes
```

| 命令 | 证明什么 | 产出 | 耗时 |
|---|---|---|---|
| `python 20_contract_backend.py` | 接口契约四种情况（正常 / 未连接 / 读库异常 / 下限缺失） | `out/20_contract_backend.json` | 秒级 |
| `python 40_two_paths.py --mode live` | 新产生候选，正式引擎现场跑出并落库 | `out/40_two_paths_live.json` | ~100s |
| `python 40_two_paths.py --mode recovered` | 重启恢复候选，bootstrap 补齐后落库 | `out/40_two_paths_recovered.json` | ~100s |
| `node 30_fault_recovery.mjs` | 五态 + 恢复（加载中/空/业务异常/HTTP/网络/恢复） | `out/30_fault_recovery.json` | ~50s |
| `node 50_two_paths_page.mjs --mode live` | 新产生候选的页面级验收 | `out/50_two_paths_page_live.json` | ~20s |
| `node 50_two_paths_page.mjs --mode recovered` | 重启恢复候选的页面级验收 | `out/50_two_paths_page_recovered.json` | ~20s |
| `python 60_history_reconcile.py` | 51 条明细：数量/字段/不进当前列表/账本无补单 | `out/60_history_reconcile.json` | 秒级 |
| `node 70_prod_page.mjs` | 生产页面：0 卡 + 9 行诊断 + 51 条明细 | `out/70_prod_page.json` | ~25s |
| `python 80_delivery_identity.py` | 版本标识 + 未动文件 sha + 服务身份 | `out/80_delivery_identity.json` | 秒级 |

解释器（本机固定路径）：
```
python → /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
node   → /Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node
```

前置条件：`3000` 前端与 `8787` 后端在跑（页面级脚本需要）。
`40_*` 与 `60_*` 不需要前端。

**这些脚本都不写生产库**：
- 行情库只读（`market_conn` 全 SELECT，会写 candles 的 `sync_dataset_bars_upto` 在进程内替换成 no-op）；
- 隔离库、账本、锁目录全部落在本单 `evidence/` 下；
- `50_*` / `70_*` 里对生产实时库与账本做 sha256 前后对比，不一致会直接判失败。

> 关于行情库 sha：`数据/市场数据/db/market_v2.sqlite3` 的 sha **会变**，
> 因为常驻采集器每秒都在写当前时段的 K 线。拿它当「探针泄漏」的判据会得出错误结论。
> 正确的判据是 `live_signals.sqlite3` 与四本账本的 sha —— 这两个必须零变化，实测零变化。

---

## 七、回归测试

- 与本单改动直接相关的 4 个测试文件、**48 个用例：全部通过**（2.77 秒）
  （`test_paper_live_01` / `test_live_pool_admission` / `test_web_readonly` / `test_metrics_contract`）
- 全量后端测试：见 `out/90_regression.txt`

---

## 八、证据文件清单

```
evidence/SIGNAL-UI-LINK-07-R1/
├── README.md                       ← 本文件
├── probes/                         ← 9 个可复跑探针
│   ├── 15_iso_api_launcher.py      隔离 API 启动器（只改进程内存的启用时点）
│   ├── 20_contract_backend.py      接口契约四种情况
│   ├── 30_fault_recovery.mjs       五态 + 恢复（页面级）
│   ├── 40_two_paths.py             两条真实候选路径（引擎级）
│   ├── 50_two_paths_page.mjs       两条路径的页面级验收
│   ├── 60_history_reconcile.py     历史明细核对 + 三方对账
│   ├── 70_prod_page.mjs            生产页面核对
│   └── 80_delivery_identity.py     版本标识与未动文件 sha
├── out/                            ← 探针产物（JSON + 截图）
├── iso_live/                       ← 新产生候选的隔离库 + 账本 + 锁
└── iso_recovered/                  ← 重启恢复候选的隔离库 + 账本 + 锁
```

---

## 九、一句话给下一次接手的人

信号链在隔离环境里是通的（同一 ID 三处一致、刷新轮询不丢不重、故障六态分得开），
生产没有自然候选是规则决定的正常空列表、并且页面能把这件解释清楚。
**要等到生产真的出现自然候选，才能说「生产信号页在有信号时能显示」通过** ——
在那之前，这句话只能是「未验证」。
